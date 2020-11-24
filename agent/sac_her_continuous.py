import os
import random as R
import numpy as np
import torch as T
import torch.nn.functional as F
from torch.optim.adam import Adam
from agent.utils.normalizer import Normalizer
from agent.utils.networks import StochasticActor, Critic
from agent.utils.replay_buffer import HindsightReplayBuffer


class HindsightSACAgent(object):
    def __init__(self, env_params, transition_namedtuple, path=None, seed=0, hindsight=True,
                 memory_capacity=int(1e6), optimization_steps=40, tau=0.02, batch_size=128,
                 discount_factor=0.98, learning_rate=0.0004, alpha=0.5, alpha_learning_rate=0.01, update_delay_steps=2):
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

        self.state_dim = env_params['obs_dims']
        self.goal_dim = env_params['goal_dims']
        self.action_dim = env_params['action_dims']
        self.action_max = env_params['action_max']

        self.normalizer = Normalizer(self.state_dim+self.goal_dim,
                                     env_params['init_input_means'], env_params['init_input_var'])
        self.hindsight = hindsight
        self.buffer = HindsightReplayBuffer(memory_capacity, transition_namedtuple, sampled_goal_num=4, seed=seed)
        self.batch_size = batch_size
        self.optimization_steps = optimization_steps
        self.gamma = discount_factor

        self.actor = StochasticActor(self.state_dim+self.goal_dim, self.action_dim,
                                     log_std_min=-20, log_std_max=2).to(self.device)
        self.actor_optimizer = Adam(self.actor.parameters(), lr=learning_rate)

        self.critic_1 = Critic(self.state_dim+self.goal_dim+self.action_dim, 1).to(self.device)
        self.critic_target_1 = Critic(self.state_dim+self.goal_dim+self.action_dim, 1).to(self.device)
        self.critic_optimizer_1 = Adam(self.critic_1.parameters(), lr=learning_rate)
        self.critic_2 = Critic(self.state_dim+self.goal_dim+self.action_dim, 1).to(self.device)
        self.critic_target_2 = Critic(self.state_dim+self.goal_dim+self.action_dim, 1).to(self.device)
        self.critic_optimizer_2 = Adam(self.critic_2.parameters(), lr=learning_rate)
        self.tau = tau
        self.critic_target_soft_update(tau=1)

        self.actor_and_target_update_delay_count = 0
        self.actor_and_target_update_delay_steps = update_delay_steps

        self.alpha = alpha
        self.target_entropy = -T.prod(T.tensor(self.action_dim, dtype=T.float).to(self.device)).item()
        self.log_alpha = T.zeros(1, requires_grad=True, device=self.device)
        self.alpha_optimizer = Adam([self.log_alpha], lr=alpha_learning_rate)

    def select_action(self, state, desired_goal):
        inputs = np.concatenate((state, desired_goal), axis=0)
        inputs = self.normalizer(inputs)
        inputs = T.tensor(inputs, dtype=T.float).to(self.device)
        self.actor.eval()
        return self.actor.get_action(inputs).detach().cpu().numpy()

    def remember(self, new_episode, *args):
        self.buffer.store_experience(new_episode, *args)

    def learn(self, steps=None, batch_size=None):
        if self.hindsight:
            self.buffer.modify_episodes()
        self.buffer.store_episodes()
        if batch_size is None:
            batch_size = self.batch_size
        if len(self.buffer) < batch_size:
            return
        if steps is None:
            steps = self.optimization_steps

        for i in range(steps):
            batch = self.buffer.sample(batch_size)
            actor_inputs = np.concatenate((batch.state, batch.desired_goal), axis=1)
            actor_inputs = self.normalizer(actor_inputs)
            actor_inputs = T.tensor(actor_inputs, dtype=T.float32).to(self.device)
            actions = T.tensor(batch.action, dtype=T.float32).to(self.device)
            actor_inputs_ = np.concatenate((batch.next_state, batch.desired_goal), axis=1)
            actor_inputs_ = self.normalizer(actor_inputs_)
            actor_inputs_ = T.tensor(actor_inputs_, dtype=T.float32).to(self.device)
            rewards = T.tensor(batch.reward, dtype=T.float32).unsqueeze(1).to(self.device)
            done = T.tensor(batch.done, dtype=T.float32).unsqueeze(1).to(self.device)

            self.critic_target_1.eval()
            self.critic_target_2.eval()
            self.actor.eval()
            self.critic_1.train()
            self.critic_2.train()
            actions_, log_probs_ = self.actor.get_action(actor_inputs_, probs=True)

            critic_inputs = T.cat((actor_inputs, actions), dim=1).to(self.device)
            value_estimate_1 = self.critic_1(critic_inputs)
            value_estimate_2 = self.critic_2(critic_inputs)
            critic_inputs_ = T.cat((actor_inputs_, actions_), dim=1).to(self.device)
            value_1_ = self.critic_target_1(critic_inputs_)
            value_2_ = self.critic_target_2(critic_inputs_)
            value_ = T.min(value_1_, value_2_) - (self.alpha*log_probs_)
            value_target = rewards + done*self.gamma*value_
            critic_loss_1 = F.mse_loss(value_estimate_1, value_target.detach())
            self.critic_optimizer_1.zero_grad()
            critic_loss_1.backward()
            self.critic_optimizer_1.step()

            critic_loss_2 = F.mse_loss(value_estimate_2, value_target.detach())
            self.critic_optimizer_2.zero_grad()
            critic_loss_2.backward()
            self.critic_optimizer_2.step()

            self.critic_1.eval()
            self.critic_2.eval()
            self.actor.train()
            new_actions, new_log_probs = self.actor.get_action(actor_inputs, probs=True)

            self.actor_and_target_update_delay_count = \
                (self.actor_and_target_update_delay_count+1) % self.actor_and_target_update_delay_steps
            if self.actor_and_target_update_delay_count == self.actor_and_target_update_delay_steps:
                self.critic_target_soft_update()

                critic_eval_inputs = T.cat((actor_inputs, new_actions), dim=1).to(self.device)
                new_values = T.min(self.critic_1(critic_eval_inputs), self.critic_2(critic_eval_inputs))
                actor_loss = self.alpha*new_log_probs - new_values
                actor_loss = actor_loss.mean()
                self.actor_optimizer.zero_grad()
                actor_loss.backward()
                self.actor_optimizer.step()
                self.actor.eval()

            alpha_loss = (self.log_alpha * (-new_log_probs - self.target_entropy).detach()).mean()
            self.alpha_optimizer.zero_grad()
            alpha_loss.backward()
            self.alpha_optimizer.step()
            self.alpha = self.log_alpha.exp()

    def critic_target_soft_update(self, tau=None):
        if tau is None:
            tau = self.tau
        for target_param, param in zip(self.critic_target_1.parameters(), self.critic_1.parameters()):
            target_param.data.copy_(
                target_param.data * (1.0 - tau) + param.data * tau
            )
        for target_param, param in zip(self.critic_target_2.parameters(), self.critic_2.parameters()):
            target_param.data.copy_(
                target_param.data * (1.0 - tau) + param.data * tau
            )

    def save_networks(self, epoch):
        T.save(self.actor.state_dict(), self.ckpt_path+'/ckpt_actor_epoch'+str(epoch)+'.pt')
        T.save(self.critic_1.state_dict(), self.ckpt_path+'/ckpt_critic_1_epoch'+str(epoch)+'.pt')
        T.save(self.critic_target_1.state_dict(), self.ckpt_path+'/ckpt_critic_1_target_epoch'+str(epoch)+'.pt')
        T.save(self.critic_2.state_dict(), self.ckpt_path+'/ckpt_critic_2_epoch'+str(epoch)+'.pt')
        T.save(self.critic_target_2.state_dict(), self.ckpt_path+'/ckpt_critic_2_target_epoch'+str(epoch)+'.pt')

    def load_network(self, epoch):
        self.actor.load_state_dict(T.load(self.ckpt_path+'/ckpt_actor_epoch'+str(epoch)+'.pt'))
        self.critic_1.load_state_dict(T.load(self.ckpt_path + '/ckpt_critic_1_epoch' + str(epoch) + '.pt'))
        self.critic_target_1.load_state_dict(T.load(self.ckpt_path+'/ckpt_critic_target_1_epoch'+str(epoch)+'.pt'))
        self.critic_2.load_state_dict(T.load(self.ckpt_path + '/ckpt_critic_2_epoch' + str(epoch) + '.pt'))
        self.critic_target_2.load_state_dict(T.load(self.ckpt_path+'/ckpt_critic_target_2_epoch'+str(epoch)+'.pt'))
