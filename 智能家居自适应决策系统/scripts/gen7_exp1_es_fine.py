# -*- coding: utf-8 -*-
"""实验 1：能耗权重细网格扫描（在 VecNormalize 基础上）。

目的
----
现有结果只测了 es ∈ {10, 30, 60, 100}：

    es=10   1.16% 不达标   2.795 元
    es=30   1.54%           2.084 元
    es=60   5.02%           1.660 元
    es=100  8.70%           1.505 元

低不舒适端（1~2%）与规则（0.98% / 1.935 元）差距最大。10 与 30 之间
跨度很大（不舒适 1.16%→1.54%、电费 2.795→2.084），中间可能有更好的
工作点。本脚本补测 es ∈ {15, 20, 25, 40, 50}。

协议（与 gen7_vecnorm_confirm.py 完全一致，便于合并）
-----------------------------------------------
· SimConfig(occupancy_mode="semi_markov")，27 维观测
· 训练：PPO MLP[128,128]，n_steps=1024，batch=256，lr=3e-4，
        γ=0.99，ent_coef=0.01，300,000 步
· **VecNormalize(norm_obs=True, norm_reward=False, clip_obs=10)**
· 评估：种子 9100~9107，8 回合 × 7 天
· 每个 es 跑 10 个种子

输出
----
models/gen7_es_fine/results.json
docs/gen7细网格扫描.md
"""

import json
import math
import os
import sys
import time
from statistics import mean, stdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                       # noqa: E402
import torch                                             # noqa: E402
from stable_baselines3 import PPO                        # noqa: E402
from stable_baselines3.common.monitor import Monitor     # noqa: E402
from stable_baselines3.common.vec_env import (           # noqa: E402
    DummyVecEnv, VecNormalize)

from src.env import (RewardConfig, SimConfig,          # noqa: E402
                     SmartHomeEnv)

# 补测的档位（10/30/60/100 已有结果，不重复跑）
SCALES = [15.0, 20.0, 25.0, 40.0, 50.0]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]
TIMESTEPS = 300_000
LOOKAHEAD = 0.6
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

OUT_DIR = os.path.join(PROJ, "models", "gen7_es_fine")
OUT = os.path.join(OUT_DIR, "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7细网格扫描.md")

# t 分布 95% 双侧（df = n-1）
T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew(scale):
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=float(scale),
                        lookahead_weight=LOOKAHEAD)


def sim_cfg():
    return SimConfig(occupancy_mode="semi_markov")


def train_one(scale, seed):
    torch.set_num_threads(8)
    venv = DummyVecEnv([lambda: Monitor(SmartHomeEnv(
        rew_cfg=rew(scale), sim_cfg=sim_cfg(), seed=seed))])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False,
                        clip_obs=10.0)
    torch.manual_seed(seed)
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                ent_coef=0.01,
                policy_kwargs=dict(net_arch=[128, 128]))
    t0 = time.perf_counter()
    model.learn(total_timesteps=TIMESTEPS)
    dt = time.perf_counter() - t0
    stats = {"mean": venv.obs_rms.mean.copy(),
             "var": venv.obs_rms.var.copy()}
    venv.close()
    return model, stats, dt


