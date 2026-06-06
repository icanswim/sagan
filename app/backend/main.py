import os, uuid, traceback, sqlite3
import asyncio, threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.concurrency import run_in_threadpool

from pydantic import BaseModel, Field

from kubernetes import client, config

from torch import long

from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT
from cosmosis.dataset import AsTensor

DB_PATH = "/app/data/job_history.db"
NAMESPACE = "sagan-app"
JOB_SELECTOR = "job-group=sagan-train"
BACKEND_SELECTOR = "app=sagan-backend"


logger = Metric.setup_logging(log_name='backend.main')

def load_k8s_config():
    # local and remote config loading
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

load_k8s_config()

batch_v1 = client.BatchV1Api()
core_v1 = client.CoreV1Api()

class FrontendCache:
    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()

    def set(self, key_or_dict, value=None):
        if value is None:
            value = []
            
        with self.lock:
            if isinstance(key_or_dict, dict):
                self.cache.update(key_or_dict)
            else:
                self.cache[key_or_dict] = value

    def get(self, key, default=None):
        if default is None:
            default = []
            
        with self.lock:
            return self.cache.get(key, default)


class TextData(BaseModel):
    content: str


class SimpleTrainConfig(BaseModel):
    batch_size: int = Field(default=64, ge=1, le=168, description="1 <= bs <= 168")
    epoch: int = Field(default=1, ge=1, le=10, description="1 <= epoch <= 10")
    n: int = Field(default=2000, ge=1000, le=300000, description="1000 <= n <= 300k")


class DatabaseManager:

    ALLOWED_COLUMNS = {
        "batch_size": "INTEGER",
        "epoch": "INTEGER",
        "n": "INTEGER",
        "status": "TEXT",
        "test_loss": "REAL",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "finished_at": "TIMESTAMP"
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self, parse_types: bool = True) -> sqlite3.Connection:
        # parse_types true for python datetime objects, false for raw strings
        detect = sqlite3.PARSE_DECLTYPES if parse_types else 0
        conn = sqlite3.connect(self.db_path, detect_types=detect)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        dynamic_schema = ", ".join([f"{col} {datatype}" for col, datatype in self.ALLOWED_COLUMNS.items()])
        
        with self._get_connection() as conn:
            query = f"""
                CREATE TABLE IF NOT EXISTS job_history (
                    job_name TEXT PRIMARY KEY,
                    {dynamic_schema}
                )
            """
            conn.execute(query)
            conn.commit()

    def update(self, job_name: str, metric_update: dict) -> None:
        filtered_updates = {
            k: v for k, v in metric_update.items() 
            if k in self.ALLOWED_COLUMNS and k != "created_at"
        }
        
        if not filtered_updates and job_name:
            return

        columns = ["job_name"] + list(filtered_updates.keys())
        placeholders = ", ".join(["?"] * len(columns))
        
        set_clause = ", ".join([f"{col} = excluded.{col}" for col in filtered_updates.keys()])
        query_values = (job_name,) + tuple(filtered_updates.values())

        with self._get_connection() as conn:
            query = f"""
                INSERT INTO job_history ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(job_name) DO UPDATE SET
                {set_clause}
            """
            conn.execute(query, query_values)
            conn.commit()

    def get_job_history(self, limit: int = None) -> list[dict]:
        with self._get_connection(parse_types=False) as conn:
            conn.row_factory = sqlite3.Row
            
            query = """
                SELECT *, 
                CASE 
                    WHEN finished_at IS NOT NULL THEN 
                        printf('%02d:%02d:%02d', 
                            CAST((julianday(finished_at) - julianday(created_at)) * 24 AS INT),
                            CAST(((julianday(finished_at) - julianday(created_at)) * 1440) % 60 AS INT),
                            CAST(((julianday(finished_at) - julianday(created_at)) * 86400) % 60 AS INT)
                        )
                    WHEN status = 'running' THEN 
                        printf('%02d:%02d:%02d', 
                            CAST((julianday('now') - julianday(created_at)) * 24 AS INT),
                            CAST(((julianday('now') - julianday(created_at)) * 1440) % 60 AS INT),
                            CAST(((julianday('now') - julianday(created_at)) * 86400) % 60 AS INT)
                        )
                    ELSE '00:00:00'
                END as training_time
                FROM job_history 
                ORDER BY created_at DESC
            """
            
            params = []
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
                
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        
    def get_running_jobs(self) -> list[str]:
        with self._get_connection(parse_types=False) as conn:
            conn.row_factory = sqlite3.Row
            return [row['job_name'] for row in conn.execute(
                "SELECT job_name FROM job_history WHERE status = 'running'"
            ).fetchall()]
        
    def rectify(self, zombies: list[str]) -> None:
        if not zombies:
            return

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        update_data = [(timestamp, name) for name in zombies]
        
        for name in zombies:
            logger.warning(f"silent failure detected: {name}")
            
        with self._get_connection(parse_types=False) as conn:
            conn.executemany("""
                UPDATE job_history 
                SET status = 'silent failure', finished_at = ? 
                WHERE job_name = ?
            """, update_data)
            conn.commit()


