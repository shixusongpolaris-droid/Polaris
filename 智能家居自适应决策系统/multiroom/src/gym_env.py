"""Gym 包装：2 房间、MultiDiscrete([8,8])、随机场景。

观测 15 维（两房间温度/占用、下一段状态、剩余时间、时间/电价、AC 档位），
规则和 RL 看到同一组信息，避免“规则有 oracle、RL 没有”的不公平。
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from multiroom_env import (
    AC_SETPOINTS, MultiRoomConfig, MultiRoomSim, comfort_band,
)


class GymMultiRoomEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, energy_scale: float = 30.0,
                 episode_steps: int = 672, seed: int | None = None):
        super().__init__()
        self.energy_scale = float(energy_scale)
        self.episode_steps = int(episode_steps)
        self.rng = np.random.default_rng(seed)
        self.action_space = spaces.MultiDiscrete([8, 8])
        self.observation_space = spaces.Box(
            low=-100.0, high=1000.0, shape=(15,), dtype=np.float32)
        self.sim = None
        self.step_count = 0

    # ---------- 场景 ----------
    def _make_scenario(self, seed=None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        start = (datetime(2026, 10, 12)
                 + timedelta(days=int(self.rng.integers(0, 7)),
                             minutes=int(self.rng.choice([0, 15, 30, 45]))))
        cfg = replace(
            MultiRoomConfig(),
            temp_init_b=float(self.rng.uniform(26.0, 30.0)),
            ua_b=0.15175 * float(self.rng.uniform(0.85, 1.15)),
            temp_outdoor_mean=22.0 + float(self.rng.uniform(-1.0, 1.0)),
        )
        return cfg, start

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        cfg, start = self._make_scenario(seed)
        self.sim = MultiRoomSim(cfg, start)
        self.step_count = 0
        return self._obs(), {}

    # ---------- 观测 ----------
    def _obs(self):
        sim = self.sim
        t = sim.t
        state = sim.schedule.state_at(t)
        nt, ns = sim.schedule.next_transition(t)
        if nt is None:
            ttn = 1.0
            code = 3
        else:
            ttn = min(1.0, (nt - t).total_seconds() / (7 * 24 * 3600))
            code = {"A": 0, "B": 1, "out": 2}.get(ns, 3)
        onehot = [0.0, 0.0, 0.0, 0.0]
        onehot[code] = 1.0
        h = t.hour + t.minute / 60.0
        hour_sin = np.sin(2 * np.pi * h / 24.0)
        hour_cos = np.cos(2 * np.pi * h / 24.0)
        price = (sim.cfg.price_peak if 6 <= t.hour < 22
                 else sim.cfg.price_valley)
        return np.array([
            sim.temp_a, sim.temp_b,
            1.0 if state == "A" else 0.0,
            1.0 if state == "B" else 0.0,
            *onehot, ttn,
            hour_sin, hour_cos, t.weekday() / 6.0, price,
            sim.prev_ac_a / 7.0, sim.prev_ac_b / 7.0,
        ], dtype=np.float32)

    # ---------- 单步 ----------
    def step(self, action):
        sim = self.sim
        prev_a, prev_b = sim.prev_ac_a, sim.prev_ac_b
        a, b = int(action[0]), int(action[1])
        res = sim.step(a, b)
        exec_a, exec_b = sim.prev_ac_a, sim.prev_ac_b
        switch = int(exec_a != prev_a) + int(exec_b != prev_b)

        cfg = sim.cfg
        lo, hi = comfort_band(res["t"], cfg)
        disc = 0.0
        if res["state"] == "A":
            if not (lo <= sim.temp_a <= hi):
                disc = 1.0
            else:
                disc = 0.05 * abs(sim.temp_a - (lo + hi) / 2)
        elif res["state"] == "B":
            if not (lo <= sim.temp_b <= hi):
                disc = 1.0
            else:
                disc = 0.05 * abs(sim.temp_b - (lo + hi) / 2)
        cost = res["price"] * (res["energy_a"] + res["energy_b"])
        reward = -(disc + self.energy_scale * cost + 0.2 * switch)

        self.step_count += 1
        truncated = self.step_count >= self.episode_steps
        info = {
            "cost": cost,
            "energy": res["energy_a"] + res["energy_b"],
            "energy_a": res["energy_a"],
            "energy_b": res["energy_b"],
            "state": res["state"],
            "temp_a": sim.temp_a,
            "temp_b": sim.temp_b,
            "occ_discomfort": sim.occ_discomfort,
            "bad": sim.bad,
            "occ_steps": sim.occ_steps,
        }
        return self._obs(), reward, False, truncated, info
