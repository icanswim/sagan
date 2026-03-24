import os, uuid, glob, threading
from contextlib import asynccontextmanager

from anyio.to_thread import run_sync

from fastapi import FastAPI, Request, HTTPException

from pydantic import BaseModel

from kubernetes import client, config

from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT

Metric.setup_logging(log_name='backend', log_dir='/app/data/')

class TextData(BaseModel):
    content: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = "/app/data/"
    d_gen, d_vocab, d_vec, d_model, d_seq = 25, 50304, 384, 384, 25
    
    model_param = {
        'd_model': d_model, 'd_vocab': d_vocab, 'n_head': 6, 'num_layers': 6,
        'd_seq': d_seq, 'd_vec': d_vec, 'd_gen': d_gen,
        'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                        'y': (d_vocab, d_vec, None, True),
                        'position': (d_seq, d_vec, None, True)}}
    
    ds_param = {'train_param': {'dir': data_dir, 'd_seq': d_seq, 'n': 1000, 'prompt': None}}
    # n = 338035
    app.state.learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=Adam, Scheduler=ReduceLROnPlateau, Criterion=CrossEntropyLoss,
        model_param=model_param, ds_param=ds_param, metric_param={'dir': data_dir},
        dir=data_dir, save_model='tinyshakes384', load_model='tinyshakes384.pt', gpu=False 
    )
    
    app.state.model_lock = threading.Lock()
    yield

app = FastAPI(lifespan=lifespan)

def load_k8s_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

@app.post("/train")
async def trigger_training():
    try:
        load_k8s_config()
        batch_v1 = client.BatchV1Api()
        job_name = f"sagan-train-{uuid.uuid4().hex[:6]}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=300, # Auto-cleanup in 5 mins
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        # Matches your ServiceAccount in local-k8s-rbac.yaml
                        service_account_name="sagan-backend-ksa", 
                        containers=[client.V1Container(
                            name="trainer",
                            image="sagan-backend:local", # Matches skaffold.yaml tag
                            image_pull_policy="Never",   # Forces use of local Minikube image
                            command=["python", "train_job.py"],
                            volume_mounts=[client.V1VolumeMount(name="data-storage", mount_path="/app/data")],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "512Mi", "cpu": "500m"},
                                limits={"memory": "1Gi", "cpu": "1000m"}
                            )
                        )],
                        volumes=[client.V1Volume(
                            name="data-storage",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name="sagan-pvc" # Shared with the backend
                            )
                        )]
                    )
                )
            )
        )
        # FIX: Changed namespace from 'default' to 'sagan-app'
        batch_v1.create_namespaced_job(namespace="sagan-app", body=job)
        return {"message": "Job launched in sagan-app namespace", "job_id": job_name}
    except Exception as e:
        logger.error(f"Launch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/prompt")
async def handle_text(request: Request, prompt: TextData):
    learner = request.app.state.learner
    lock = request.app.state.model_lock
    def locked_predict(text_input: str):
        with lock:
            return learner.run_experiment(prompt=text_input)
    try:
        result = await run_sync(locked_predict, prompt.content)
        return {"status": "success", "output": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
def load_k8s_config():  # Load Kubernetes config, trying local first then in-cluster
    try:
        config.load_kube_config()
    except config.ConfigException:
        config.load_incluster_config()

@app.get("/get_log")
async def get_log():

    log_search_pattern = f'/app/data/*.log'
    log_files = glob.glob(log_search_pattern)
    
    if not log_files:
        raise HTTPException(
            status_code=404, 
            detail=f"No log files found matching prefix: {pattern}"
        )

    latest_log_path = max(log_files, key=os.path.getmtime)

    try:
        with open(latest_log_path, "r") as f:
            lines = f.readlines()
            last_lines = lines[-100:] if len(lines) > 100 else lines
            return {
                "filename": os.path.basename(latest_log_path),
                "log": "".join(last_lines)
            }
    except Exception as e:
        return {"error": f"Failed to read {latest_log_path}: {str(e)}"}


@app.get("/health")
async def health():
    return {"status": "healthy", "mode": "cpu"}  