import os, uuid, traceback
import asyncio, copy, time
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.concurrency import run_in_threadpool

from pydantic import BaseModel, Field
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

import aiosqlite
import anyio

from torch import long

from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT
from cosmosis.dataset import AsTensor

DB_PATH = "/app/db/job_history.db"
NAMESPACE = "sagan-app"
DEFAULT_CACHE = {"status": "",
                 "color": "grey",  
                 "history": {"status": "no data"},
                 "job_name": "no data",
                 "log": "no data"}

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
        self.cache = copy.deepcopy(DEFAULT_CACHE)
        self.lock = asyncio.Lock()

    async def set(self, key_or_dict, value=None):
        async with self.lock:
            if isinstance(key_or_dict, dict):
                self.cache.update(key_or_dict)
            else:
                self.cache[key_or_dict] = value

    async def get(self, key, default=None):
        async with self.lock:
            return self.cache.get(key, default)

    async def get_all(self):
        async with self.lock:
            return copy.deepcopy(self.cache)


class TextData(BaseModel):
    content: str


class SimpleTrainConfig(BaseModel):
    batch_size: int = Field(default=64, ge=1, le=168, description="1 <= bs <= 168")
    epoch: int = Field(default=1, ge=1, le=10, description="1 <= epoch <= 10")
    n: int = Field(default=2000, ge=1000, le=300000, description="1000 <= n <= 300k")


class JobUpdateSchema(BaseModel):
    status: str
    test_loss: Optional[float] = None


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

    async def _init_db(self) -> None:
        dynamic_schema = ", ".join([f"{col} {datatype}" for col, datatype in self.ALLOWED_COLUMNS.items()])
        
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            
            query = f"""
                CREATE TABLE IF NOT EXISTS job_history (
                    job_name TEXT PRIMARY KEY,
                    {dynamic_schema}
                )
            """
            await conn.execute(query)
            await conn.commit()

    async def update(self, job_name: str, metric_update: dict) -> None:
        filtered_updates = {
            k: v for k, v in metric_update.items() 
            if k in self.ALLOWED_COLUMNS and k != "created_at"
        }
        
        if not filtered_updates and not job_name:
            return

        columns = ["job_name"] + list(filtered_updates.keys())
        placeholders = ", ".join(["?"] * len(columns))
        set_clause = ", ".join([f"{col} = excluded.{col}" for col in filtered_updates.keys()])
        query_values = (job_name,) + tuple(filtered_updates.values())

        async with aiosqlite.connect(self.db_path) as conn:
            query = f"""
                INSERT INTO job_history ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(job_name) DO UPDATE SET
                {set_clause}
            """
            await conn.execute(query, query_values)
            await conn.commit()

    async def get_db_history(self, limit: int = None) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

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
                
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        
    async def get_db_running_jobs(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            
            query = """
                SELECT job_name,
                       CAST((julianday('now') - julianday(created_at)) * 86400 AS INT) as training_time
                FROM job_history 
                WHERE status = 'running'
            """
            
            async with conn.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        
    async def rectify(self, zombies: list[dict]) -> None:
        if not zombies:
            return
        
        filtered_zombies = [job for job in zombies if job.get('training_time', 0) > 60]
        if not filtered_zombies:
            return
        
        timestamp = datetime.now(timezone.utc).isoformat()
        update_data = [(timestamp, job.get('job_name')) for job in filtered_zombies if job.get('job_name')]
        
        for job in filtered_zombies:
            logger.warning(f"silent failure detected: {job.get('job_name')}")
            
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executemany("""
                UPDATE job_history 
                SET status = 'silent failure', finished_at = ? 
                WHERE job_name = ?
            """, update_data)
            await conn.commit()


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

DEFAULT_TIME = datetime.fromtimestamp(0)

def get_jobs(label_selector="job-group=train-job"):
    jobs = batch_v1.list_namespaced_job(namespace=NAMESPACE, label_selector=label_selector)
    return sorted(
        jobs.items or [], 
        key=lambda x: getattr(getattr(x, 'metadata', None), 'creation_timestamp', None) or DEFAULT_TIME
    )

def get_pods(label_selector=""):
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=label_selector)
    return sorted(
        pods.items or [], 
        key=lambda x: getattr(getattr(x, 'metadata', None), 'creation_timestamp', None) or DEFAULT_TIME
    )