def get_current_image():
    env_image = os.getenv("SKAFFOLD_IMAGE_SAGAN_BACKEND")
    if env_image and env_image != "sagan-backend":
        return env_image

    pod_name = os.getenv("HOSTNAME")
    if not pod_name:
        return "sagan-backend"
        
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
        return pod.spec.containers[0].image
    except ApiException as e:
        logger.error(f"failed to read current pod spec: {e}")
        return "sagan-backend"
 
def get_jobs():
    jobs = batch_v1.list_namespaced_job(namespace=NAMESPACE, label_selector=JOB_SELECTOR)
    job_items = getattr(jobs, 'items', []) or []

    if not job_items:
        return {"status": "no jobs found", "color": "gray"}

    sorted_jobs = sorted(job_items, key=lambda x: getattr(x.metadata, 'creation_timestamp', None) or "")
    return sorted_jobs 

def get_pods():
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=JOB_SELECTOR)
    pod_items = getattr(pods, 'items', []) or []
    
    sorted_pods = sorted(pod_items, key=lambda x: getattr(x.metadata, 'creation_timestamp', None) or "")
    return sorted_pods

def get_containers(pod):
    spec = getattr(pod, 'spec', None)
    if not spec:
        logger.warning(f"main.get_containers: received pod object without a valid spec schema: {type(pod)}")
        return []
        
    init = getattr(spec, 'init_containers', []) or []
    app = getattr(spec, 'containers', []) or []
    return [c.name for c in init + app]

def get_container_log(pod_name, container_name):
    try:
        container_log = core_v1.read_namespaced_pod_log(
            name=pod_name, 
            namespace=NAMESPACE, 
            container=container_name,
            tail_lines=100,
            _request_timeout=4,
        )
        return container_log
    except ApiException as e:
        logger.warning(f"could not read logs for pod {pod_name}/{container_name}: {e.reason}")
        return f"unable to fetch logs from kubernetes api: {e.reason}"
    except Exception as e:
        logger.exception(f"unexpected system error reading logs for {pod_name}")
        return f"system error loading log history: {e}"

