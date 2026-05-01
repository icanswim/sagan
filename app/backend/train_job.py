import os
import argparse
import traceback
import sqlite3

from torch import long
from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT

logger = Metric.setup_logging(log_name='backend.train-job', log_dir='/app/data')

def run_training(d_model=384, d_vec=384, d_seq=25, d_gen=25, d_vocab=50304, 
                 n_head=6, num_layers=6, batch_size=64, epoch=1, n=1000):
    
    dir = "/app/data"
    
    # d_seq = dimension sequence length
    # d_gen = dimension generate number of tokens in inference
    # d_vocab = dimension vocabulary (50304) 
    # d_vec = dimension embedding vector
    # d_model = dimension model input
    # n_head = number of attention heads
    # num_layers = number of transformer layers
    # n = random contiguous subset size

    assert d_model == d_vec
    
    model_param = {
        'd_model': d_model,
        'd_vocab': d_vocab, 
        'n_head': n_head, 
        'num_layers': num_layers,
        'd_gen': d_gen,
        'd_vec': d_vec,
        'temperature': 1000,
        'top_k': 3,
        'embed_param': {
            'tokens': (d_vocab, d_vec, None, True), 
            'y': (d_vocab, d_vec, None, True),
            'position': (d_seq, d_vec, None, True)
        }
    } 
    
    ds_param = {
        'train_param': {
            'transforms': {
                'tokens': [AsTensor(long)],
                'y': [AsTensor(long)],
                'position': [AsTensor(long)]
            },
            'n': n,
            'd_seq': d_seq,
            'dir': dir,
            'prompt': False,
        }
    }

    metric_param = {
        'metric_name': 'transformer',
        'report_interval': 1,
        'last_n': 10,
        'min_lr': 0.0025  # break if learning rate falls below
    } 
                
    opt_param = {'lr': 0.01}
    crit_param = {}
    sample_param = {'set_seed': 88, 'splits': (0.7, 0.15)}
    sched_param = {'factor': 0.5, 'patience': 2, 'cooldown': 2}
    
    learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=Adam, Scheduler=ReduceLROnPlateau, Criterion=CrossEntropyLoss,
        model_param=model_param, ds_param=ds_param, metric_param=metric_param,
        opt_param=opt_param, crit_param=crit_param, sample_param=sample_param, 
        sched_param=sched_param,
        dir=dir, batch_size=batch_size, epoch=epoch, gpu=False,
        save_model='tinyshakes384', load_model='tinyshakes384'
    )
    
    try:
        out = learner.run_experiment()
        logger.info("train-job complete... {}".format(out))
        job_name = os.environ.get("JOB_NAME")
        test_loss = out.get('test_loss') if isinstance(out, dict) else None
        update_db("Succeeded", test_loss)
    except Exception as e:
        full_trace = traceback.format_exc()
        logger.error(f"backend.train-job failed: {e}\n{full_trace}")
        update_db("Failed", None)
        raise RuntimeError(f"Training failed: {e}") 
    
def update_db(status, test_loss):
    job_name = os.environ.get("JOB_NAME") # matches env var
    with sqlite3.connect("/app/data/training_history.db") as conn:
        conn.execute(
            """UPDATE job_history 
               SET status = ?, test_loss = ?, finished_at = CURRENT_TIMESTAMP 
               WHERE job_name = ?""", 
            (status, test_loss, job_name)
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="run sagan train job")
    
    parser.add_argument("--batch_size", type=int, default=64)
    parser.add_argument("--epoch", type=int, default=1)
    parser.add_argument("--n", type=int, default=5000)
    
    args = parser.parse_args()

    run_training(
        batch_size=args.batch_size,
        epoch=args.epoch,
        n=args.n
    )