def get_container_names(pod):
    if not getattr(pod, 'spec', None):
        return []
    all_containers = (pod.spec.init_containers or []) + (pod.spec.containers or [])
    return [c.name for c in all_containers if getattr(c, 'name', None)]

def get_container_log(pod_name, container_name):
    try:
        return core_v1.read_namespaced_pod_log(
            name=pod_name, 
            namespace=NAMESPACE, 
            container=container_name,
            tail_lines=20,
            _request_timeout=3, 
        )
    except ApiException as e:
        return f"unable to fetch logs from kubernetes api: {e.reason}"
    except Exception as e:
        return f"system error loading log history: {e}"

def loop(old_cache: dict=None):
    old_cache = old_cache or DEFAULT_CACHE.copy()
    new_cache = DEFAULT_CACHE.copy()

    if old_cache['color'] == 'yellow':
        time.sleep(5)
    
    status_lines = []
    condition = "green"  
    train_job = False  

    pods = get_pods(label_selector="app in (backend, train-job)")
    
    for p in pods:
        pod_name = p.metadata.name or "unknown"
        pod_labels = p.metadata.labels or {}
        pod_app = pod_labels.get("app", "")

        for container_name in get_container_names(p):
            if container_name in ["train-job", "backend"]:
                log = get_container_log(pod_name, container_name)
                new_cache[f'{pod_name}-log'] = (
                    f"--- pod: {pod_name}, container: {container_name} ---\n{log}\n"
                )

        if pod_app == "train-job":
            train_job = True
            new_cache['job_name'] = pod_labels.get('job_name', 'unknown')

        if pod_app == "backend" or not p.status:
            continue

        if p.status.phase == "Failed" and p.status.reason == "Evicted":
            status_lines.append("📉 node capacity exceeded (pod evicted).")
            condition = "yellow" if condition != "red" else "red"
            continue

        for cs in (p.status.container_statuses or []):
            if cs.name != "train-job" or not cs.state:
                continue

            if cs.state.waiting:
                reason = cs.state.waiting.reason or 'unknown'
                status_lines.append(f"waiting (Reason: {reason})")
                if reason in ["ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"]:
                    status_lines.append(f"⚠️ Stuck setup: {reason}")
                    condition = "red"
                elif condition not in ["red", "blue"]:
                    condition = "yellow"

            elif cs.state.running:
                current_status = old_cache.get('status', '').strip()
                
                if current_status == 'running...🏃':
                    status_lines.append('running.🏃...')  
                elif current_status == 'running.🏃...':
                    status_lines.append('running..🏃.')    
                else:
                    status_lines.append('running...🏃')   
                    
                if condition != "red":
                    condition = "blue"

            elif cs.state.terminated:
                exit_code = cs.state.terminated.exit_code
                reason = cs.state.terminated.reason or ''
                
                if reason == "OOMKilled" or exit_code != 0:
                    status_lines.append(f"❌ failed / OOMKilled (Exit Code: {exit_code})")
                    condition = "red"
                else:
                    status_lines.append("✅ completed successfully.")
                    if condition not in ["red", "blue", "yellow"]:
                        condition = "green"

    if not train_job:
        previous_job = old_cache.get("job_name", "n/a")
        
        if previous_job in ["n/a", "unknown", "no data", "no info", None]:
            new_cache["job_name"] = "n/a"
            new_cache["status"] = "training pod is idle..."
            new_cache["color"] = "green"
        else:
            new_cache["status"] = f"job '{previous_job}' has completed. training pod is idle..."
            new_cache["color"] = "green"
            new_cache["job_name"] = "n/a"
            
    elif not status_lines:
        new_cache["status"] = "there is a training pod but with no status..."
        new_cache["color"] = "yellow"
    else:
        new_cache["status"] = "\n".join(status_lines) + "\n"
        new_cache["color"] = condition
    
    return new_cache