def loop():

    out = {
        "status": "",
        "color": "green" 
    }
    
    sorted_pods = get_pods()
    if not sorted_pods:
        out['status'] = "no training pods found..."
        out['color'] = "yellow"
        return out
    
    for pod in sorted_pods:
        container_names = get_containers(pod)
        metadata = getattr(pod, 'metadata', None)
        if not metadata:
            continue
            
        pod_name = getattr(metadata, 'name', "unknown")
        pod_suffix = f" ({pod_name})" if len(sorted_pods) > 1 else ""
        
        status_obj = getattr(pod, 'status', None)
        container_statuses = getattr(status_obj, 'container_statuses', []) or []
        
        for container_name in container_names:
            if container_name in ["sagan-backend", "train-job"]: 
                container_log = get_container_log(pod_name, container_name)
                out[f'{container_name}-log{pod_suffix}'] = f"--- pod: {pod_name}, container: {container_name} ---\n{container_log}\n"
            
            if container_name == "train-job":
                log_key = f'train-job-log{pod_suffix}'
                if log_key not in out:
                    out[log_key] = ""

                if not container_statuses:
                    out[log_key] += "no container statuses found for this pod.\n"
                else:
                    for cs in container_statuses:
                        if cs.name == container_name:
                            state = cs.state
                            if not state:
                                continue
                                
                            if state.waiting:
                                out['status'] += f"Status{pod_suffix}: Waiting | Reason: {state.waiting.reason}\n"
                                if state.waiting.reason in ["ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"]:
                                    out['status'] += f"⚠️ Container is STUCK during setup: {state.waiting.reason}\n"
                                    out['color'] = "red" 
                                elif out['color'] not in ["red", "blue"]:
                                    out['color'] = "yellow"
                            
                            elif state.running:
                                out['status'] += f"Status{pod_suffix}: Running 🏃\n"
                                if out['color'] != "red":
                                    out['color'] = "blue"
                            
                            elif state.terminated:
                                out['status'] += f"Status{pod_suffix}: Terminated | Exit Code: {state.terminated.exit_code} | Reason: {state.terminated.reason}\n"
                                if state.terminated.reason == "OOMKilled":
                                    out['status'] += "🚫 Container ran out of memory (OOMKilled).\n"
                                    out['color'] = "red"
                                elif state.terminated.exit_code == 0:
                                    out['status'] += "✅ Container completed successfully.\n"
                                    # Don't downgrade from red or blue
                                    if out['color'] not in ["red", "blue"]:
                                        out['color'] = "green"
                                else:
                                    out['status'] += "❌ Container failed with non-zero exit code.\n"
                                    out['color'] = "red"

        # check pod-level eviction status
        if status_obj and getattr(status_obj, 'phase', None) == "Failed" and getattr(status_obj, 'reason', None) == "Evicted":
            if "train-job" in container_names:
                out['status'] += f"📉 Pod Evicted ({pod_name}): Node ran out of disk or resource capacity.\n"
                out['color'] = "red"

    if not out['status'].strip():
        out['status'] = "no training pods found..."
        out['color'] = "yellow"

    return out


