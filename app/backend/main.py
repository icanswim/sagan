import torch
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from anyio.to_thread import run_sync
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException

from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metrics, Selector
from cosmosis.models import GPT

class TextData(BaseModel):
    content: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    d_gen = 25 # dimension generate number of tokens
    d_vocab = 50304 # dimension vocabulary
    d_vec = 384 # dimension embedding vector
    d_model = 384 # dimension model input
    d_pos = 25 # dimension positional encoding d_pos >= max(len(prompt_tokens), d_gen)
    d_seq = 25 # dimension sequence

    assert d_model == d_vec

    torch.set_num_threads(torch.get_num_threads()) 
    
    ds_train_param = {'train_param': {'transforms': {'tokens': [AsTensor('long')],
                                                     'y': [AsTensor('long')],
                                                     'position': [AsTensor('long')]},
                                      'd_seq': d_seq}}
    
    model_param={'d_model': d_model,
                 'd_vocab': d_vocab, 
                 'n_head': 6, 
                 'num_layers': 6,
                 'd_seq': d_seq,
                 'd_vec': d_vec,
                 'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                                 'y': (d_vocab, d_vec, None, True),
                                 'position': (d_seq, d_vec, None, True)}}
    
    app.state.training_learner = Learn(
        [TinyShakes], GPT, Metrics=Metrics, Sampler=Selector, 
        Optimizer=Adam, Scheduler=ReduceLROnPlateau, Criterion=CrossEntropyLoss,
        model_param=model_param, ds_param=ds_train_param, 
        batch_size=32, epochs=1, gpu=False, save_model='tinyshakes384')

    model_param_inf = {
                'd_model': d_model,
                'd_vocab': d_vocab, 
                'n_head': 6, 
                'num_layers': 6,
                'd_gen': d_gen,
                'd_vec': d_vec,
                'temperature': 100,
                'top_k': 3,
                'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                                'position': (d_pos, d_vec, None, True)},
                } 

    app.state.inference_learner = Learn(
        [TinyShakes], GPT, Metrics=Metrics, Sampler=Selector, 
        Optimizer=None, Scheduler=None, Criterion=None, # no criterion implies inference
        model_param=model_param_inf, 
        ds_param={'train_param': {'transforms': {}, 'prompt': ""}}, 
        batch_size=1, gpu=False, load_model='tinyshakes384.pth', load_embed=True
    )
    
    app.state.model_lock = threading.Lock()
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/prompt")
async def handle_text(request: Request, prompt: TextData):
    # Access the pre-loaded inference model from state
    learner = request.app.state.inference_learner
    lock = request.app.state.model_lock

    def locked_predict(text: str):
        with lock:
            # inference
            return learner.predict(text)

    try:
        result = await run_sync(locked_predict, prompt.content)
        return {"status": "success", "received": prompt.content, "output": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "sagan-backend"}

@app.get("/logs")
async def read_data():
    file_path = Path("/data/training_logs.txt")
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found in bucket")
    
    # Standard synchronous read (works because FUSE handles the API calls)
    content = file_path.read_text()

    return {'content': content}