async def monitor_loop(frontend_cache: FrontendCache, db_manager: DatabaseManager):
    """
    Monitors kubernetes for job updates and caches results for frontend retrieval.
    """
    while True:
        try:
            cache_data = await frontend_cache.get_all() or {}
            new_data = await run_in_threadpool(loop, cache_data)
            interval = 2

            if new_data.get("color") == "green":
                interval = 10
                running_jobs = await db_manager.get_db_running_jobs()
                await db_manager.rectify(running_jobs)

            history_data = await db_manager.get_db_history(limit=20)
            new_data['history'] = history_data
                
            await frontend_cache.set(new_data)
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
                   'temperature': 1,
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

    db_manager = DatabaseManager(DB_PATH)
    frontend_cache = FrontendCache()

    await db_manager._init_db()

    app.state.db_manager = db_manager
    app.state.frontend_cache = frontend_cache
    app.state.model_lock = asyncio.Lock()

    app.state.learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=None, Scheduler=None, Criterion=None,
        model_param=model_param, ds_param=ds_param, metric_param=metric_param,
        opt_param=opt_param, crit_param=crit_param, sample_param=sample_param, 
        sched_param=sched_param, batch_size=1, epoch=1,
        dir=dir, save_model=False, load_model='tinyshakes384', 
        gpu=False)
    
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
    cache_data = await request.app.state.frontend_cache.get_all()
    data = {k: v for k, v in cache_data.items() if 'log' in k}
    return data if data else {"log": "no data"}

@app.get("/job_status")
async def get_job_status(request: Request):
    cache_data = await request.app.state.frontend_cache.get_all()
    
    return {
        "status": cache_data.get('status', "no status data"),
        "color": cache_data.get('color', "yellow"),
        "job_name": cache_data.get('job_name', "no data")
    }

@app.get("/history")
async def get_history(request: Request):
    cache_data = await request.app.state.frontend_cache.get_all() or {}
    data = cache_data.get('history')
    if data: return data
    return []