async def monitor_loop(frontend_cache: FrontendCache, db_manager: DatabaseManager):
    """
    monitors kubernetes for job updates and caches results for frontend retrieval.
    """
    while True:
        try:
            output = await run_in_threadpool(loop)
            
            if output['status'] == "no training pods found...":
                db_manager.rectify(db_manager.get_running_jobs())
                output['history'] = db_manager.get_job_history(limit=10)
                
            frontend_cache.set(output)
            interval = 30 if output['status'] == "no training pods found..." else 2
            await asyncio.sleep(interval)
                
        except asyncio.CancelledError:
            logger.info("main.monitor_loop shutting down...")
            raise 
            
        except Exception as e:
            logger.error(f"error in main.monitor_loop: {e}. retrying in 5 seconds...", exc_info=True)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_manager = DatabaseManager(DB_PATH)
    frontend_cache = FrontendCache()
    app.state.frontend_cache = frontend_cache
    
    monitor_task = asyncio.create_task(monitor_loop(frontend_cache, db_manager))
    logger.info("monitor_loop started...")
    
    try:
        yield
    finally:
        logger.info("shutting down monitor_loop...")
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            logger.info("monitor_loop task successfully cancelled.")
        except Exception as e:
            logger.error(f"error during monitor_loop shutdown: {e}")


    dir = "/app/data"
    d_seq = 25 # dimension sequence (context window size/prompt length)
    d_gen = 50 # dimension generate number of tokens in inference
    d_vocab = 50304 # dimension vocabulary
    d_vec = 384 # dimension embedding vector
    d_model = 384 # dimension model input

    # d_gen must be >= len(prompt)
    assert d_model == d_vec
    assert d_gen >= d_seq 

    ds_param = {'train_param': {'transforms': {'tokens': [AsTensor(long)],
                                               'y': [AsTensor(long)],
                                               'position': [AsTensor(long)]},
                                'n': 1, # set to 1 for inference
                                'd_seq': d_seq, 
                                'dir': dir,
                                'prompt': None},
                }

    model_param = {'d_model': d_model,
                   'd_vocab': d_vocab, 
                   'n_head': 6, 
                   'num_layers': 6,
                   'd_gen': d_gen,
                   'd_vec': d_vec,
                   'temperature': 1000,
                   'top_k': 3,
                   'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                                   #'y': (d_vocab, d_vec, None, True),
                                   'position': (d_gen, d_vec, None, True)},
                    } 
                                        
    metric_param = {'metric_name': 'transformer'}                        
                
    opt_param = {}
    crit_param = {}
    sample_param = {}
    sched_param = {}

    app.state.learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=None, Scheduler=None, Criterion=None,
        model_param=model_param, ds_param=ds_param, metric_param=metric_param,
        opt_param=opt_param, crit_param=crit_param, sample_param=sample_param, 
        sched_param=sched_param, batch_size=1, epoch=1,
        dir=dir, save_model=False, load_model='tinyshakes384', 
        gpu=False)
    
    app.state.model_lock = threading.Lock()

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get("/get_log")
async def get_log(request: Request): 
    data = {}
    cache = request.app.state.frontend_cache
    log_keys = [key for key in cache.cache.keys() if 'log' in key]
    for k in log_keys:
        data[k] = cache.get(k)
    return data if data else {"status": "no log data", "color": "gray", "name": "N/A"}

@app.get("/job_status")
async def get_job_status(request: Request):
    cache: FrontendCache = request.app.state.frontend_cache
    
    status = cache.get('status', default="no job status data")
    color = cache.get('color', default="gray")
    
    return {
        "status": status,
        "color": color
    }

@app.get("/history")
async def get_history(request: Request):
    cache: FrontendCache = request.app.state.frontend_cache
    data = cache.get('history', default=None)
    return data if data else {"status": "no history data", "color": "gray", "name": "N/A"}

