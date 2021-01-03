import os
import plot
import pybullet_multigoal_gym as pgm
from agent import DDPGHer
algo_params = {
    'hindsight': True,
    'clip_value': 50,
    'prioritised': True,
    'memory_capacity': int(1e6),
    'learning_rate': 0.001,
    'update_interval': 1,
    'batch_size': 128,
    'optimization_steps': 40,
    'tau': 0.05,
    'discount_factor': 0.98,
    'discard_time_limit': True,

    'random_action_chance': 0.2,
    'noise_deviation': 0.05,

    'training_epochs': 51,
    'training_cycles': 50,
    'training_episodes': 16,
    'testing_gap': 1,
    'testing_episodes': 30,
    'saving_gap': 25,
}
seeds = [11, 22, 33, 44, 55, 66]
seed_returns = []
seed_success_rates = []
path = os.path.dirname(os.path.realpath(__file__))
path = os.path.join(path, 'Reach_HER')

for seed in seeds:

    env = pgm.make("KukaReachSparseEnv-v0")

    seed_path = path + '/seed'+str(seed)

    agent = DDPGHer(algo_params=algo_params, env=env, path=seed_path, seed=seed)
    agent.run(test=False)

    seed_returns.append(agent.statistic_dict['epoch_test_return'])
    seed_success_rates.append(agent.statistic_dict['epoch_test_success_rate'])

return_statistic = plot.get_mean_and_deviation(seed_returns, save_data=True,
                                               file_name=os.path.join(path, 'return_statistic.json'))
plot.smoothed_plot_mean_deviation(path + '/returns.png', return_statistic, x_label='Cycle', y_label='Average returns')


success_rate_statistic = plot.get_mean_and_deviation(seed_success_rates, save_data=True,
                                                     file_name=os.path.join(path, 'success_rate_statistic.json'))
plot.smoothed_plot_mean_deviation(path + '/success_rates.png', success_rate_statistic,
                                  x_label='Cycle', y_label='Success rates')