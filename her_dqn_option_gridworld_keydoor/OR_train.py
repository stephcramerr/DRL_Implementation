import os
from envs.gridworld_one_room import OneRoom
from her_dqn_option_gridworld_keydoor.trainer import Trainer
path = os.path.dirname(os.path.realpath(__file__))

setup = {
    'locked_room_height': 15,
    'locked_room_width': 3,
    'locked_room_num': 2,
    'hall_height': 20,
}
folder = '/OneRoom'
path += folder
if not os.path.isdir(path):
    os.mkdir(path)
env = OneRoom(setup, seed=2222)
trainer = Trainer(env, path, training_epoch=201, torch_seed=300, random_seed=300)
trainer.print_training_info()
trainer.run()