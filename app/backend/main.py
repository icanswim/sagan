from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes

@asynccontextmanager
async def lifespan(app: FastAPI):

    d_seq = 20 # dimension sequence
    d_vocab = 50304 # dimension vocabulary
    d_vec = 384 # dimension embedding vector
    d_model = 384 # dimension model input
    assert d_model == d_vec

    ds_param = {'train_param': {'transforms': {'tokens': [AsTensor(long)],
                                               'y': [AsTensor(long)],
                                               'position': [AsTensor(long)]},
                                'd_seq': d_seq,
                                #'n': 1000,
                                }}

    app.state.model_param = {'d_model': d_model,
                            'd_vocab': d_vocab, 
                            'n_head': 6, 
                            'num_layers': 6,
                            'd_seq': d_seq,
                            'd_vec': d_vec,
                            'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                                            'y': (d_vocab, d_vec, None, True),
                                            'position': (d_seq, d_vec, None, True)}} 
                                        
    metrics_param = {'metric_name': 'transformer',
                    'report_interval': 1,
                    'log_plot': False,
                    'min_lr': .0025} # break if learning rate falls below                        
                
    opt_param = {'lr': 0.01}

    crit_param = {}

    sample_param = {'set_seed': 88,
                    'splits': (.7,.15)}

    sched_param = {'factor': .5, 
                'patience': 2,
                'cooldown': 2}

    app.state.learn = Learn([TinyShakes], 
                            GPT,
                            Metrics=Metrics,
                            Sampler=Selector, 
                            Optimizer=Adam, 
                            Scheduler=ReduceLROnPlateau, 
                            Criterion=CrossEntropyLoss,
                            model_param=app.state.model_param, ds_param=ds_param, sample_param=sample_param,
                            opt_param=opt_param, sched_param=sched_param, crit_param=crit_param,
                            metrics_param=metrics_param, 
                            batch_size=32, epochs=1, gpu=True, save_model='tinyshakes384', 
                            load_model=None, load_embed=False, target='y')
    
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "sagan-backend"}

@app.get("/logs")
async def read_data(request: Request):
    file_path = "/data/"
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found in bucket")
    
    # Standard synchronous read (works because FUSE handles the API calls)
    with open(file_path, "r") as f:
        content = f.read()

    return {'content': content}

@app.post("/prompt")
async def handle_text(prompt: TextData):
    
    # inference
    d_gen = 20 # dimension generate number of tokens
    d_vocab = 50304 # dimension vocabulary
    d_vec = 384 # dimension embedding vector
    d_model = 384 # dimension model input
    d_pos = 20 # dimension positional encoding d_pos >= max(len(prompt_tokens), d_gen)

    assert d_model == d_vec

    ds_param = {'train_param': {'transforms': {'tokens': [AsTensor(long)],
                                               'y': [AsTensor(long)],
                                               'position': [AsTensor(long)]},
                                'prompt': prompt.content}}

    model_param = {
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
                                        
    metrics_param = {'metric_name': 'transformer'}                        
                
    opt_param = {}

    crit_param = {}

    sample_param = {}

    sched_param = {}

    learn = Learn([TinyShakes], 
                  GPT,
                  Metrics=Metrics,
                  Sampler=Selector, 
                  Optimizer=None, 
                  Scheduler=None, 
                  Criterion=None, # no criterion implies inference
                  model_param=model_param, ds_param=ds_param, sample_param=sample_param,
                  opt_param=opt_param, sched_param=sched_param, crit_param=crit_param,
                  metrics_param=metrics_param, 
                  batch_size=1, epochs=1, gpu=False, 
                  load_model='tinyshakes384.pth', load_embed=True, target=None)

    return {"status": "success", "received": prompt.content}
