import os
from Atari.trainer import Trainer

trainer = Trainer(path=os.path.dirname(os.path.realpath(__file__)), env='Breakout-v0')
trainer.warm_start()
trainer.train(render=False)