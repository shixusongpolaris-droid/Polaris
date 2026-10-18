# -*- coding: utf-8 -*-
"""实验 2：奖励与评估指标的「区间内梯度」对齐。

动机
----
评估指标是「温度是否越出舒适区间」的**二值**判断 —— 区间内的位置
不影响任何指标。但奖励函数在区间**内部**还加了一个趋向中心的重力：

    in_band_gradient * alpha_hot * |T - center|

（`src/env/reward.py:115`，默认 in_band_gradient=0.05）

它本意是辅助学习的 shaping，但会诱导策略把温度压向中心、白耗电，
而不是优化评估真正看重的"越界比例"。

已有的直接证据：energy_scale=10 的 PPO 把**空房间**压到 23.67 °C
（无人区间 16~30 的中心是 23），无人时段耗电占总电量 44.7%
（docs/为何比全开更费电.md）。2026-10-17 只修了"无人时不施加"，
**有人时仍保留**。

本实验把该梯度参数化为 4 档，检验完全去掉（=与评估指标对齐）
是否让策略更省电、同时不增加越界。

设计
----
· 固定 energy_scale = 30（这是规则仅领先 7.7% 的赛点，最敏感）
· in_band_gradient ∈ {0.00, 0.02, 0.05（现状）, 0.10}
· 10 个种子 × 300,000 步，VecNormalize 开启
· 评估：种子 9100~9107，8 回合

判据
----
· 主看：同等或更低的不达标率下，电费是否更低
· 兼顾：不达标率不能明显上升（否则是"用舒适换电费"，不算改善）

输出
----
models/gen7_exp2_inband/results.json
docs/gen7区间内梯度对齐.md
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

SCALE = 30.0
GRADIENTS = [0.00, 0.02, 0.05, 0.10]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]
TIMESTEPS = 300_000
LOOKAHEAD = 0.6
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

OUT_DIR = os.path.join(PROJ, "models", "gen7_exp2_inband")
OUT = os.path.join(OUT_DIR, "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7区间内梯度对齐.md")

T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew(grad):
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=SCALE, lookahead_weight=LOOKAHEAD,
                        in_band_gradient=float(grad))


def sim_cfg():
    return SimConfig(occupancy_mode="semi_markov")


def train_one(grad, seed):
    torch.set_num_threads(8)
    venv = DummyVecEnv([lambda: Monitor(SmartHomeEnv(
        rew_cfg=rew(grad), sim_cfg=sim_cfg(), seed=seed))])
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


def measure(model, stats, grad, n=EVAL_EPISODES):
    """评估时用「与梯度无关」的统一口径，保证四组可比。"""
    rc = rew(grad)
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
    rows = []
    print("=" * 88)
    print(" 实验 2：奖励与评估指标的区间内梯度对齐")
    print("=" * 88)
    print(f"  固定 es={SCALE:.0f}，in_band_gradient ∈ {GRADIENTS}")
    print(f"  {len(GRADIENTS)} 组 × {len(SEEDS)} 种子 × {TIMESTEPS:,} 步")
    print(f"  预计耗时约 {len(GRADIENTS)*len(SEEDS)*145/60:.0f} 分钟")
    print()

    for g in GRADIENTS:
        print(f"\n--- in_band_gradient = {g:.2f} "
              f"{'(现状)' if abs(g - 0.05) < 1e-9 else ''} ---")
        seeds_res = []
        for sd in SEEDS:
            model, stats, dt = train_one(g, sd)
            r = measure(model, stats, g)
            r["seed"] = sd
            seeds_res.append(r)
            print(f"  seed={sd:<5} 电量 {r['energy']:>6.3f}  "
                  f"电费 {r['cost']:>6.3f}  "
                  f"有人不达标 {r['occ_discomfort']:>7.2%}")
        agg = {
            "gradient": g,
            "energy": mean([r["energy"] for r in seeds_res]),
            "energy_ci": ci95([r["energy"] for r in seeds_res]),
            "cost": mean([r["cost"] for r in seeds_res]),
            "cost_ci": ci95([r["cost"] for r in seeds_res]),
            "occ_discomfort": mean([r["occ_discomfort"] for r in seeds_res]),
            "occ_ci": ci95([r["occ_discomfort"] for r in seeds_res]),
            "per_seed": seeds_res,
        }
        rows.append(agg)
        print(f"  均值: 电量 {agg['energy']:.3f}±{agg['energy_ci']:.3f}  "
              f"电费 {agg['cost']:.3f}±{agg['cost_ci']:.3f}  "
              f"有人不达标 {agg['occ_discomfort']:.2%}"
              f"±{agg['occ_ci']:.2%}")

    base = next((r for r in rows if abs(r["gradient"] - 0.05) < 1e-9), rows[0])

    print()
    print("=" * 88)
    print(" 汇总")
    print("=" * 88)
    print(f"  {'gradient':>9}{'电量':>18}{'电费':>18}{'有人不达标':>18}")
    print("  " + "-" * 63)
    for r in rows:
        print(f"  {r['gradient']:>9.2f}"
              f"{r['energy']:>11.2f}±{r['energy_ci']:<6.2f}"
              f"{r['cost']:>11.2f}±{r['cost_ci']:<6.2f}"
              f"{r['occ_discomfort']:>12.2%}±{r['occ_ci']:<5.2%}")

    print()
    print(f" 相对现状（gradient={base['gradient']:.2f}）的变化：")
    for r in rows:
        if r is base:
            continue
        dc = (r["cost"] - base["cost"]) / base["cost"]
        dd = r["occ_discomfort"] - base["occ_discomfort"]
        verdict = "改善" if (dc < -0.01 and dd <= 0.005) else (
            "变差" if (dc > 0.01 or dd > 0.02) else "基本持平")
        # ⚠️ dd 是**比例差**（如 0.03 表示 3 个百分点），打印必须乘 100。
        # 2026-10-18 团队审计（t8，P1）指出原写法少乘 100 倍，
        # 会把真实的 +3.00pp 印成 +0.03pp，与 verdict 阈值自相矛盾。
        print(f"   gradient={r['gradient']:.2f}: "
              f"电费 {dc:>+7.1%}  不达标 {dd * 100:>+7.2f}pp  -> {verdict}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"scale": SCALE, "gradients": GRADIENTS,
                   "seeds": SEEDS, "timesteps": TIMESTEPS,
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：奖励与评估指标的区间内梯度对齐", "",
          f"- 生成：2026-10-18",
          f"- 固定 es={SCALE:.0f}，in_band_gradient ∈ {GRADIENTS}",
          f"- {len(SEEDS)} 种子 × {TIMESTEPS:,} 步，VecNormalize 开启",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}", "",
          "## 一、为什么要做", "",
          "评估指标是「温度是否越出舒适区间」的二值判断，"
          "区间内的位置不影响任何指标。",
          "但奖励在区间**内部**还有一个趋向中心的重力"
          "（`reward.py:115`，默认 0.05）。"
          "它会诱导策略把温度压向中心、白耗电，"
          "而不是优化评估看重的「越界比例」。",
          "", "已有证据：energy_scale=10 的 PPO 把空房间压到 23.67°C，"
          "无人时段耗电占总电量 44.7%（`为何比全开更费电.md`）。"
          "当时只修了「无人时不施加」，有人时仍保留。", "",
          "## 二、结果", "",
          "| in_band_gradient | 电量 | 电费 | 有人不达标 |",
          "|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['gradient']:.2f} "
                  f"| {r['energy']:.2f}±{r['energy_ci']:.2f} "
                  f"| {r['cost']:.2f}±{r['cost_ci']:.2f} "
                  f"| {r['occ_discomfort']:.2%}±{r['occ_ci']:.2%} |")
    md += ["", "## 三、相对现状的变化", "",
           "| gradient | 电费变化 | 不达标变化 | 判定 |", "|---|---|---|---|"]
    for r in rows:
        if r is base:
            continue
        dc = (r["cost"] - base["cost"]) / base["cost"]
        dd = r["occ_discomfort"] - base["occ_discomfort"]
        verdict = "改善" if (dc < -0.01 and dd <= 0.005) else (
            "变差" if (dc > 0.01 or dd > 0.02) else "基本持平")
        md.append(f"| {r['gradient']:.2f} | {dc:+.1%} "
                  f"| {dd * 100:+.2f}pp | {verdict} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
