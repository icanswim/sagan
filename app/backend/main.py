import os, uuid, threading, traceback
from contextlib import asynccontextmanager
from collections import deque

from anyio.to_thread import run_sync

from fastapi import FastAPI, Request, HTTPException

from pydantic import BaseModel

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

def load_k8s_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

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
        sched_param=sched_param,
        dir=dir, save_model=False, load_model='tinyshakes384', 
        gpu=False)
    
    app.state.model_lock = threading.Lock()
    logger.info("main.lifespan inference engine initialized..")
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/train")
async def trigger_training():
    try:
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
                        service_account_name="sagan-backend-ksa", 
                        containers=[client.V1Container(
                            name="trainer",
                            image="sagan-backend", 
                            image_pull_policy="Never",   
                            command=["/app/.venv/bin/python", "-u", "train_job.py"],
                            volume_mounts=[client.V1VolumeMount(name="data-storage", 
                                                                mount_path="/app/data")],
                            resources=client.V1ResourceRequirements(
                                requests={"memory": "512Mi", "cpu": "500m"},
                                limits={"memory": "4Gi", "cpu": "1000m"}
                            )
                        )],
                        volumes=[client.V1Volume(
                            name="data-storage",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name="sagan-pvc" 
                            )
                        )]
                    )
                )
            )
        )
        batch_v1.create_namespaced_job(namespace="sagan-app", body=job)
        logger.info(f"main.trigger_training train job: '{job_name}' in 'sagan-app' namespace.")
        return {"message": "main.trigger_training train job launched...", "job_id": job_name}
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
        # Match your frontend's expected key "response"
        return {"response": response} 
    except Exception as e:
        # Capture the full trace
        full_trace = traceback.format_exc()
        logger.error(f"main.handle_text failed: {e}\n{full_trace}")
        # Send both the message and the trace to Streamlit
        raise HTTPException(
            status_code=500, 
            detail={"message": str(e), "traceback": full_trace}
        )
    
@app.get("/get_log")
async def get_log():
    log_dir = "/app/data"
    output = {}

    # force a sync with GCS Fuse
    try:
        os.utime(log_dir, None) # touch the directory to invalidate cache
    except:
        pass
    
    try:
        # force GCS Fuse to refresh its metadata by listing the directory
        all_files = os.listdir(log_dir)
        
        targets = ['backend', 'train_job']
        
        for target in targets:
            matches = [f for f in all_files if target in f and f.endswith('.log')]
            
            if matches:
                # sort by filename (which includes the timestamp) to get the latest
                latest_file = sorted(matches)[-1]
                full_path = os.path.join(log_dir, latest_file)
                
                with open(full_path, "r") as f:
                    # read the last 100 lines
                    last_lines = deque(f, maxlen=100)
                    output[latest_file] = "".join(last_lines)
                    
    except Exception as e:
        logger.error(f"Log read error: {e}")
    
    return output

@app.get("/job_status")
async def get_job_status():
    try:
        batch_v1 = client.BatchV1Api()
        # list jobs in the namespace, sorted by creation timestamp
        jobs = batch_v1.list_namespaced_job(namespace="sagan-app")
        if not jobs.items:
            return {"main.get_job_status": "No Jobs Found", "color": "gray"}

        latest_job = sorted(jobs.items, key=lambda x: x.metadata.creation_timestamp)[-1]
        name = latest_job.metadata.name
        status = latest_job.status

        if status.active:
            return {"main.get_job_status": "Running 🏃", "color": "blue", "name": name}
        if status.succeeded:
            return {"main.get_job_status": "Succeeded ✅", "color": "green", "name": name}
        if status.failed:
            return {"main.get_job_status": "Failed ❌", "color": "red", "name": name}
            
        return {"main.get_job_status": "Pending ⏳", "color": "orange", "name": name}
    except Exception as e:
        return {"main.get_job_status": f"Error: {str(e)}", "color": "red"}
    
@app.delete("/stop_train")
async def stop_training():
    try:
        batch_v1 = client.BatchV1Api()
        jobs = batch_v1.list_namespaced_job(namespace="sagan-app")
        
        if not jobs.items:
            return {"main.stop_training": "No active jobs to stop."}

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
            app.state.learner.load_model('tinyshakes384.pth')
            return {"main.reload_model": "weights updated successfully!"}
        except Exception as e:
            logger.error(f"main.reload_model failed to reload model: {e}")
            return {"main.reload_model": f"reload failed: {str(e)}"}
        
@app.get("/health")
async def health():
    return {"main.health": "healthy", "mode": "cpu"}  