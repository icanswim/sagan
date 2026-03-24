import sys, logging

from torch import long
from torch.optim import Adam
from torch.nn import CrossEntropyLoss
from torch.optim.lr_scheduler import ReduceLROnPlateau

from cosmosis.dataset import AsTensor 
from gpt.dataset import TinyShakes
from cosmosis.learning import Learn, Metric, Selector
from cosmosis.model import GPT

print("🚀 Starting training job...")

# Force logs to stream immediately to GKE/Streamlit
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout 
)
logger = logging.getLogger("sagan-trainer")

def run_training():
    dir = "/app/data/"
    d_vocab, d_vec, d_model, d_seq = 50304, 384, 384, 25
    
    model_param = {'d_model': d_model, 
                   'd_vocab': d_vocab, 
                   'n_head': 6, 
                   'num_layers': 6,
                   'd_seq': d_seq, 
                   'd_vec': d_vec,
                   'embed_param': {'tokens': (d_vocab, d_vec, None, True), 
                                   'y': (d_vocab, d_vec, None, True),
                                   'position': (d_seq, d_vec, None, True)}
                    }

    ds_param = {'train_param': {'transforms': {'tokens': [AsTensor(long)],
                                               'y': [AsTensor(long)],
                                               'position': [AsTensor(long)]},
                                'd_seq': d_seq}
                }

    logger.info("Initializing Learner for GKE Training Job...")
    
    learner = Learn(
        [TinyShakes], GPT, Metric=Metric, Sampler=Selector, 
        Optimizer=Adam, Scheduler=ReduceLROnPlateau, Criterion=CrossEntropyLoss,
        model_param=model_param, ds_param=ds_param, 
        batch_size=16, epochs=5, gpu=False, 
        dir=dir, save_model='tinyshakes384', load_model='tinyshakes384.pt')

    logger.info(f"🚀 Training starting on CPU'...")
    
    try:
        learner.run_experiment()
        logger.info("✅ Training complete. Weights successfully synced to GCS bucket.")
    except Exception as e:
        logger.error(f"❌ Training failed: {str(e)}")
        sys.exit(1) # Ensure the GKE Job marks itself as 'Failed'

if __name__ == "__main__":
    run_training()