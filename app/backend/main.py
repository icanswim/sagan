import os, uuid, logging, threading
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel
from kubernetes import client, config
from anyio import to_thread

# Assuming your models/utils are imported here (Learn, GPT, etc.)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sagan-backend")

class TextData(BaseModel):
    content: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Use /app/data which will be backed by a PVC in Minikube
    data_dir = "/app/data/"
    d_gen, d_vocab, d_vec, d_model, d_seq = 25, 50304, 384, 384, 25
    
    model_param = {
        'd_model': d_model, 'd_vocab': d_vocab, 'n_head': 6, 'num_layers': 6,
        'd_seq': d_seq, 'd_vec': d_vec, 'd_gen': d_gen,
        'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                        'y': (d_vocab, d_vec, None, True),
                        'position': (d_seq, d_vec, None, True)}}
    
    ds_param = {'train_param': {'dir': data_dir, 'd_seq': d_seq, 'n': 338035, 'prompt': None}}

    # Initialize learner
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
    log_path = "/app/data/cosmosis.log"
    
    if not os.path.ismount("/app/data"):
        raise HTTPException(status_code=503, detail="Storage volume not mounted")

    if not os.path.exists(log_path):
        return {"log": f"Log file not found at {log_path}. Ensure your training job has started."}

    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
            last_lines = lines[-100:] if len(lines) > 100 else lines
            return {"log": "".join(last_lines)}
    except Exception as e:
        return {"error": f"Failed to read log: {str(e)}"}

@app.get("/health")
async def health():
    return {"status": "healthy", "mode": "cpu"}  