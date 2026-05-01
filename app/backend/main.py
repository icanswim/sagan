import os, uuid, threading, traceback, sqlite3
from contextlib import asynccontextmanager
from collections import deque

from anyio.to_thread import run_sync

from fastapi import FastAPI, Request, HTTPException

from pydantic import BaseModel, Field

from kubernetes import client, config

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
    batch_size: int = Field(default=64, ge=1, le=128, description="Batch size must be between 1 and 512")
    epoch: int = Field(default=1, ge=1, le=10, description="Epochs must be between 1 and 100")
    n: int = Field(default=5000, ge=100, le=300000, description="Samples must be between 100 and 300k")

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

app = FastAPI(lifespan=lifespan)

@app.post("/train")
async def trigger_training(config: SimpleTrainConfig):

    skaffold_image_sagan_backend = get_current_image()
    try:
        batch_v1 = client.BatchV1Api()
        job_name = f"sagan-train-{uuid.uuid4().hex[:6]}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=200, 
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
                                requests={"memory": "1Gi", "cpu": "500m"},
                                limits={"memory": "5Gi", "cpu": "1000m"}
                            )
                        )],
                        volumes=[client.V1Volume(
                            name="fuse-volume", # must match the volume_mounts name
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

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO job_history (job_name, batch_size, epoch, n, status) VALUES (?, ?, ?, ?, ?)",
                (job_name, config.batch_size, config.epoch, config.n, "Running")
            )

        batch_v1.create_namespaced_job(namespace="sagan-app", body=job)
        logger.info(f"main.trigger_training train job: '{job_name}' in 'sagan-app' namespace.")
        return {"message": "main.trigger_training train job launched...", "job_name": job_name}
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
            return {f"train job log {name}": f"Status: {status.state.waiting.reason}. Waiting for mount..."}

        logs = v1.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=100)
        return {f"train job log {name}": logs}
    except Exception:
        return {f"train job log {name}": "Initializing logs..."}   
     
@app.get("/get_log")
async def get_log():
    v1 = client.CoreV1Api()
    output = {}
    output.update(get_latest_file_logs("/app/data", "main"))
    output.update(get_latest_pod_logs(v1, "sagan-app", "job-group=sagan-train"))

    return output

@app.get("/job_status")
async def get_job_status():
    try:
        batch_v1 = client.BatchV1Api()
        jobs = batch_v1.list_namespaced_job(namespace="sagan-app")
        if not jobs.items:
            return {"status": "No Jobs Found", "color": "gray", "name": "N/A"}

        latest_job = sorted(jobs.items, key=lambda x: x.metadata.creation_timestamp)[-1]
        name = latest_job.metadata.name
        status = latest_job.status

        if status.active:
            res = {"status": "Running 🏃", "color": "blue", "name": name}
        elif status.succeeded:
            res = {"status": "Succeeded ✅", "color": "green", "name": name}
        elif status.failed:
            res = {"status": "Failed ❌", "color": "red", "name": name}
        else:
            res = {"status": "Pending ⏳", "color": "orange", "name": name}

        # sync with sqlite if the job is finished
        if status.succeeded or status.failed:
            final_val = "Succeeded" if status.succeeded else "Failed"
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE job_history SET status = ? WHERE job_name = ?", (final_val, name))

        return res

    except Exception as e:
        return {"status": f"Error: {str(e)}", "color": "red", "name": "Error"}

@app.get("/history")
async def get_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # SQL math: (finished - created) converted to HH:MM:SS format
        query = """
            SELECT *, 
            CASE 
                WHEN finished_at IS NOT NULL THEN 
                    strftime('%H:%M:%S', (julianday(finished_at) - julianday(created_at)), '12:00:00')
                ELSE 'Running...' 
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

        return {"main.stop_training": f"Stopped {len(jobs.items)} training job(s)."}
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


