# -*- coding: utf-8 -*-
"""RED_linear_run1.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1-WN1MY9YYluGcnigLgrndqsxcOYldbB6
"""

#@title mount your Google Drive
#@markdown Your work will be stored in a folder called `cs285_f2021` by default to prevent Colab instance timeouts from deleting your edits.

import os
from google.colab import drive
drive.mount('/content/gdrive')

# Commented out IPython magic to ensure Python compatibility.
#@title set up mount symlink

DRIVE_PATH = '/content/gdrive/My\ Drive/cs285_project'
DRIVE_PYTHON_PATH = DRIVE_PATH.replace('\\', '')
if not os.path.exists(DRIVE_PYTHON_PATH):
#   %mkdir $DRIVE_PATH

## the space in `My Drive` causes some issues,
## make a symlink to avoid this
SYM_PATH = '/content/cs285_project'
if not os.path.exists(SYM_PATH):
  !ln -s $DRIVE_PATH $SYM_PATH

!apt update 
!apt install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        gnupg2 \
        make \
        cmake \
        ffmpeg \
        swig \
        libz-dev \
        unzip \
        zlib1g-dev \
        libglfw3 \
        libglfw3-dev \
        libxrandr2 \
        libxinerama-dev \
        libxi6 \
        libxcursor-dev \
        libgl1-mesa-dev \
        libgl1-mesa-glx \
        libglew-dev \
        libosmesa6-dev \
        lsb-release \
        ack-grep \
        patchelf \
        wget \
        xpra \
        xserver-xorg-dev \
        xvfb \
        python-opengl \
        ffmpeg

# Commented out IPython magic to ensure Python compatibility.
#@title download mujoco

MJC_PATH = '{}/mujoco'.format(SYM_PATH)
# %mkdir $MJC_PATH
# %cd $MJC_PATH
!wget -q https://www.roboti.us/download/mujoco200_linux.zip
!unzip -q mujoco200_linux.zip
# %mv mujoco200_linux mujoco200
# %rm mujoco200_linux.zip

#@title update mujoco paths

import os

os.environ['LD_LIBRARY_PATH'] += ':{}/mujoco200/bin'.format(MJC_PATH)
os.environ['MUJOCO_PY_MUJOCO_PATH'] = '{}/mujoco200'.format(MJC_PATH)
os.environ['MUJOCO_PY_MJKEY_PATH'] = '{}/mjkey.txt'.format(MJC_PATH)

