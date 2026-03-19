import torch
import threading
import uuid
import os
import logging
from contextlib import asynccontextmanager
from anyio.to_thread import run_sync
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException
from kubernetes import client, config

from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sagan-backend")

class TextData(BaseModel):
    content: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    DIR = "./data"
    d_gen, d_vocab, d_vec, d_model, d_pos, d_seq = 25, 50304, 384, 384, 25, 25
    
    model_param = {
        'd_model': d_model, 'd_vocab': d_vocab, 'n_head': 6, 'num_layers': 6,
        'd_seq': d_seq, 'd_vec': d_vec, 'd_gen': d_gen,
        'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                        'y': (d_vocab, d_vec, None, True),
                        'position': (d_pos, d_vec, None, True)}}

    app.state.learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=Adam, Scheduler=ReduceLROnPlateau, Criterion=CrossEntropyLoss,
        model_param=model_param, 
        ds_param={'train_param': {'d_seq': d_seq}},
        metric_param={'dir': DIR},
        dir=DIR, save_model='tinyshakes384', load_model='tinyshakes384', gpu=False 
    )
    
    app.state.model_lock = threading.Lock()
    yield

app = FastAPI(lifespan=lifespan)

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

@app.post("/train")
async def trigger_training():
    try:
        config.load_incluster_config()
        batch_v1 = client.BatchV1Api()
        job_name = f"sagan-train-{uuid.uuid4().hex[:6]}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(annotations={"gke-gcsview.io/volumes": "true"}),
                    spec=client.V1PodSpec(
                        service_account_name="sagan-backend-ksa",
                        restart_policy="Never",
                        containers=[client.V1Container(
                            name="trainer",
                            image="us-central1-docker.pkg.dev/sagan-5/sagan-image-repo/sagan-backend:v3",
                            command=["/opt/venv-backend/bin/python"],
                            args=["train_job.py"],
                            volume_mounts=[client.V1VolumeMount(name="fuse-volume", mount_path="/app/data")],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "4Gi", "cpu": "2000m"},
                                limits={"memory": "8Gi", "cpu": "4000m"}
                            )
                        )],
                        volumes=[client.V1Volume(
                            name="fuse-volume",
                            csi=client.V1CSIVolumeSource(
                                driver="gcsfuse.csi.storage.gke.io",
                                volume_attributes={"bucketName": "sagan-bucket", 
                                                   "mountOptions": "uid=1000,gid=1000,file-mode=775,dir-mode=775"}
                            )
                        )]
                    )
                )
            )
        )
        batch_v1.create_namespaced_job(namespace="sagan-app", body=job)
        return {"message": "CPU Training job launched", "job_id": job_name}
    except Exception as e:
        logger.error(f"Job launch failed: {e}")
        raise HTTPException(status_code=500, detail="Could not trigger job")

@app.get("/get_log")
async def get_log():
    log_path = "/app/data/log/cosmosis.log"
    
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