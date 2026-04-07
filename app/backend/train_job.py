import sys

from torch import long
from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT

logger = Metric.setup_logging(log_name='train_job', log_dir='./data')

def run_training():
    dir = "./data"

    d_seq = 25       # dimension sequence length
    d_gen = 25       # dimension generate number of tokens
    d_vocab = 50304  # dimension vocabulary
    d_vec = 384      # dimension embedding vector
    d_model = 384    # dimension model input
    
    model_param = {
        'd_model': d_model,
        'd_vocab': d_vocab, 
        'n_head': 6, 
        'num_layers': 6,
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
            'n': 500,
            'd_seq': d_seq,
            'dir': dir,
            'prompt': None,
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
        dir=dir, batch_size=64, epoch=3, gpu=False,
        save_model='tinyshakes384', load_model='tinyshakes384'
    )
    
    try:
        out = learner.run_experiment()
        logger.info("train_job complete... {}".format(out))
    except Exception as e:
        logger.error(f"train_job failed: {str(e)}")
        sys.exit(1) 

if __name__ == "__main__":
    run_training()