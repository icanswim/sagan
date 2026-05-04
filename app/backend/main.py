import os, uuid, threading, traceback, sqlite3
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from collections import deque
import asyncio

from anyio.to_thread import run_sync

from fastapi import FastAPI, Request, HTTPException, Response

from pydantic import BaseModel, Field

from kubernetes import client, config, watch

from torch import long
from torch.optim import Adam
from torch.optim.lr_scheduler import ReduceLROnPlateau

from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT
from cosmosis.dataset import AsTensor

logger = Metric.setup_logging(log_name='backend.main', log_dir='/app/data')

class TextData(BaseModel):
    content: str

class SimpleTrainConfig(BaseModel):
    batch_size: int = Field(default=64, ge=1, le=168, description="1 <= bs <= 168")
    epoch: int = Field(default=1, ge=1, le=10, description="1 <= epoch <= 10")
    n: int = Field(default=2000, ge=1000, le=300000, description="1000 <= n <= 300k")

DB_PATH = "/app/data/training_history.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # enable WAL mode for concurrent read/write performance
        conn.execute("PRAGMA journal_mode=WAL;")
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                job_name TEXT PRIMARY KEY,
                batch_size INTEGER,
                epoch INTEGER,
                n INTEGER,
                status TEXT,
                test_loss REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            )
        """)

init_db()

def load_k8s_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

def get_current_image():
    # try and get env var image:tag from skaffold
    env_image = os.getenv("SKAFFOLD_IMAGE_SAGAN_BACKEND")
    if env_image and env_image != "sagan-backend":
        return env_image
    # use kubernetes api to get image name and current tag
    try:
        config.load_incluster_config()
        v1 = client.CoreV1Api()
        pod_name = os.getenv("HOSTNAME") 
        pod = v1.read_namespaced_pod(name=pod_name, namespace="sagan-app")
        # grab the image from the first container
        return pod.spec.containers[0].image
    except Exception as e:
        logger.warning(f"could not fetch image name from api: {e}")
        return "sagan-backend:latest"
    
async def k8s_db_sync():
    load_k8s_config()
    batch_v1 = client.BatchV1Api()
    w = watch.Watch()
    
    while True:
        try:
            jobs_list = batch_v1.list_namespaced_job(
                namespace="sagan-app", label_selector="job-group=sagan-train"
            )
            resource_version = jobs_list.metadata.resource_version
            active_k8s_names = {j.metadata.name for j in jobs_list.items}

            with sqlite3.connect(DB_PATH) as conn:
                # mark jobs failed if they are running in db but missing from k8s
                db_running = conn.execute("SELECT job_name FROM job_history WHERE status = 'Running'").fetchall()
                for row in db_running:
                    if row[0] not in active_k8s_names:
                        conn.execute(
                            "UPDATE job_history SET status = 'Failed', finished_at = ? WHERE job_name = ?",
                            (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ'), row[0])
                        )

            # stream job events
            stream = w.stream(
                batch_v1.list_namespaced_job,
                namespace="sagan-app",
                label_selector="job-group=sagan-train",
                resource_version=resource_version,
                timeout_seconds=300
            )

            for event in stream:
                job = event['object']
                status = job.status
                job_name = job.metadata.name
                
                # check for silent conditions
                is_failed = (status.failed or 0) > 0 or any(
                    c.type == "Failed" and c.status == "True" for c in (status.conditions or [])
                )
                is_succeeded = (status.succeeded or 0) > 0

                if is_succeeded or is_failed:
                    final_status = "Succeeded" if is_succeeded else "Failed"
                    finished_at = status.completion_time or (status.conditions[-1].last_transition_time if status.conditions else None) or datetime.now(timezone.utc)
                    finished_str = finished_at.strftime('%Y-%m-%d %H:%M:%SZ')

                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("""
                            UPDATE job_history SET status = ?, finished_at = ? 
                            WHERE job_name = ? AND status = 'Running'
                        """, (final_status, finished_str, job_name))

        except Exception as e:
            logger.error(f"Sync error: {e}")
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):

    load_k8s_config()

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
    logger.info("main.lifespan inference engine initialized..")
    yield
    watcher_task = asyncio.create_task(k8s_db_sync())
    try:
        # Keep the lifespan alive until shutdown
        # Use an event or a loop to stay here
        while True:
            await asyncio.sleep(1)
    finally:
        watcher_task.cancel()
        logger.info("Sync task cancelled.")

app = FastAPI(lifespan=lifespan)

@app.post("/train")
async def trigger_training(config: SimpleTrainConfig):
    skaffold_image_sagan_backend = get_current_image()
    
    try:
        # check db for any running job
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            active_job = conn.execute(
                "SELECT job_name FROM job_history WHERE status = 'Running'"
            ).fetchone()
            if active_job:
                raise HTTPException(
                    status_code=400, 
                    detail=f"job '{active_job['job_name']}' is still running..."
                )

        # check api for any existing job (even if finished/zombie)
        batch_v1 = client.BatchV1Api()
        existing_jobs = batch_v1.list_namespaced_job(
            namespace="sagan-app",
            label_selector="job-group=sagan-train" 
        )
        if existing_jobs.items:
            raise HTTPException(
                status_code=400,
                detail="an old job is still shutting down...."
            )

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
                        labels={"job-group": "sagan-train"},
                        annotations={"gke-gcsfuse/volumes": "true"}
                    ),
                    spec=client.V1PodSpec(
                        service_account_name="sagan-backend-ksa",
                        restart_policy="Never",
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
                            volume_mounts=[client.V1VolumeMount(name="fuse-volume", mount_path="/app/data")],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "2.5Gi", "cpu": "500m"},
                                limits={"memory": "4Gi", "cpu": "1000m"}
                            )
                        )],
                        volumes=[client.V1Volume(
                            name="fuse-volume",
                            csi=client.V1CSIVolumeSource(
                                driver="gcsfuse.csi.storage.gke.io",
                                volume_attributes={
                                    "bucketName": "sagan-bucket",
                                    "mountOptions": "uid=1000,gid=1000,file-mode=775,dir-mode=775"
                                }
                            )
                        )]
                    )
                )
            )
        )

        batch_v1.create_namespaced_job(namespace="sagan-app", body=job)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO job_history (job_name, batch_size, epoch, n, status, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (job_name, config.batch_size, config.epoch, config.n, "Running")
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
        response = await run_sync(locked_predict, prompt.content)
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
    
def get_latest_file_logs(directory, pattern, limit=50):
    try:
        files = [f for f in os.listdir(directory) if pattern in f and f.endswith('.log')]
        if not files: return {}
        
        latest = sorted(files)[-1]
        with open(os.path.join(directory, latest), "r") as f:
            return {f"main log {latest}": "".join(deque(f, maxlen=limit))}
    except Exception as e:
        logger.error(f"main log {latest}error: {e}")
        return {}

def get_latest_pod_logs(v1, namespace='sagan-app', label='job-group=sagan-train'):
    try:
        pods = v1.list_namespaced_pod(namespace, label_selector=label).items
        if not pods: return {}

        pod = sorted(pods, key=lambda x: x.metadata.creation_timestamp)[-1]
        name = pod.metadata.name
        
        # check if container is ready
        status = next((s for s in (pod.status.container_statuses or []) if s.name == "train-job"), None)
        if status and status.state.waiting:
            return {f"train job log {name}": f"Status: {status.state.waiting.reason}. waiting for mount..."}

        logs = v1.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=100)
        return {f"train job log {name}": logs}
    except Exception:
        return {f"train job log {name}": "initializing logs..."}   
     
@app.get("/get_log")
async def get_log():
    v1 = client.CoreV1Api()
    output = {}
    output.update(get_latest_file_logs("/app/data", "main"))
    output.update(get_latest_pod_logs(v1, "sagan-app", "job-group=sagan-train"))

    return output

@app.get("/job_status")
async def get_job_status(response: Response):
    # Standard headers to prevent browser caching of the status
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    try:
        batch_v1 = client.BatchV1Api()
        jobs = batch_v1.list_namespaced_job(
            namespace="sagan-app", 
            label_selector="job-group=sagan-train"
        )
        
        if not jobs.items:
            return {"status": "no jobs found", "color": "gray", "name": "N/A"}

        # Get the latest job by creation time
        latest_job = sorted(jobs.items, key=lambda x: x.metadata.creation_timestamp)[-1]
        status = latest_job.status
        job_name = latest_job.metadata.name

        # 1. Check for failure (Explicit or Silent/Condition-based)
        is_failed = (status.failed or 0) > 0 or any(
            c.type == "Failed" and c.status == "True" for c in (status.conditions or [])
        )
        
        if is_failed:
            return {"status": "failed ❌", "color": "red", "name": job_name}

        # 2. Check for success
        if status.succeeded:
            return {"status": "succeeded ✅", "color": "green", "name": job_name}

        # 3. Check for active/running
        if status.active:
            return {"status": "running 🏃", "color": "blue", "name": job_name}

        # 4. Fallback for pending/orphaned states
        return {"status": "pending ⏳", "color": "orange", "name": job_name}

    except Exception as e:
        logger.error(f"UI Status Error: {e}")
        return {"status": "error", "color": "red", "name": str(e)}

@app.get("/history")
async def get_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT *, 
            CASE 
                WHEN finished_at IS NOT NULL THEN 
                    printf('%02d:%02d:%02d', 
                        (julianday(finished_at) - julianday(created_at)) * 24,
                        ((julianday(finished_at) - julianday(created_at)) * 1440) % 60,
                        ((julianday(finished_at) - julianday(created_at)) * 86400) % 60
                    )
                WHEN status = 'Running' THEN 
                    printf('%02d:%02d:%02d', 
                        (julianday('now', 'utc') - julianday(created_at)) * 24,
                        ((julianday('now', 'utc') - julianday(created_at)) * 1440) % 60,
                        ((julianday('now', 'utc') - julianday(created_at)) * 86400) % 60
                    )
                ELSE '00:00:00'
            END as training_time
            FROM job_history 
            ORDER BY created_at DESC
        """
        cursor = conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

@app.delete("/history/clear")
async def clear_history():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM job_history")
        return {"status": "history cleared"}
    except Exception as e:
        logger.error(f"failed to clear history: {e}")
        return {"error": str(e)}, 500
        
@app.delete("/stop_train")
async def stop_training():
    try:
        batch_v1 = client.BatchV1Api()
        jobs = batch_v1.list_namespaced_job(namespace="sagan-app")
        
        if not jobs.items:
            return {"main.stop_training": "no active jobs to stop."}

        for job in jobs.items:
            batch_v1.delete_namespaced_job(
                name=job.metadata.name, 
                namespace="sagan-app",
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


