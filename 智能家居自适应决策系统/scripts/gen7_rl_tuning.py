# -*- coding: utf-8 -*-
"""gen7：诊断强化学习为什么打不过规则 —— 逐项加入"标准实践"。

背景
----
gen7 的等舒适度对比显示，RL 在实用舒适区仍比规则贵 3.7%~59%。在断言
"RL 不适合这类问题"之前，必须先排除"是我们把 RL 训坏了"这一可能。

本脚本逐项加入三项**任何合格的 RL 实践者都会做、而本项目此前没做**的
改进，看差距能关掉多少：

  1. VecNormalize   —— 观测归一化
  2. 更大的网络     —— [128,128] -> [256,256]
  3. 更多训练步数   —— 300k -> 1M

第 1 项是最可疑的。当前观测各维量级差 10 万倍：

      light_in    0 ~ 30,000
      temp_in    15 ~ 35
      elec_price  0.3 ~ 0.9

MLP 的第一层直接吃原始值，梯度会被大数量级的维主导。SB3 的标准做法
就是 VecNormalize，而本项目的训练脚本从未用过。

设计
----
固定 es=60（规则只领先 3.7%，是最紧的赛点），3 种子，对比四组配置。
评估在**同一批回合**上做，与规则结果可直接比较。

输出
----
docs/gen7强化学习调优诊断.md
models/gen7_rl_tuning/results.json
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
                     SmartHomeEnv, ActionConfig, encode_action)

OUT_DIR = os.path.join(PROJ, "models", "gen7_rl_tuning")
OUT = os.path.join(OUT_DIR, "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7强化学习调优诊断.md")

TARGET_SCALE = 60.0
SEEDS = [42, 66, 2026]
LOOKAHEAD = 0.6
EVAL_EPISODES = 8
EVAL_SEED0 = 9100          # 与 gen7_equal_comfort / 规则结果一致

CONFIGS = [
    ("A 基线 (现状)", dict(net=128, steps=300_000, vecnorm=False)),
    ("B +VecNormalize", dict(net=128, steps=300_000, vecnorm=True)),
    ("C +VecNorm+网络256", dict(net=256, steps=300_000, vecnorm=True)),
    ("D +VecNorm+256+1M步", dict(net=256, steps=1_000_000, vecnorm=True)),
]

T95 = {7: 2.365, 2: 4.303}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=TARGET_SCALE,
                        lookahead_weight=LOOKAHEAD)


def sim_cfg():
    return SimConfig(occupancy_mode="semi_markov")


def make_vec(net, vecnorm):
    venv = DummyVecEnv([lambda: Monitor(SmartHomeEnv(
        rew_cfg=rew(), sim_cfg=sim_cfg(), seed=0))])
    if vecnorm:
        venv = VecNormalize(venv, norm_obs=True, norm_reward=False,
                            clip_obs=10.0)
    return venv


def train_one(cfg, seed):
    torch.set_num_threads(8)
    venv = make_vec(cfg["net"], cfg["vecnorm"])
    torch.manual_seed(seed)
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                ent_coef=0.01,
                policy_kwargs=dict(net_arch=[cfg["net"], cfg["net"]]))
    t0 = time.perf_counter()
    model.learn(total_timesteps=cfg["steps"])
    dt = time.perf_counter() - t0
    venv.close()
    # 取出归一化统计，用于评估时对齐
    if cfg["vecnorm"]:
        vn = venv
        obs_rms = {"mean": vn.obs_rms.mean.copy(),
                   "var": vn.obs_rms.var.copy()}
    else:
        obs_rms = None
    return model, obs_rms, dt


def measure(model, obs_rms, n=EVAL_EPISODES):
    """与规则脚本完全相同的度量口径。"""
    rc = rew()
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
            if obs_rms is not None:
                v = (v - obs_rms["mean"]) / np.sqrt(obs_rms["var"] + 1e-8)
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
    # 规则的参照点（来自 gen7_equal_comfort 的同一批回合）
    rule_ref = None
    eq_path = os.path.join(PROJ, "models", "gen7_equal_comfort",
                           "results.json")
    if os.path.exists(eq_path):
        with open(eq_path, encoding="utf-8") as f:
            eq = json.load(f)
        for a in eq.get("rl_agg", []):
            if abs(a["scale"] - TARGET_SCALE) < 1e-6:
                for c in eq.get("equal_comfort", []):
                    if abs(c["scale"] - TARGET_SCALE) < 1e-6:
                        rule_ref = c
    if rule_ref:
        print(f"  规则在同一舒适度水平的参照电费："
              f"{rule_ref['rule_cost']:.3f} 元/天"
              f"（对应不达标 {rule_ref['rl_d']:.1%}）")
    print()

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    print("=" * 92)
    print(f" gen7 RL 调优诊断（es={TARGET_SCALE:.0f}，{len(SEEDS)} 种子）")
    print("=" * 92)
    for name, cfg in CONFIGS:
        print(f"\n--- {name} ---")
        per_seed = []
        for sd in SEEDS:
            model, rms, dt = train_one(cfg, sd)
            r = measure(model, rms)
            r["seed"] = sd
            r["train_seconds"] = dt
            per_seed.append(r)
            print(f"  seed={sd:<5} 电量 {r['energy']:>6.3f}  "
                  f"电费 {r['cost']:>6.3f}  "
                  f"有人不达标 {r['occ_discomfort']:>6.2%}  "
                  f"训练 {dt:>5.0f}s")
        agg = {
            "name": name,
            "energy": mean([r["energy"] for r in per_seed]),
            "cost": mean([r["cost"] for r in per_seed]),
            "cost_ci": ci95([r["cost"] for r in per_seed]),
            "occ_discomfort": mean([r["occ_discomfort"] for r in per_seed]),
            "occ_ci": ci95([r["occ_discomfort"] for r in per_seed]),
            "train_seconds": mean([r["train_seconds"] for r in per_seed]),
            "per_seed": per_seed,
        }
        rows.append(agg)
        print(f"  均值: 电量 {agg['energy']:.3f}  电费 {agg['cost']:.3f}"
              f"±{agg['cost_ci']:.3f}  有人不达标 "
              f"{agg['occ_discomfort']:.2%}±{agg['occ_ci']:.2%}")

    print()
    print("=" * 92)
    print(" 汇总")
    print("=" * 92)
    print(f"  {'配置':<22}{'电量':>8}{'电费':>9}{'有人不达标':>12}"
          f"{'训练(秒)':>10}")
    print("  " + "-" * 63)
    for r in rows:
        print(f"  {r['name']:<22}{r['energy']:>8.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>12.2%}{r['train_seconds']:>10.0f}")

    base = rows[0]
    print()
    print(" 相对基线的改善：")
    for r in rows[1:]:
        de = (r["energy"] - base["energy"]) / base["energy"]
        dd = r["occ_discomfort"] - base["occ_discomfort"]
        print(f"   {r['name']:<22} 电量 {de:>+7.1%}   "
              f"有人不达标 {dd:>+7.2f}pp")

    if rule_ref:
        print()
        print(" 与规则的差距（等舒适度，同批回合）：")
        for r in rows:
            # 用该配置的不达标率去查规则前沿
            with open(eq_path, encoding="utf-8") as f:
                eq = json.load(f)
            rf = eq.get("rule_front", [])
            pts = sorted(rf, key=lambda x: x["occ_discomfort"])
            lo = [p for p in pts if p["occ_discomfort"]
                  <= r["occ_discomfort"]]
            hi = [p for p in pts if p["occ_discomfort"]
                  >= r["occ_discomfort"]]
            if not lo or not hi:
                print(f"   {r['name']:<22} 不达标率超出规则前沿范围")
                continue
            l = max(lo, key=lambda x: x["occ_discomfort"])
            h = min(hi, key=lambda x: x["occ_discomfort"])
            if abs(h["occ_discomfort"] - l["occ_discomfort"]) < 1e-12:
                rc = l["cost"]
            else:
                w = ((r["occ_discomfort"] - l["occ_discomfort"])
                     / (h["occ_discomfort"] - l["occ_discomfort"]))
                rc = l["cost"] + w * (h["cost"] - l["cost"])
            diff = (r["cost"] - rc) / rc
            who = "规则" if diff > 0 else "RL"
            r["rule_cost"] = rc
            r["vs_rule"] = diff
            print(f"   {r['name']:<22} 规则 {rc:.3f}  RL {r['cost']:.3f}  "
                  f"{diff:>+7.1%}  {who}更省")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"target_scale": TARGET_SCALE, "seeds": SEEDS,
                   "configs": rows, "rule_ref": rule_ref},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：强化学习调优诊断", "",
          f"- 生成：2026-10-18",
          f"- 目标档位：es={TARGET_SCALE:.0f}（规则只领先 3.7%，最紧的赛点）",
          f"- 种子：{SEEDS}",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}"
          f"（与规则结果同一批回合）", "",
          "## 一、为什么做这个", "",
          "在断言「RL 不适合这类问题」之前，必须先排除"
          "「是我们把 RL 训坏了」。本脚本逐项加入三项"
          "**标准实践但本项目此前没做**的改进：", "",
          "| # | 改进 | 理由 |", "|---|---|---|",
          "| 1 | VecNormalize | 观测各维量级差 10 万倍"
          "（light_in 0~30000 vs elec_price 0.3~0.9），"
          "MLP 首层梯度被大数量级维主导 |",
          "| 2 | 网络 128 -> 256 | 容量 |",
          "| 3 | 300k -> 1M 步 | 训练预算 |", "",
          "## 二、结果", "",
          "| 配置 | 电量 | 电费 | 有人不达标 | 训练耗时 |",
          "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['name']} | {r['energy']:.3f} "
                  f"| {r['cost']:.3f}±{r['cost_ci']:.3f} "
                  f"| {r['occ_discomfort']:.2%}±{r['occ_ci']:.2%} "
                  f"| {r['train_seconds']:.0f}s |")
    md += ["", "## 三、相对基线的改善", "",
           "| 配置 | 电量变化 | 有人不达标变化 |", "|---|---|---|"]
    for r in rows[1:]:
        de = (r["energy"] - base["energy"]) / base["energy"]
        dd = r["occ_discomfort"] - base["occ_discomfort"]
        md.append(f"| {r['name']} | {de:+.1%} | {dd:+.2f}pp |")
    if any("vs_rule" in r for r in rows):
        md += ["", "## 四、与规则的差距（等舒适度）", "",
               "| 配置 | 规则同水平 | RL 电费 | 差值 | 谁省 |",
               "|---|---|---|---|---|"]
        for r in rows:
            if "vs_rule" not in r:
                continue
            who = "规则" if r["vs_rule"] > 0 else "RL"
            md.append(f"| {r['name']} | {r['rule_cost']:.3f} "
                      f"| {r['cost']:.3f} | {r['vs_rule']:+.1%} | {who} |")
    md += ["", "## 五、结论", ""]
    best = min(rows, key=lambda x: x["cost"])
    md.append(f"- 电费最低的配置：**{best['name']}**"
              f"（{best['cost']:.3f} 元/天）")
    if "vs_rule" in best:
        md.append(f"- 该配置与规则的差距：{best['vs_rule']:+.1%}")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
