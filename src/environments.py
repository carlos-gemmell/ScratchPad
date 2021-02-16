import random
import re
import copy
import torch
from time import perf_counter
import numpy as np
import tqdm
from pytorch_lightning import seed_everything
from torch.utils.data import Dataset, DataLoader
from pytorch_lightning import Trainer, Callback, seed_everything
import string
from tokenizers import ByteLevelBPETokenizer, Tokenizer

from gym import error, spaces, utils
import gym
    

def scratch_pad_exec(code):
    if not code:
        return ''
    try:
        prior_code, _, last_line = code.rpartition('\n')
        exec(f'{prior_code}\nglobal __i__; __i__ = {last_line}')
        global __i__
        return str(__i__)
    except Exception as e:
        if hasattr(e,'msg'):
            return "ERROR: " + e.msg
        return "ERROR: " + str(e)

def remove_ScratchPad(current_string):
    return re.sub(r'\[SP\]([^.]*)\[ESP\]', '',current_string)
    

class AddGymEnv(gym.Env):
    metadata = {"render.modes": ["human"], }
    
    def __init__(self, tokenizer_path='data/tokenizer_simple.json', testing=False, max_val=10, max_token_length=35, padding=True):
        self.testing = testing
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.max_token_length = max_token_length
        self.vocab_size = len(self.tokenizer.get_vocab())
        self.max_val = max_val
        self.padding = padding
        self.ep_count = 0
        
        self.action_space = spaces.Discrete(self.vocab_size)
        self.observation_space = spaces.MultiDiscrete([self.vocab_size] * self.max_token_length)
        
        self.reset()
        
    def seed(self, seed=None):
        seed_everything(seed)
        
    def step(self, action):
        self.action_count += 1
        self.current_state.append(action)
        self.execute()
        
        pad_width = self.max_token_length - len(self.current_state)
        if self.padding and pad_width > 0:
            next_state = np.pad(self.current_state, (0,pad_width))
        else:
            next_state = self.current_state

        done = self.is_done()
        reward = self.reward_fn()
        return next_state[:self.max_token_length], reward, done, {}
    
    def execute(self):
        EXEC_id = self.tokenizer.get_vocab()['>>>']
        SP_id = self.tokenizer.get_vocab()['[SP]']
        ESP_id = self.tokenizer.get_vocab()['[ESP]']
        
        SP_count = sum( x == SP_id for x in self.current_state)
        ESP_count = sum( x == ESP_id for x in self.current_state)
        
        if SP_count <= ESP_count:
            return
        
        if self.current_state[-1] == EXEC_id:
            sequence = self.tokenizer.decode(self.current_state, skip_special_tokens=False)
            prior_scratch_pad_sequence, _, last_scratch_pad_sequence = sequence.rpartition('[SP]')
            prior_scratch_pad_sequences = re.findall(r'\[SP\]([^.]*)\[ESP\]', prior_scratch_pad_sequence)
            all_statements = ''.join(prior_scratch_pad_sequences + [last_scratch_pad_sequence])
            individual_statements = re.split(r'>>>.*\[NL\]|>>>', all_statements)
            code = '\n'.join([s for s in individual_statements if s])
            
            stmnt_out = scratch_pad_exec(code)
            tokenized_stmnt_out = self.tokenizer.encode(stmnt_out + '[NL]').ids
            self.current_state += tokenized_stmnt_out
    
    def render(self):
        current_string =  self.tokenizer.decode(self.current_state, skip_special_tokens=False)
        print(current_string)
    
    
    def reward_fn(self):
        if not self.is_done():
            return 0 
        
        current_string =  self.tokenizer.decode(self.current_state, skip_special_tokens=False)
        current_string = remove_ScratchPad(current_string)
        
        if current_string == self.answer:
            return 1
        else:
            return -1
    
    def is_done(self):
        EOS_id = self.tokenizer.get_vocab()['[EOS]']
        return self.current_state[-1] == EOS_id or len(self.current_state) >= self.max_token_length
    
    def getAutoGeneratedMask(self, current_state):
        EXEC_id = self.tokenizer.get_vocab()['>>>']
        NL_id = self.tokenizer.get_vocab()['[NL]']
        state_len = current_state.shape[0]
        is_auto_gen_start_tok = current_state == EXEC_id
        is_auto_gen_start_tok = torch.cat((torch.tensor([False]), is_auto_gen_start_tok[:-1]))
        
        is_auto_gen_end_tok = current_state == NL_id
        is_auto_gen_end_tok = torch.cat((torch.tensor([False]), is_auto_gen_end_tok[:-1]))
        
        SP_toks = is_auto_gen_start_tok + is_auto_gen_end_tok
        SP_mask = torch.cumsum(SP_toks.to(torch.int), dim=0) % 2 == 1
        return SP_mask
    
    def get_gold(self):
        gold_trajectory = f'[BOS]What is {self.a}+{self.b}?[SP]{self.a}+{self.b}>>>{self.a+self.b}[NL][ESP]{self.a+self.b}[EOS]'
        self.gold_state = self.tokenizer.encode(gold_trajectory).ids
        trainable_tokens_mask = ~self.getAutoGeneratedMask(torch.tensor(self.gold_state))
        trainable_tokens_mask[:len(self.current_state)] = False
        
        return {"gold_state":self.gold_state, "trainable_tokens_mask":[True]*len(self.gold_state)}
            
    
    def reset(self):
        self.action_count = 0
        self.ep_count += 1
        
        a = random.randrange(0,self.max_val)
        b = random.randrange(0,self.max_val)
        self.a = a
        self.b = b
        current_string = f'[BOS]What is {a}+{b}?'
        self.current_state = self.tokenizer.encode(current_string).ids
        self.answer = f'[BOS]What is {a}+{b}?{a+b}[EOS]'
        
        pad_width = self.max_token_length - len(self.current_state)
        if self.padding and pad_width > 0:
            next_state = np.pad(self.current_state, (0,pad_width))
        else:
            next_state = self.current_state
        
        return next_state