## installation on colab does not find *.so files
## in LD_LIBRARY_PATH, copy over manually instead
!cp $MJC_PATH/mujoco200/bin/*.so /usr/lib/x86_64-linux-gnu/

# Commented out IPython magic to ensure Python compatibility.
# %cd $MJC_PATH
!git clone https://github.com/openai/mujoco-py.git
# %cd mujoco-py
# %pip install -e .

## cythonize at the first import
import mujoco_py

# Commented out IPython magic to ensure Python compatibility.
# %cd $SYM_PATH
# %cd RED
# %tensorflow_version 1.x
! pip install mpi4py

'''
Disclaimer: this code is highly based on trpo_mpi at @openai/baselines and @openai/imitation
'''

import argparse
import os.path as osp
import logging
from mpi4py import MPI
from tqdm import tqdm

import numpy as np
import gym

from baselines.rnd_gail import mlp_policy
from baselines.common import set_global_seeds, tf_util as U
from baselines.common.misc_util import boolean_flag
from baselines import bench
from baselines import logger
from baselines.rnd_gail.merged_critic import make_critic

import pickle

def get_exp_data(expert_path):
    with open(expert_path, 'rb') as f:
        data = pickle.loads(f.read())

        data["actions"] = np.squeeze(data["actions"])
        data["observations"] = data["observations"]

        # print(data["observations"].shape)
        # print(data["actions"].shape)
        return [data["observations"], data["actions"]]


Log_dir = osp.expanduser("~/workspace/log/mujoco")
Checkpoint_dir = osp.expanduser("~/workspace/checkpoint/mujoco")

def get_task_name(args):
    task_name = args.env_id.split("-")[0]
    if args.pretrained:
        task_name += "pretrained."
    task_name +="gamma_%f." % args.gamma
    task_name += ".seed_" + str(args.seed)
    task_name += ".reward_" + str(args.reward)
    task_name += "kl_" + str(args.max_kl)
    task_name += "g_"+str(args.g_step)

    return task_name


def modify_args(args):
    #task specific parameters
    if args.reward<2:
        rnd_iter = 200
        dyn_norm = False

        if args.env_id == "Reacher-v2":
            rnd_iter = 300
            args.gamma = 0.99

        if args.env_id == "HalfCheetah-v2":
            args.pretrained = True


        if args.env_id == "Walker2d-v2":
            args.fixed_var = False

        if args.env_id == "Ant-v2":
            args.pretrained = True
            args.BC_max_iter = 10
            args.fixed_var = False
        return args, rnd_iter, dyn_norm
    else:
        if args.env_id == "Hopper-v2":
            args.gamma = 0.99
            dyn_norm = False

        if args.env_id == "Reacher-v2":
            dyn_norm = True

        if args.env_id == "HalfCheetah-v2":
            dyn_norm = True

        if args.env_id == "Walker2d-v2":
            args.gamma = 0.99
            dyn_norm = True

        if args.env_id == "Ant-v2":
            args.gamma = 0.99
            dyn_norm = False

        return args, 0, dyn_norm

parser = argparse.ArgumentParser("Tensorflow Implementation of GAIL")
    parser.add_argument('--env_id', help='environment ID', default="Hopper-v2")
    parser.add_argument('--seed', help='RNG seed', type=int, default=0)
    parser.add_argument('--checkpoint_dir', help='the directory to save model', default=Checkpoint_dir)
    parser.add_argument('--log_dir', help='the directory to save log file', default=Log_dir)
    parser.add_argument('--load_model_path', help='if provided, load the model', type=str, default=None)
    # Task
    parser.add_argument('--task', type=str, choices=['train', 'evaluate', 'sample'], default='train')
    # for evaluatation
    boolean_flag(parser, 'stochastic_policy', default=False, help='use stochastic/deterministic policy to evaluate')
    # Optimization Configuration
    parser.add_argument('--g_step', help='number of steps to train policy in each epoch', type=int, default=3)
    parser.add_argument('--d_step', help='number of steps to train discriminator in each epoch', type=int, default=1)
    # Network Configuration (Using MLP Policy)
    parser.add_argument('--policy_hidden_size', type=int, default=100)
    parser.add_argument('--adversary_hidden_size', type=int, default=100)
    # Algorithms Configuration
    parser.add_argument('--max_kl', type=float, default=0.01)
    parser.add_argument('--policy_entcoeff', help='entropy coefficiency of policy', type=float, default=0)
    parser.add_argument('--adversary_entcoeff', help='entropy coefficiency of discriminator', type=float, default=1e-3)
    # Traing Configuration
    parser.add_argument('--num_timesteps', help='number of timesteps per episode', type=int, default=5e6)
    # Behavior Cloning
    boolean_flag(parser, 'pretrained', default=False, help='Use BC to pretrain')
    boolean_flag(parser, 'fixed_var', default=False, help='Fixed policy variance')
    parser.add_argument('--BC_max_iter', help='Max iteration for training BC', type=int, default=20)
    parser.add_argument('--gamma', help='Discount factor', type=float, default=0.97)
    boolean_flag(parser, 'popart', default=True, help='Use popart on V function')
    parser.add_argument('--reward', help='Reward Type', type=int, default=0)

args = parser.parse_args(args=[])

set_global_seeds(args.seed)
    env = gym.make(args.env_id)
    env.seed(args.seed)

    # env = bench.Monitor(env, logger.get_dir() and
    #                     osp.join(logger.get_dir(), "monitor.json"))


    gym.logger.setLevel(logging.WARN)

    if args.log_dir != Log_dir:
        log_dir = osp.join(Log_dir, args.log_dir)
        save_dir = osp.join(Checkpoint_dir, args.log_dir)
    else:
        log_dir = Log_dir
        save_dir = Checkpoint_dir

    args, rnd_iter, dyn_norm = modify_args(args)
    def policy_fn(name, ob_space, ac_space,):
        return mlp_policy.MlpPolicy(name=name, ob_space=ob_space, ac_space=ac_space,
                                    hid_size=args.policy_hidden_size, num_hid_layers=2, popart=args.popart, gaussian_fixed_var=args.fixed_var)

exp_data = get_exp_data("/content/gdrive/My Drive/cs285_project/RED/data/Hopper-v2.pkl")
task_name = get_task_name(args)
logger.configure(dir=log_dir, log_suffix=task_name, format_strs=["log", "stdout"])

class RND_Critic_Revise(object):
    def __init__(self, W, sigma_hat, ob_size, ac_size, rnd_hid_size=128, rnd_hid_layer=4, hid_size=128, hid_layer=1,
                 out_size=128, scale=250000.0, offset=0., reward_scale=1.0, scope="rnd"):
        self.scope = scope
        self.scale = scale
        self.offset = offset
        self.out_size = out_size
        self.rnd_hid_size = rnd_hid_size
        self.rnd_hid_layer = rnd_hid_layer
        self.hid_size = hid_size
        self.hid_layer = hid_layer
        self.reward_scale = reward_scale
        self.W = W
        self.sigma_hat = sigma_hat
        print("RND Critic")

        ob = tf.placeholder(tf.float32, [None, ob_size])
        ac = tf.placeholder(tf.float32, [None, ac_size])
        lr = tf.placeholder(tf.float32, None)


        feat = self.build_graph(ob, ac, self.scope, hid_layer, hid_size, out_size)
        rnd_feat = self.build_graph(ob, ac, self.scope+"_rnd", rnd_hid_layer, rnd_hid_size, out_size)

        feat_loss = tf.reduce_mean(tf.square(feat-rnd_feat))
        self.reward = reward_scale*tf.exp(offset- tf.reduce_mean(tf.square(feat - rnd_feat), axis=-1) * self.scale)

        rnd_loss = tf.reduce_mean(tf.square(feat - rnd_feat), axis=-1) * self.scale
        # self.reward = reward_scale * tf.exp(offset - rnd_loss)
        # self.reward = reward_scale * (tf.math.softplus(rnd_loss) - rnd_loss)
        self.reward_func = U.function([ob, ac], self.reward)
        self.raw_reward = U.function([ob, ac], rnd_loss)

        self.trainer = tf.train.AdamOptimizer(learning_rate=lr)

        gvs = self.trainer.compute_gradients(feat_loss, self.get_trainable_variables())

        self._train = U.function([ob, ac, lr], [], updates=[self.trainer.apply_gradients(gvs)])

    def build_graph(self, ob, ac, scope, hid_layer, hid_size, size):
        with tf.variable_scope(scope, reuse=tf.AUTO_REUSE):
            layer = tf.concat([ob, ac], axis=1)
            for _ in range(hid_layer):
                layer = tf.layers.dense(layer, hid_size, activation=tf.nn.leaky_relu)
            layer = tf.layers.dense(layer, size, activation=None)
        return layer

    def build_reward_op(self, ob, ac):
        feat = self.build_graph(ob, ac, self.scope, self.hid_layer, self.hid_size, self.out_size)
        rnd_feat = self.build_graph(ob, ac, self.scope + "_rnd", self.rnd_hid_layer, self.rnd_hid_size
                                    , self.out_size)

        reward = self.reward_scale* tf.exp(self.offset- tf.reduce_mean(tf.square(feat - rnd_feat), axis=-1) * self.scale)
        return reward

    def get_trainable_variables(self):
        return tf.trainable_variables(self.scope)


    def get_reward(self, ob, ac):
        # return self.reward_func(ob, ac)
        x = np.concatenate([ob.reshape([-1,1]),ac.reshape([-1,1])],axis = 0)
        # calculate prediction variance and multiply it by 5 for rescaling. We can also change the multiplier to the other constant for tuning.
        var = sigma_hat*np.sqrt(np.matmul(np.matmul(x.T,W),x))*5
        return np.exp(-var**2)
    
    def get_raw_reward(self, ob, ac):
        return self.raw_reward(ob, ac)

    def train(self, ob, ac, batch_size=32, lr=0.001, iter=200):
        logger.info("Training RND Critic")
        # for _ in range(iter):
        #     for data in iterbatches([ob, ac], batch_size=batch_size, include_final_partial_batch=True):
        #         self._train(*data, lr)

import numpy as np
import tensorflow as tf
from baselines.common import tf_util as U
from baselines.common.dataset import iterbatches
from baselines import logger

hid_size=128
rnd_hid_size=128 
reward_type=0
scale=250000
reward_type=args.reward

ac_size = env.action_space.sample().shape[0]
ob_size = env.observation_space.shape[0]

# linear model to estimate variance
X1 = exp_data[0]
X2 = exp_data[1] 
X = np.concatenate([X1,X2],axis=1)
np.random.seed(1)
# randomly create a oracle linear model to estimate
param = np.random.normal(0,1,14).reshape([-1,1])
# calculate response under this oracle model
Y = np.matmul(X,param).flatten() + np.random.normal(0,1,X.shape[0])
# estimate the linear model
beta_hat = np.matmul(np.linalg.inv(np.matmul(X.T,X)),np.matmul(X.T,Y))
# estimate varaince
sigma_hat = np.sqrt(np.sum((Y-np.matmul(X,beta_hat))**2)/(X.shape[0]-14))
# calculate a matrix for later use
W = np.linalg.inv(np.matmul(X.T,X))


critic = RND_Critic_Revise(W, sigma_hat, ob_size, ac_size, hid_size=hid_size, rnd_hid_size=rnd_hid_size, scale=scale)



import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from scipy.ndimage.filters import gaussian_filter

def generate_density_plot(x,y):
    def myplot(x, y, s, bins=1000):
        heatmap, xedges, yedges = np.histogram2d(x, y, bins=bins)
        heatmap = gaussian_filter(heatmap, sigma=s)

        extent = [xedges[0], xedges[-1], yedges[0], yedges[-1]]
        return heatmap.T, extent


    fig, axs = plt.subplots(nrows=2,ncols=2,figsize=(10,10))

    sigmas = [0, 16, 32, 64]

    for ax, s in zip(axs.flatten(), sigmas):
        if s == 0:
            ax.plot(x, y, 'k.', markersize=3)
            ax.set_title("Scatter plot")
        else:
            img, extent = myplot(x, y, s)
            ax.imshow(img, extent=extent, origin='lower', cmap=cm.jet)
            ax.set_title("Smoothing with  $\sigma$ = %d" % s)

    plt.show()

X1 = exp_data[0]
X2 = exp_data[1] 
generate_density_plot(X2[:,0],X2[:,1])



from matplotlib import pyplot as plt, cm, colors
import numpy as np

plt.rcParams["figure.figsize"] = [7.00, 3.50]
plt.rcParams["figure.autolayout"] = True

X1 = exp_data[0]
X2 = exp_data[1] 
X = np.concatenate([X1,X2],axis=1)

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)

N=100
side = np.linspace(-4, 6, N)
x, y = np.meshgrid(side, side)

z= np.zeros([N,N])
for i in range(N):
    for j in range(N):
        ac[0] = x[0,i]
        ac[1] = y[j,0]
        z[i,j] = critic.get_reward(ob,ac).flatten()[0]


plt.pcolormesh(x, y, z, shading='auto')

plt.show()

from matplotlib import pyplot as plt, cm, colors
import numpy as np

plt.rcParams["figure.figsize"] = [7.00, 3.50]
plt.rcParams["figure.autolayout"] = True

X1 = exp_data[0]
X2 = exp_data[1] 
X = np.concatenate([X1,X2],axis=1)

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)

N=100
side = np.linspace(-1, 3, N)
x, y = np.meshgrid(side, side)

z= np.zeros([N,N])
for i in range(N):
    for j in range(N):
        ob[0] = x[0,i]
        ob[1] = y[j,0]
        z[i,j] = critic.get_reward(ob,ac).flatten()[0]


plt.pcolormesh(x, y, z, shading='auto')

plt.show()

from matplotlib import pyplot as plt, cm, colors
import numpy as np

plt.rcParams["figure.figsize"] = [7.00, 3.50]
plt.rcParams["figure.autolayout"] = True

X1 = exp_data[0]
X2 = exp_data[1] 
X = np.concatenate([X1,X2],axis=1)

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)

N=100
side = np.linspace(-1, 3, N)
x, y = np.meshgrid(side, side)

z= np.zeros([N,N])
for i in range(N):
    for j in range(N):
        ob[2] = x[0,i]
        ob[4] = y[j,0]
        z[i,j] = critic.get_reward(ob,ac).flatten()[0]


plt.pcolormesh(x, y, z, shading='auto')

plt.show()







np.max(X,0)

from matplotlib import pyplot as plt, cm, colors
import numpy as np

plt.rcParams["figure.figsize"] = [7.00, 3.50]
plt.rcParams["figure.autolayout"] = True

X1 = exp_data[0]
X2 = exp_data[1] 
X = np.concatenate([X1,X2],axis=1)

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)

fig,ax=plt.subplots(nrows=5,ncols=3,figsize=(10,12))
axes = ax.flatten()

N=100
count = 0
for p in range(6):
    for q in range(6):
        if p<q:
            max_value = np.max([np.max(X,0)[p],np.max(X,0)[q]])+1
            min_value = np.max([np.min(X,0)[p],np.min(X,0)[q]])-1        
            side = np.linspace(min_value, max_value, N)
            x, y = np.meshgrid(side, side)

            z= np.zeros([N,N])
            for i in range(N):
                for j in range(N):
                    ob[p] = x[0,i]
                    ob[q] = y[j,0]
                    z[i,j] = critic.get_reward(ob,ac).flatten()[0]

            ## figure 1.1
            axes[count].pcolormesh(x, y, z, shading='auto')
            axes[count].set_title("dim "+str(p+1) + " vs " + "dim " + str(q+1), fontsize=14)
            count = count +1
plt.show()



import numpy as np
import matplotlib.pyplot as plt 

fig,ax=plt.subplots(nrows=2,ncols=2,figsize=(10,8))
axes = ax.flatten()

## figure 1.1
axes[0].pcolormesh(x, y, z, shading='auto')

plt.show()



















plt.rcParams["figure.figsize"] = [4.50, 3.50]

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)
critic.get_reward(ob,ac)

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)
reward_ls = []
node = 0
for i in range(100):
    ac[node] = i * 0.1 - 5
    reward_ls.append(critic.get_reward(ob,ac).flatten()[0])

import numpy as np
import matplotlib.pyplot as plt 

x = np.array(range(len(reward_ls)))/10 - 5
plt.plot(x, reward_ls,color="limegreen",linestyle='-', markersize=7)
plt.xlabel('Value of the First Dimension of Action', fontsize=12)
plt.ylabel('Reward', fontsize=12)
plt.tight_layout(pad=4)
# plt.title("Linear Model Variance Estimation Based Reward Function \n (Change the First Dimension of Action)")
plt.show()

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)
reward_ls = []
node = 1
for i in range(100):
    ac[node] = i * 0.1 - 5
    reward_ls.append(critic.get_reward(ob,ac).flatten()[0])

import numpy as np
import matplotlib.pyplot as plt 

x = np.array(range(len(reward_ls)))/10 - 5
plt.plot(x, reward_ls,color="limegreen",linestyle='-', markersize=7)
plt.xlabel('Value of the Second Dimension of Action', fontsize=12)
plt.ylabel('Reward', fontsize=12)
plt.tight_layout(pad=4)
# plt.title("Linear Model Variance Estimation Based Reward Function \n (Change the Second Dimension of Action)")
plt.show()

ob = np.mean(X[:,0:11],axis=0)
ac = np.mean(X[:,11:],axis=0)
reward_ls = []
node = 2
for i in range(100):
    ac[node] = i * 0.1 - 5
    reward_ls.append(critic.get_reward(ob,ac).flatten()[0])

import numpy as np
import matplotlib.pyplot as plt 

x = np.array(range(len(reward_ls)))/10 - 5
plt.plot(x, reward_ls,color="limegreen",linestyle='-', markersize=7)
plt.xlabel('Value of the Third Dimension of Action', fontsize=12)
plt.ylabel('Reward', fontsize=12)
plt.tight_layout(pad=4)
plt.title("Linear Model Variance Estimation Based Reward Function \n (Change the Third Dimension of Action)")
plt.show()

# X1 = exp_data[0]
# X2 = exp_data[1] 
# X = np.concatenate([X1,X2],axis=1)
# np.random.seed(1)
# param = np.random.normal(0,1,14).reshape([-1,1])
# Y = np.matmul(X,param).flatten() + np.random.normal(0,1,X.shape[0])
# beta_hat = np.matmul(np.linalg.inv(np.matmul(X.T,X)),np.matmul(X.T,Y))
# sigma_hat = np.sqrt(np.sum((Y-np.matmul(X,beta_hat))**2)/(X.shape[0]-14))
# W = np.linalg.inv(np.matmul(X.T,X))

# scale = 5

# x = np.ones(14).reshape([-1,1])
# var = sigma_hat*np.sqrt(np.matmul(np.matmul(x.T,W),x))*scale
# reward_return = np.exp(-var**2)
# print("var: ", var)
# print("reward: ", reward_return)

# x = X[1,:].reshape([-1,1])
# var = sigma_hat*np.sqrt(np.matmul(np.matmul(x.T,W),x))*scale
# reward_return = np.exp(-var**2)
# print("var: ", var)
# print("reward: ", reward_return)

seed = args.seed
reward_giver = critic
dataset = exp_data
g_step = args.g_step
d_step = args.d_step
policy_entcoeff = args.policy_entcoeff
num_timesteps = args.num_timesteps
checkpoint_dir = save_dir
pretrained = args.pretrained
BC_max_iter = args.BC_max_iter
gamma = args.gamma

pretrained_weight = None

    from baselines.rnd_gail import trpo_mpi
    # Set up for MPI seed
    rank = MPI.COMM_WORLD.Get_rank()
    if rank != 0:
        logger.set_level(logger.DISABLED)
    workerseed = seed + 10000 * MPI.COMM_WORLD.Get_rank()
    set_global_seeds(workerseed)
    env.seed(workerseed)

import time
import os
from contextlib import contextmanager
from mpi4py import MPI
from collections import deque

import tensorflow as tf
import numpy as np

import baselines.common.tf_util as U
from baselines.common import explained_variance, zipsame, dataset, fmt_row
from baselines import logger
from baselines.common.mpi_adam import MpiAdam
from baselines.common.cg import cg
from baselines.gail.statistics import stats
from baselines.common.dataset_plus import iterbatches

env = env
policy_func = policy_fn 
reward_giver = reward_giver
expert_dataset = exp_data
rank =rank
pretrained = pretrained
pretrained_weight = pretrained_weight
g_step = g_step
d_step = d_step
entcoeff = policy_entcoeff
max_timesteps=num_timesteps
ckpt_dir=checkpoint_dir
timesteps_per_batch=1024
max_kl=args.max_kl 
cg_iters=10
cg_damping=0.1
gamma=gamma
lam=0.97
vf_iters=5
vf_stepsize=1e-3
d_stepsize=3e-4
task_name=task_name 
rnd_iter=rnd_iter 
dyn_norm=dyn_norm
mmd=args.reward==2
max_iters=0
callback=None
max_episodes=0

nworkers = MPI.COMM_WORLD.Get_size()
    rank = MPI.COMM_WORLD.Get_rank()
    np.set_printoptions(precision=3)
    # Setup losses and stuff
    # ----------------------------------------
    ob_space = env.observation_space
    ac_space = env.action_space
    pi = policy_func("pi", ob_space, ac_space)
    oldpi = policy_func("oldpi", ob_space, ac_space)
    atarg = tf.placeholder(dtype=tf.float32, shape=[None])  # Target advantage function (if applicable)

    ob = U.get_placeholder_cached(name="ob")
    ac = pi.pdtype.sample_placeholder([None])

    kloldnew = oldpi.pd.kl(pi.pd)
    ent = pi.pd.entropy()
    meankl = tf.reduce_mean(kloldnew)
    meanent = tf.reduce_mean(ent)
    entbonus = entcoeff * meanent

    ratio = tf.exp(pi.pd.logp(ac) - oldpi.pd.logp(ac))  # advantage * pnew / pold
    surrgain = tf.reduce_mean(ratio * atarg)

    optimgain = surrgain + entbonus
    losses = [optimgain, meankl, entbonus, surrgain, meanent]
    loss_names = ["optimgain", "meankl", "entloss", "surrgain", "entropy"]

    dist = meankl

    all_var_list = pi.get_trainable_variables()
    var_list = [v for v in all_var_list if v.name.startswith("pi/pol") or v.name.startswith("pi/logstd")]
    vf_var_list = [v for v in all_var_list if v.name.startswith("pi/vff")]
    vfadam = MpiAdam(vf_var_list)

    get_flat = U.GetFlat(var_list)
    set_from_flat = U.SetFromFlat(var_list)
    klgrads = tf.gradients(dist, var_list)
    flat_tangent = tf.placeholder(dtype=tf.float32, shape=[None], name="flat_tan")
    shapes = [var.get_shape().as_list() for var in var_list]
    start = 0
    tangents = []
    for shape in shapes:
        sz = U.intprod(shape)
        tangents.append(tf.reshape(flat_tangent[start:start+sz], shape))
        start += sz
    gvp = tf.add_n([tf.reduce_sum(g*tangent) for (g, tangent) in zipsame(klgrads, tangents)])  # pylint: disable=E1111
    fvp = U.flatgrad(gvp, var_list)

    assign_old_eq_new = U.function([], [], updates=[tf.assign(oldv, newv)
                                                    for (oldv, newv) in zipsame(oldpi.get_variables(), pi.get_variables())])
    compute_losses = U.function([ob, ac, atarg], losses)
    compute_lossandgrad = U.function([ob, ac, atarg], losses + [U.flatgrad(optimgain, var_list)])
    compute_fvp = U.function([flat_tangent, ob, ac, atarg], fvp)
    compute_vflossandgrad = pi.vlossandgrad

def traj_segment_generator(pi, env, reward_giver, horizon, stochastic):

    # Initialize state variables
    t = 0
    ac = env.action_space.sample()
    new = True
    rew = 0.0
    true_rew = 0.0
    ob = env.reset()

    cur_ep_ret = 0
    cur_ep_len = 0
    cur_ep_true_ret = 0
    ep_true_rets = []
    ep_rets = []
    ep_lens = []

    # Initialize history arrays
    obs = np.array([ob for _ in range(horizon)])
    true_rews = np.zeros(horizon, 'float32')
    rews = np.zeros(horizon, 'float32')
    vpreds = np.zeros(horizon, 'float32')
    news = np.zeros(horizon, 'int32')
    acs = np.array([ac for _ in range(horizon)])
    prevacs = acs.copy()

    while True:
        prevac = ac
        ac, vpred = pi.act(stochastic, ob)
        # Slight weirdness here because we need value function at time T
        # before returning segment [0, T-1] so we get the correct
        # terminal value
        if t > 0 and t % horizon == 0:
            yield {"ob": obs, "rew": rews, "vpred": vpreds, "new": news,
                   "ac": acs, "prevac": prevacs, "nextvpred": vpred * (1 - new),
                   "ep_rets": ep_rets, "ep_lens": ep_lens, "ep_true_rets": ep_true_rets}
            _, vpred = pi.act(stochastic, ob)
            # Be careful!!! if you change the downstream algorithm to aggregate
            # several of these batches, then be sure to do a deepcopy
            ep_rets = []
            ep_true_rets = []
            ep_lens = []
        i = t % horizon
        obs[i] = ob
        vpreds[i] = vpred
        news[i] = new
        acs[i] = ac
        prevacs[i] = prevac

        rew = reward_giver.get_reward(ob, ac)
        ob, true_rew, new, _ = env.step(ac)
        rews[i] = rew
        true_rews[i] = true_rew

        cur_ep_ret += rew
        cur_ep_true_ret += true_rew
        cur_ep_len += 1
        if new:
            ep_rets.append(cur_ep_ret)
            ep_true_rets.append(cur_ep_true_ret)
            ep_lens.append(cur_ep_len)
            cur_ep_ret = 0
            cur_ep_true_ret = 0
            cur_ep_len = 0
            ob = env.reset()
        t += 1


def add_vtarg_and_adv(seg, gamma, lam):
    new = np.append(seg["new"], 0)  # last element is only used for last vtarg, but we already zeroed it if last new = 1
    vpred = np.append(seg["vpred"], seg["nextvpred"])
    T = len(seg["rew"])
    seg["adv"] = gaelam = np.empty(T, 'float32')
    rew = seg["rew"]
    lastgaelam = 0
    for t in reversed(range(T)):
        nonterminal = 1-new[t+1]
        delta = rew[t] + gamma * vpred[t+1] * nonterminal - vpred[t]
        gaelam[t] = lastgaelam = delta + gamma * lam * nonterminal * lastgaelam
    seg["tdlamret"] = seg["adv"] + seg["vpred"]

def flatten_lists(listoflists):
    return [el for list_ in listoflists for el in list_]

def allmean(x):
        assert isinstance(x, np.ndarray)
        out = np.empty_like(x)
        MPI.COMM_WORLD.Allreduce(x, out, op=MPI.SUM)
        out /= nworkers
        return out

    U.initialize()
    th_init = get_flat()
    MPI.COMM_WORLD.Bcast(th_init, root=0)
    set_from_flat(th_init)
    vfadam.sync()
    if rank == 0:
        print("Init param sum", th_init.sum(), flush=True)

    # Prepare for rollouts
    # ----------------------------------------
    seg_gen = traj_segment_generator(pi, env, reward_giver, timesteps_per_batch, stochastic=True)

    episodes_so_far = 0
    timesteps_so_far = 0
    iters_so_far = 0
    tstart = time.time()
    lenbuffer = deque(maxlen=40)  # rolling buffer for episode lengths
    rewbuffer = deque(maxlen=40)  # rolling buffer for episode rewards
    true_rewbuffer = deque(maxlen=40)

    assert sum([max_iters > 0, max_timesteps > 0, max_episodes > 0]) == 1

    ep_stats = stats(["True_rewards", "Rewards", "Episode_length"])
    # if provide pretrained weight
    if pretrained_weight is not None:
        U.load_variables(pretrained_weight, variables=pi.get_variables())
    else:
        if not dyn_norm:
            pi.ob_rms.update(expert_dataset[0])


    if not mmd:
        reward_giver.train(*expert_dataset, iter=rnd_iter)

best = -2000
    save_ind = 0
    max_save = 3
    while True:
        if callback: callback(locals(), globals())
        if max_timesteps and timesteps_so_far >= max_timesteps:
            break
        elif max_episodes and episodes_so_far >= max_episodes:
            break
        elif max_iters and iters_so_far >= max_iters:
            break


        logger.log("********** Iteration %i ************" % iters_so_far)

        def fisher_vector_product(p):
            return allmean(compute_fvp(p, *fvpargs)) + cg_damping * p
        # ------------------ Update G ------------------
        # logger.log("Optimizing Policy...")
        for _ in range(g_step):
            seg = seg_gen.__next__()

            #mmd reward
            if mmd:
                reward_giver.set_b2(seg["ob"], seg["ac"])
                seg["rew"] = reward_giver.get_reward(seg["ob"], seg["ac"])

            #report stats and save policy if any good
            lrlocal = (seg["ep_lens"], seg["ep_rets"], seg["ep_true_rets"])  # local values
            listoflrpairs = MPI.COMM_WORLD.allgather(lrlocal)  # list of tuples
            lens, rews, true_rets = map(flatten_lists, zip(*listoflrpairs))
            true_rewbuffer.extend(true_rets)
            lenbuffer.extend(lens)
            rewbuffer.extend(rews)

            true_rew_avg = np.mean(true_rewbuffer)
            logger.record_tabular("EpLenMean", np.mean(lenbuffer))
            logger.record_tabular("EpRewMean", np.mean(rewbuffer))
            logger.record_tabular("EpTrueRewMean", true_rew_avg)
            logger.record_tabular("EpThisIter", len(lens))
            episodes_so_far += len(lens)
            timesteps_so_far += sum(lens)
            iters_so_far += 1

            logger.record_tabular("EpisodesSoFar", episodes_so_far)
            logger.record_tabular("TimestepsSoFar", timesteps_so_far)
            logger.record_tabular("TimeElapsed", time.time() - tstart)
            logger.record_tabular("Best so far", best)

            # Save model
            if ckpt_dir is not None and true_rew_avg >= best:
                best = true_rew_avg
                fname = os.path.join(ckpt_dir, task_name)
                os.makedirs(os.path.dirname(fname), exist_ok=True)
                pi.save_policy(fname+"_"+str(save_ind))
                save_ind = (save_ind+1) % max_save


            #compute gradient towards next policy
            add_vtarg_and_adv(seg, gamma, lam)
            ob, ac, atarg, tdlamret = seg["ob"], seg["ac"], seg["adv"], seg["tdlamret"]
            vpredbefore = seg["vpred"]  # predicted value function before udpate
            atarg = (atarg - atarg.mean()) / atarg.std()  # standardized advantage function estimate

            if hasattr(pi, "ob_rms") and dyn_norm: pi.ob_rms.update(ob)  # update running mean/std for policy

            args = seg["ob"], seg["ac"], atarg
            fvpargs = [arr[::5] for arr in args]

            assign_old_eq_new()  # set old parameter values to new parameter values
            *lossbefore, g = compute_lossandgrad(*args)
            lossbefore = allmean(np.array(lossbefore))
            g = allmean(g)
            if np.allclose(g, 0):
                logger.log("Got zero gradient. not updating")
            else:
                stepdir = cg(fisher_vector_product, g, cg_iters=cg_iters, verbose=False)
                assert np.isfinite(stepdir).all()
                shs = .5*stepdir.dot(fisher_vector_product(stepdir))
                lm = np.sqrt(shs / max_kl)
                fullstep = stepdir / lm
                expectedimprove = g.dot(fullstep)
                surrbefore = lossbefore[0]
                stepsize = 1.0
                thbefore = get_flat()
                for _ in range(10):
                    thnew = thbefore + fullstep * stepsize
                    set_from_flat(thnew)
                    meanlosses = surr, kl, *_ = allmean(np.array(compute_losses(*args)))
                    improve = surr - surrbefore
                    logger.log("Expected: %.3f Actual: %.3f" % (expectedimprove, improve))
                    if not np.isfinite(meanlosses).all():
                        logger.log("Got non-finite value of losses -- bad!")
                    elif kl > max_kl * 1.5:
                        logger.log("violated KL constraint. shrinking step.")
                    elif improve < 0:
                        logger.log("surrogate didn't improve. shrinking step.")
                    else:
                        logger.log("Stepsize OK!")
                        break
                    stepsize *= .5
                else:
                    logger.log("couldn't compute a good step")
                    set_from_flat(thbefore)
                if nworkers > 1 and iters_so_far % 20 == 0:
                    paramsums = MPI.COMM_WORLD.allgather((thnew.sum(), vfadam.getflat().sum()))  # list of tuples
                    assert all(np.allclose(ps, paramsums[0]) for ps in paramsums[1:])
            if pi.use_popart:
                pi.update_popart(tdlamret)
            for _ in range(vf_iters):
                for (mbob, mbret) in dataset.iterbatches((seg["ob"], seg["tdlamret"]),
                                                         include_final_partial_batch=False, batch_size=128):
                    if hasattr(pi, "ob_rms") and dyn_norm:
                        pi.ob_rms.update(mbob)  # update running mean/std for policy
                    vfadam.update(allmean(compute_vflossandgrad(mbob, mbret)), vf_stepsize)

        g_losses = meanlosses
        for (lossname, lossval) in zip(loss_names, meanlosses):
            logger.record_tabular(lossname, lossval)
        logger.record_tabular("ev_tdlam_before", explained_variance(vpredbefore, tdlamret))
        if rank == 0:
            logger.dump_tabular()



