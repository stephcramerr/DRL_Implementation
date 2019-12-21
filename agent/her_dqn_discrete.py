import os
import numpy as np
import torch as T
import random as R
import torch.nn.functional as F
from torch.optim.adam import Adam
from agent.utils.networks import Critic
from agent.utils.replay_buffer import GridWorldHindsightReplayBuffer
from agent.utils.exploration_strategy import ExpDecayGreedy


class HindsightDQN(object):
    def __init__(self, env_params, tr_namedtuple, path=None, seed=0, hindsight=True,
                 lr=1e-4, mem_capacity=int(1e6), batch_size=512, tau=0.5, clip_value=5.0,
                 optimization_steps=2, gamma=0.99, eps_start=1, eps_end=0.05, eps_decay=5000):
        T.manual_seed(seed)
        R.seed(seed)
        if path is None:
            self.ckpt_path = "ckpts"
        else:
            self.ckpt_path = path+"/ckpts"
        if not os.path.isdir(self.ckpt_path):
            os.mkdir(self.ckpt_path)
        use_cuda = T.cuda.is_available()
        self.device = T.device("cuda" if use_cuda else "cpu")

        self.input_dim = env_params['input_dim']
        self.output_dim = env_params['output_dim']
        self.max = env_params['max']
        self.input_max = env_params['input_max']
        self.input_max_no_inv = np.delete(self.input_max, [n for n in range(4, len(self.input_max))], axis=0)
        self.input_min = env_params['input_min']
        self.input_min_no_inv = np.delete(self.input_min, [n for n in range(4, len(self.input_min))], axis=0)

        self.exploration = ExpDecayGreedy(start=eps_start, end=eps_end, decay=eps_decay)
        self.agent = Critic(self.input_dim, self.output_dim).to(self.device)
        self.target = Critic(self.input_dim, self.output_dim).to(self.device)
        self.optimizer = Adam(self.agent.parameters(), lr=lr)
        self.hindsight = hindsight
        self.memory = GridWorldHindsightReplayBuffer(mem_capacity, tr_namedtuple, seed=seed)
        self.batch_size = batch_size

        self.gamma = gamma
        self.clip_value = clip_value
        self.soft_update(tau=1)
        self.tau = tau
        self.optimization_steps = optimization_steps

    def select_action(self, obs, ep=None):
        inputs = np.concatenate((obs['state'], obs['desired_goal_loc'], obs['inventory_vector']), axis=0)
        inputs = self.scale(inputs)
        inputs = T.tensor(inputs, dtype=T.float).to(self.device)
        self.target.eval()
        values = self.target(inputs)
        if ep is None:
            action = T.argmax(values).item()
            return action
        else:
            _ = R.uniform(0, 1)
            if _ < self.exploration(ep):
                action = R.randint(0, self.max)
            else:
                action = T.argmax(values).item()
            return action

    def remember(self, new, *args):
        self.memory.store_experience(new, *args)

    def learn(self, steps=None, batch_size=None):
        if self.hindsight:
            self.memory.modify_episodes()
        self.memory.store_episodes()
        if steps is None:
            steps = self.optimization_steps
        if batch_size is None:
            batch_size = self.batch_size
        if len(self.memory) < batch_size:
            return

        for s in range(steps):
            batch = self.memory.sample(batch_size)
            inputs = np.concatenate((batch.state, batch.desired_goal, batch.inventory), axis=1)
            inputs_ = np.concatenate((batch.next_state, batch.next_goal, batch.next_inventory), axis=1)
            inputs = self.scale(inputs)
            inputs_ = self.scale(inputs_)
            inputs = T.tensor(inputs, dtype=T.float).to(self.device)
            inputs_ = T.tensor(inputs_, dtype=T.float).to(self.device)

            actions = T.tensor(batch.action, dtype=T.long).unsqueeze(1).to(self.device)
            rewards = T.tensor(batch.reward, dtype=T.float).unsqueeze(1).to(self.device)
            episode_done = T.tensor(batch.done, dtype=T.float).unsqueeze(1).to(self.device)

            self.agent.train()
            self.target.eval()
            estimated_values = self.agent(inputs).gather(1, actions)
            maximal_next_values = self.target(inputs_).max(1)[0].view(batch_size, 1)
            target_values = rewards + episode_done*self.gamma*maximal_next_values
            target_values = T.clamp(target_values, 0.0, self.clip_value)

            self.optimizer.zero_grad()
            loss = F.smooth_l1_loss(estimated_values, target_values)
            loss.backward()
            self.optimizer.step()
            self.agent.eval()
            self.soft_update()

    def soft_update(self, tau=None):
        if tau is None:
            tau = self.tau
        for target_param, param in zip(self.target.parameters(), self.agent.parameters()):
            target_param.data.copy_(
                target_param.data * (1.0 - tau) + param.data * tau
            )

    def save_network(self, epo):
        T.save(self.agent.state_dict(), self.ckpt_path+"/ckpt_agent_epo"+str(epo)+".pt")
        T.save(self.target.state_dict(), self.ckpt_path+"/ckpt_target_epo"+str(epo)+".pt")

    def load_network(self, epo):
        self.agent.load_state_dict(T.load(self.ckpt_path+'/ckpt_agent_epo' + str(epo)+'.pt'))
        self.target.load_state_dict(T.load(self.ckpt_path+'/ckpt_target_epo'+str(epo)+'.pt'))

    def scale(self, inputs):
        ins = (inputs - self.input_min) / (self.input_max - self.input_min)
        return ins