def measure(model, stats, scale, n=EVAL_EPISODES):
    """与 gen7_vecnorm_confirm.py 完全相同的度量口径。"""
    rc = rew(scale)
    sc = sim_cfg()
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            v = np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32)
            v = (v - stats["mean"]) / np.sqrt(stats["var"] + 1e-8)
            v = np.clip(v, -10.0, 10.0)
            a = int(model.predict(v, deterministic=True)[0])
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            e = float(info["components"]["energy_kwh"])
            if not occ:
                lo, hi = rc.temp_low_empty, rc.temp_high_empty
            else:
                occ_steps += 1
                if hour >= rc.sleep_start_hour or hour < rc.sleep_end_hour:
                    lo, hi = rc.temp_low_sleep, rc.temp_high_sleep
                else:
                    lo, hi = rc.temp_low_occupied, rc.temp_high_occupied
            tot += 1
            if T > hi or T < lo:
                bad += 1
                if occ:
                    occ_bad += 1
            cost += info["components"]["elec_price"] * e
            if term or trunc:
                m = info["episode_metrics"]
                days = max(env.episode_steps * env.sim_cfg.dt_minutes
                           / 60 / 24, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                break
        env.close()
    return {"energy": mean(E), "cost": mean(C),
            "discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1)}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_rows = []
    print("=" * 88)
    print(" 实验 1：能耗权重细网格扫描（VecNormalize）")
    print("=" * 88)
    print(f"  补测 es = {SCALES}")
    print(f"  {len(SCALES)} 档 × {len(SEEDS)} 种子 × {TIMESTEPS:,} 步")
    print(f"  预计耗时约 {len(SCALES)*len(SEEDS)*145/60:.0f} 分钟")
    print()

    for sc in SCALES:
        print(f"\n--- es={sc:.0f} ---")
        seeds_res = []
        for sd in SEEDS:
            model, stats, dt = train_one(sc, sd)
            r = measure(model, stats, sc)
            r["seed"] = sd
            seeds_res.append(r)
            print(f"  seed={sd:<5} 电量 {r['energy']:>6.3f}  "
                  f"电费 {r['cost']:>6.3f}  "
                  f"有人不达标 {r['occ_discomfort']:>7.2%}")
        agg = {
            "scale": sc,
            "energy": mean([r["energy"] for r in seeds_res]),
            "energy_ci": ci95([r["energy"] for r in seeds_res]),
            "cost": mean([r["cost"] for r in seeds_res]),
            "cost_ci": ci95([r["cost"] for r in seeds_res]),
            "occ_discomfort": mean([r["occ_discomfort"] for r in seeds_res]),
            "occ_ci": ci95([r["occ_discomfort"] for r in seeds_res]),
            "per_seed": seeds_res,
        }
        all_rows.append(agg)
        print(f"  均值: 电量 {agg['energy']:.3f}±{agg['energy_ci']:.3f}  "
              f"电费 {agg['cost']:.3f}±{agg['cost_ci']:.3f}  "
              f"有人不达标 {agg['occ_discomfort']:.2%}"
              f"±{agg['occ_ci']:.2%}")

    # 与既有四档合并
    existing_path = os.path.join(PROJ, "models", "gen7_vecnorm_confirm",
                                 "results.json")
    merged = []
    if os.path.exists(existing_path):
        with open(existing_path, encoding="utf-8") as f:
            old = json.load(f).get("agg", [])
        merged = [{"scale": a["scale"], "energy": a["energy"],
                   "energy_ci": a["energy_ci"], "cost": a["cost"],
                   "cost_ci": a["cost_ci"],
                   "occ_discomfort": a["occ_discomfort"],
                   "occ_ci": a["occ_ci"], "per_seed": a.get("per_seed", [])}
                  for a in old]
    merged.extend(all_rows)
    merged.sort(key=lambda x: x["scale"])

    # 合并后的 RL 前沿（去支配）
    def dominated(r):
        return any(
            (o["cost"] <= r["cost"] + 1e-12
             and o["occ_discomfort"] <= r["occ_discomfort"] + 1e-12
             and (o["cost"] < r["cost"] - 1e-12
                  or o["occ_discomfort"] < r["occ_discomfort"] - 1e-12))
            for o in merged if o is not r)

    front = sorted([m for m in merged if not dominated(m)],
                   key=lambda x: x["occ_discomfort"])

    print()
    print("=" * 88)
    print(" 合并全部档位后的 RL 前沿")
    print("=" * 88)
    print(f"  {'es':>5}{'不达标':>10}{'电费':>10}{'电量':>9}  在前沿?")
    print("  " + "-" * 48)
    for m in merged:
        tag = "" if m in front else "  (被支配)"
        print(f"  {m['scale']:>5.0f}{m['occ_discomfort']:>10.2%}"
              f"{m['cost']:>10.3f}{m['energy']:>9.3f}{tag}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"scales": SCALES, "seeds": SEEDS,
                   "timesteps": TIMESTEPS,
                   "new": all_rows, "merged": merged,
                   "rl_front": front}, f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：能耗权重细网格扫描", "",
          f"- 生成：2026-10-18",
          f"- 补测 es = {SCALES}（10/30/60/100 已有）",
          f"- {len(SEEDS)} 种子 × {TIMESTEPS:,} 步，VecNormalize 开启",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}", "",
          "## 一、新增档位", "",
          "| es | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for a in all_rows:
        md.append(f"| {a['scale']:.0f} "
                  f"| {a['energy']:.2f}±{a['energy_ci']:.2f} "
                  f"| {a['cost']:.2f}±{a['cost_ci']:.2f} "
                  f"| {a['occ_discomfort']:.2%}±{a['occ_ci']:.2%} |")
    md += ["", "## 二、合并全部档位后的 RL 前沿", "",
           "| es | 不达标 | 电费 | 电量 |", "|---|---|---|---|"]
    for m in front:
        md.append(f"| {m['scale']:.0f} | {m['occ_discomfort']:.2%} "
                  f"| {m['cost']:.3f} | {m['energy']:.3f} |")
    md += ["", "## 三、全部档位", "",
           "| es | 不达标 | 电费 | 电量 | 在 RL 前沿 |", "|---|---|---|---|---|"]
    for m in merged:
        md.append(f"| {m['scale']:.0f} | {m['occ_discomfort']:.2%} "
                  f"| {m['cost']:.3f} | {m['energy']:.3f} "
                  f"| {'是' if m in front else '否'} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