@app.delete("/history/clear")
async def clear_latest_history():
    def db_delete():
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                DELETE FROM job_history 
                WHERE rowid = (
                    SELECT rowid FROM job_history 
                    ORDER BY created_at DESC 
                    LIMIT 1
                )
            """)
            conn.commit()
    try:
        await run_in_threadpool(db_delete)
        return {"status": "most recent job entry successfully deleted"}
        
    except Exception as e:
        logger.error(f"failed to delete latest history: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"failed to alter database log history: {str(e)}"
        )
   
@app.delete("/stop_train")
async def stop_training():
    try:
        batch_v1 = client.BatchV1Api()
        jobs = batch_v1.list_namespaced_job(namespace=NAMESPACE)
        
        if not jobs.items:
            return {"main.stop_training": "no active jobs to stop."}

        for job in jobs.items:
            batch_v1.delete_namespaced_job(
                name=job.metadata.name, 
                namespace=NAMESPACE,
                propagation_policy="Foreground" 
            )
            logger.info(f"main.stop_training terminated training job: {job.metadata.name}")
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE job_history 
                    SET status = ?, 
                        finished_at = CURRENT_TIMESTAMP
                    WHERE job_name = ?
                """, ("cancelled", job.metadata.name))

        return {"main.stop_training": f"stopped {len(jobs.items)} training job(s)."}
    except Exception as e:
        logger.error(f"main.stop_training failed to stop jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))  
    
@app.post("/reload_model")
async def reload_model():
    with app.state.model_lock:
        try:
            app.state.learner.reload_model('tinyshakes384')
            return {"main.reload_model": "weights updated successfully!"}
        except Exception as e:
            logger.error(f"main.reload_model failed to reload model: {e}")
            return {"main.reload_model": f"reload failed: {str(e)}"}
        
@app.get("/health")
async def health():
    return {"main.health": "healthy", "mode": "cpu"}  

@app.post("/train")
async def trigger_training(config: SimpleTrainConfig):
    skaffold_image_sagan_backend = get_current_image()
    
    try:
        if not app.state.frontend_cache.get('status') == "no training pods found...":
            raise HTTPException(status_code=400, detail="a trainining job is in process...")

        job_name = f"sagan-train-{uuid.uuid4().hex[:6]}"
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                backoff_limit=0,
                ttl_seconds_after_finished=30, 
                active_deadline_seconds=180, 
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={"job-group": "sagan-train"}
                    ),
                    spec=client.V1PodSpec(
                        service_account_name="sagan-backend-ksa",
                        restart_policy="Never",
                        affinity=client.V1Affinity(
                            pod_affinity=client.V1PodAffinity(
                                required_during_scheduling_ignored_during_execution=[
                                    client.V1PodAffinityTerm(
                                        label_selector=client.V1LabelSelector(
                                            match_labels={"app": "sagan-backend"} 
                                        ),
                                        topology_key="kubernetes.io/hostname"
                                    )
                                ]
                            )
                        ),
                        node_selector={"cloud.google.com/gke-nodepool": "spot-backend-pool"},
                        tolerations=[client.V1Toleration(
                            key="dedicated", operator="Equal", value="spot", effect="NoSchedule"
                        )],
                        containers=[client.V1Container(
                            name="train-job",
                            image=skaffold_image_sagan_backend,
                            image_pull_policy="IfNotPresent",
                            env=[client.V1EnvVar(name="JOB_NAME", value=job_name)],
                            command=["/app/.venv/bin/python", "-u", "train_job.py",
                                     "--batch_size", str(config.batch_size),
                                     "--epoch", str(config.epoch),
                                     "--n", str(config.n)],
                            volume_mounts=[
                                client.V1VolumeMount(name="sqlite-pvc", mount_path="/app/data")
                                ],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "3Gi", "cpu": "500m"},
                                limits={"memory": "5Gi", "cpu": "1000m"}
                            )
                        )],
                        volumes=[
                            client.V1Volume(
                                    name="sqlite-pvc",
                                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="sagan-pvc")
                            ),  
                            ]
                    )
                )
            )
        )

        batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO job_history (job_name, batch_size, epoch, n, status, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (job_name, config.batch_size, config.epoch, config.n, "running")
            )

        logger.info(f"main.trigger_training train job: '{job_name}' launched.")
        return {"message": "job launched successfully", "job_name": job_name}

    except HTTPException as he:
        raise he 
    except Exception as e:
        logger.error(f"main.trigger_training train job failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/prompt")
async def handle_text(request: Request, prompt: TextData):

    learner = request.app.state.learner
    lock = request.app.state.model_lock
    
    def locked_predict(text_input: str):
        with lock:
            return learner.run_experiment(prompt=text_input)
    try:
        response = await run_in_threadpool(locked_predict, prompt.content)
        logger.info(f"prompt: {prompt.content}\nresponse {response}")
        # match frontend's expected key "response"
        return {"response": response} 
    except Exception as e:
        full_trace = traceback.format_exc()
        logger.error(f"main.handle_text failed: {e}\n{full_trace}")
        raise HTTPException(
            status_code=500, 
            detail={"message": str(e), "traceback": full_trace}
        )
    
if __name__ == "__main__":
    asyncio.run(main())