@app.delete("/history/clear")
async def clear_latest_history(request: Request): # 1. Inject the request object
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                DELETE FROM job_history 
                WHERE rowid = (
                    SELECT rowid FROM job_history 
                    ORDER BY created_at DESC 
                    LIMIT 1
                )
            """)
            await db.commit()

            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM job_history ORDER BY created_at DESC LIMIT 20") as cursor:
                rows = await cursor.fetchall()
                
        cache = request.app.state.frontend_cache
        current_cache = await cache.get_all()
        
        current_cache['history'] = [dict(row) for row in rows] if rows else []
        
        await cache.set(current_cache)

        return {"status": "most recent job entry successfully deleted..", "color": "green"}
        
    except Exception as e:
        logger.error(f"failed to delete latest history entry: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"failed to delete latest history entry: {str(e)}"
        )
@app.delete("/stop_train")
async def stop_training(request: Request):
    try:
        batch_v1 = client.BatchV1Api()
        jobs = await anyio.to_thread.run_sync(
            lambda: batch_v1.list_namespaced_job(namespace=NAMESPACE)
        )
        
        if not jobs.items:
            return {"main.stop_training": "no active jobs to stop...", "color": "green"}

        async with aiosqlite.connect(DB_PATH) as db:
            for job in jobs.items:
                job_name = job.metadata.name
                
                await anyio.to_thread.run_sync(
                    lambda j_name=job_name: batch_v1.delete_namespaced_job(
                        name=j_name, 
                        namespace=NAMESPACE,
                        propagation_policy="Foreground" 
                    )
                )
                logger.info(f"main.stop_training terminated training job: {job_name}")
                
                await db.execute("""
                    UPDATE job_history 
                    SET status = ?, 
                        finished_at = CURRENT_TIMESTAMP
                    WHERE job_name = ?
                """, ("cancelled", job_name))
                
            await db.commit()

        current_cache = await request.app.state.frontend_cache.get_all() or {}
        current_cache.update({
            "color": "green",
            "status": f"stopping {job_name}..."
        })
        await request.app.state.frontend_cache.set(current_cache)

        return {"main.stop_training": f"stopping {job_name}...", "color": "yellow"}
        
    except Exception as e:
        logger.error(f"main.stop_training failed to stop jobs: {e}", exc_info=True)
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
async def trigger_training(config: SimpleTrainConfig, request: Request):
    skaffold_image_sagan_backend = get_current_image()
    
    cache = request.app.state.frontend_cache
    
    current_cache = await cache.get_all()
    
    current_cache = current_cache or {}
    current_color = current_cache.get('color', 'green')
    
    if current_color != "green":
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot start training job: system state is currently '{current_color}'."
        )
    
    job_name = f"train-job-{uuid.uuid4().hex[:6]}"

    job_labels = {
        "job-group": "train-job",
        "app": "train-job",
        "app.kubernetes.io/name": "train-job",  
        "job-name": job_name,
        "job_name": job_name
    }

    job_metadata = client.V1ObjectMeta(name=job_name, labels=job_labels)
    pod_template_metadata = client.V1ObjectMeta(labels=job_labels)

    cluster_backend_url = os.getenv("BACKEND_URL", f"http://backend-service.{NAMESPACE}.svc.cluster.local:8000")

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=job_metadata,
        spec=client.V1JobSpec(
            backoff_limit=0,
            ttl_seconds_after_finished=30, 
            active_deadline_seconds=43200, 
            template=client.V1PodTemplateSpec(
                metadata=pod_template_metadata,
                spec=client.V1PodSpec(
                    service_account_name="sagan-backend-ksa",
                    restart_policy="Never",
                    affinity=client.V1Affinity(
                        node_affinity=client.V1NodeAffinity(
                            required_during_scheduling_ignored_during_execution=client.V1NodeSelector(
                                node_selector_terms=[
                                    client.V1NodeSelectorTerm(
                                        match_expressions=[
                                            client.V1NodeSelectorRequirement(
                                                key="topology.kubernetes.io/zone",
                                                operator="In",
                                                values=["us-central1-a"]
                                            )
                                        ]
                                    )
                                ]
                            )
                        )
                    ),
                    node_selector={"cloud.google.com/gke-nodepool": "spot-shared-pool"},
                    tolerations=[client.V1Toleration(
                        key="dedicated", operator="Equal", value="spot", effect="NoSchedule"
                    )],
                    containers=[client.V1Container(
                        name="train-job",
                        image=skaffold_image_sagan_backend,
                        image_pull_policy="IfNotPresent",
                        env=[
                            client.V1EnvVar(name="JOB_NAME", value=job_name),
                            client.V1EnvVar(name="BACKEND_URL", value=cluster_backend_url)
                        ],
                        command=["/app/.venv/bin/python", "-u", "train_job.py",
                                    "--batch_size", str(config.batch_size),
                                    "--epoch", str(config.epoch),
                                    "--n", str(config.n)],
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="data-pvc", 
                                mount_path="/app/data",
                                sub_path="data_dir"
                            )
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={"memory": "2Gi", "cpu": "500m"},
                            limits={"memory": "4Gi", "cpu": "1000m"}
                        )
                    )],
                    volumes=[
                        client.V1Volume(
                                name="data-pvc",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="sagan-pvc")
                        ),  
                    ]
                )
            )
        )
    )

    try:
        await anyio.to_thread.run_sync(
            lambda: batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO job_history (job_name, batch_size, epoch, n, status, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (job_name, config.batch_size, config.epoch, config.n, "running")
            )
            await db.commit()

        logger.info(f"main.trigger_training train job: '{job_name}' launched successfully.")
        
        current_cache.update({
            "job_name": job_name, 
            "color": "blue", 
            "status": "running...🏃"
        })
        await app.state.frontend_cache.set(current_cache)
        
        return {"message": "job launched successfully", "job_name": job_name}

    except Exception as e:
        logger.error(f"main.trigger_training train job failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to schedule job: {str(e)}")
    
@app.post("/jobs/{job_name}/callback")
async def job_callback(job_name: str, payload: JobUpdateSchema, request: Request):
    try:
        db_manager = request.app.state.db_manager 
        cache = request.app.state.frontend_cache
        
        is_success = "success" in payload.status.lower() or "complete" in payload.status.lower()
        
        metric_update = {
            "status": payload.status,
            "test_loss": payload.test_loss
        }
        
        if is_success:
            metric_update["finished_at"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        await db_manager.update(job_name=job_name, metric_update=metric_update)
        
        current_cache = await cache.get_all()
        current_cache = current_cache or {}
        
        current_cache.update({
            "job_name": job_name,
            "color": "green" if is_success else "red",
            "status": "✅ Completed successfully." if is_success else f"❌ Failed: {payload.status}"
        })
        
        await cache.set(current_cache)
        
        logger.info(f"update callback successful for: {job_name}")
        return {"status": "updated"}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"failed handling callback update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="internal database error")

@app.post("/prompt")
async def handle_text(request: Request, prompt: TextData):
    learner = request.app.state.learner
    lock = request.app.state.model_lock
    
    try:
        async with lock:
            response = await run_in_threadpool(learner.run_experiment, prompt=prompt.content)
            
        logger.info(f"prompt: {prompt.content}\nresponse: {response}")
        return {"response": response} 
        
    except Exception as e:
        full_trace = traceback.format_exc()
        logger.error(f"main.handle_text failed: {e}\n{full_trace}")
        raise HTTPException(
            status_code=500, 
            detail={"message": str(e), "traceback": full_trace}
        )