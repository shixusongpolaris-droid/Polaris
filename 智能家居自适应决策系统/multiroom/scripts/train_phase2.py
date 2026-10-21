"""Phase 2：2 房间 PPO 训练（MultiDiscrete([8,8])）。

用法：
  python train_phase2.py --seeds 42,66,2026,7,123 --energy-scales 10,30,60 --timesteps 300000
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))

import torch  # noqa: E402
from stable_baselines3 import PPO  # noqa: E402
from stable_baselines3.common.vec_env import (  # noqa: E402
    DummyVecEnv, VecNormalize)

from gym_env import GymMultiRoomEnv  # noqa: E402

OUT = ROOT / "multiroom" / "models" / "ppo"


def train_one(es, seed, timesteps):
    env = GymMultiRoomEnv(energy_scale=es, seed=seed)
    venv = DummyVecEnv([lambda: env])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)
    torch.set_num_threads(8)
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = PPO(
        "MlpPolicy", venv,
        n_steps=1024, batch_size=256, learning_rate=3e-4,
        gamma=0.99, ent_coef=0.01,
        policy_kwargs=dict(net_arch=[128, 128]),
        seed=seed, verbose=0, device="cpu",
    )
    t0 = time.perf_counter()
    model.learn(total_timesteps=timesteps)
    dt = time.perf_counter() - t0
    mdir = OUT / f"es{es:g}_s{seed}"
    mdir.mkdir(parents=True, exist_ok=True)
    model.save(str(mdir / "model"))
    np.savez(mdir / "norm_stats.npz",
             mean=venv.obs_rms.mean, var=venv.obs_rms.var)
    print(f"es={es:g} seed={seed} steps={timesteps} "
          f"train={dt:.0f}s -> {mdir}", flush=True)
    return dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="42,66,2026,7,123")
    ap.add_argument("--energy-scales", default="10,30,60")
    ap.add_argument("--timesteps", type=int, default=300_000)
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    scales = [float(s) for s in args.energy_scales.split(",") if s.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"PPO 2-room: {len(scales) * len(seeds)} models, "
          f"{args.timesteps:,} steps each", flush=True)
    total = 0.0
    for es in scales:
        for seed in seeds:
            total += train_one(es, seed, args.timesteps)
    print(f"ALL DONE {len(scales) * len(seeds)} models, "
          f"{total / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
