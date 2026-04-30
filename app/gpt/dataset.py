import sys # required for relative imports in jupyter lab
sys.path.insert(0, '../')
import logging
import random
import os
import requests
import tiktoken
import numpy as np

from sys import getsizeof

from cosmosis.dataset import TDataset

logger = logging.getLogger(__name__)

class TinyShakes(TDataset):
    """
    https://github.com/karpathy/nanoGPT
    """      
    def load_data(self, dir='./data', d_seq=10, n=301306, prompt=None, tokenizer=tiktoken):
        # n = 301306 total tokens
        data_url = 'https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt'
        self.encoding = tokenizer.get_encoding("gpt2")
        self.n, self.d_seq, self.dir = n, d_seq, dir
        tiny_bin = os.path.join(self.dir, 'tinyshakes_stripped_encoded.bin')

        if prompt is None: # pre-loading awaiting prompt input for inference 
            self.ds_idx = [0]
            return {0: 'TinyShakes dataset loaded and ready for inference. Awaiting prompt input.'}
        elif prompt is False: # load dataset for training
            if not os.path.exists(os.path.join(self.dir, 'tinyshakes.txt')):
                with open(os.path.join(self.dir, 'tinyshakes.txt'), 'w', encoding='utf-8') as f:
                    f.write(requests.get(data_url).text)
                logger.info('TinyShakes.load_data tinyshakes.txt downloaded and saved in {}'.format(self.dir))
            else:
                logger.info('TinyShakes.load_data inyshakes.txt loaded from saved file in {}'.format(self.dir))

            if not os.path.exists(tiny_bin) or os.path.getsize(tiny_bin) == 0:
                with open(os.path.join(self.dir, 'tinyshakes.txt'), 'r', encoding='utf-8') as f:
                    data = f.read()
                    data = data.replace('\n', ' ')
                # encode with tiktoken gpt2 bpe
                tokens = self.encoding.encode_ordinary(data)
                tokens = np.array(tokens, dtype=np.uint16)
                tokens.tofile(tiny_bin)
                logger.info('TinyShakes.load_data text has been tokenized and saved in file {}'.format(tiny_bin))
            else:
                logger.info('TinyShakes.load_data tokens loaded from file {}'.format(tiny_bin))

            try:
                ds = np.memmap(tiny_bin, dtype=np.uint16, mode='r')
            except ValueError as e:
                logger.info(f"TinyShakes.load_data error mapping file: {e}")
                raise RuntimeError(f"TinyShakes.load_data failed to load dataset at {tiny_bin}.")

            if self.n < len(ds):
                rand_start = random.randint(0, len(ds) - self.n - 1)
                self.ds_idx = list(range(rand_start, rand_start + self.n - self.d_seq - 1))
            else:
                self.ds_idx = list(range(len(ds) - self.d_seq - 1))
        else: # prompt provided for inference, encode and return dataset
            ds = self.encoding.encode_ordinary(prompt)
            ds = np.array(ds, dtype=np.uint16)
            self.d_seq = ds.shape[0]
            self.ds_idx = [0]

        return ds.copy()
    
    def prompt(self, prompt):
        ds = self.encoding.encode_ordinary(prompt)
        ds = np.array(ds, dtype=np.uint16)
        self.d_seq = ds.shape[0]
        self.ds_idx = [0]
        return ds.copy()

