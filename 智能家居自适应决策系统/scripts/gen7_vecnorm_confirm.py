# -*- coding: utf-8 -*-
"""gen7：VecNormalize 配置的 10 种子确认实验。

为什么需要
----------
调优诊断发现 "+VecNormalize" 让 es=60 的电量降 44.3%、电费降 38.7%，
并落在规则族无法表达的 2%~15% 不舒适区（1.504 元 / 4.56%）。
但那只跑了 **3 个种子**，电费的 95% 置信区间半宽达 0.358 元（24%），
不足以支撑"结论翻转"这种断言。

本脚本用 10 个种子重跑，并同时跑 es=10/30/60/100 四档，看
VecNormalize 是否在所有档位都带来同向改善。

判据
----
  · 与规则前沿的对比**只在规则有实测点的舒适度水平上做**
  · 若 RL 的不达标率落在规则的 1.0%~34.1% 空档内，如实标注
    「规则无对应工作点，无法做等舒适度比较」

输出
----
docs/gen7_VecNorm确认.md
models/gen7_vecnorm_confirm/results.json
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

SCALES = [10.0, 30.0, 60.0, 100.0]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]
TIMESTEPS = 300_000
LOOKAHEAD = 0.6
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

OUT_DIR = os.path.join(PROJ, "models", "gen7_vecnorm_confirm")
OUT = os.path.join(OUT_DIR, "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7_VecNorm确认.md")

T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew(scale):
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=float(scale), lookahead_weight=LOOKAHEAD)


def sim_cfg():
    return SimConfig(occupancy_mode="semi_markov")


def train_one(scale, seed):
    torch.set_num_threads(8)
    venv = DummyVecEnv([lambda: Monitor(SmartHomeEnv(
        rew_cfg=rew(scale), sim_cfg=sim_cfg(), seed=seed))])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)
    torch.manual_seed(seed)
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99, ent_coef=0.01,
                policy_kwargs=dict(net_arch=[128, 128]))
    t0 = time.perf_counter()
    model.learn(total_timesteps=TIMESTEPS)
    dt = time.perf_counter() - t0
    stats = {"mean": venv.obs_rms.mean.copy(),
             "var": venv.obs_rms.var.copy()}
    venv.close()
    return model, stats, dt


def measure(model, stats, scale, n=EVAL_EPISODES):
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
    print(" gen7：VecNormalize 的 10 种子确认实验")
    print("=" * 88)
    print(f"  四档 es × {len(SEEDS)} 种子 × {TIMESTEPS:,} 步")
    print(f"  预计耗时约 {len(SCALES)*len(SEEDS)*145/60:.0f} 分钟")
    print()

    for sc in SCALES:
        print(f"\n--- es={sc:.0f} ---")
        seeds_res = []
        for sd in SEEDS:
            model, stats, dt = train_one(sc, sd)
            r = measure(model, stats, sc)
            r["seed"] = sd
            r["train_seconds"] = dt
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
              f"有人不达标 {agg['occ_discomfort']:.2%}±{agg['occ_ci']:.2%}")

    print()
    print("=" * 88)
    print(" 汇总")
    print("=" * 88)
    print(f"  {'es':>5}{'电量':>18}{'电费':>18}{'有人不达标':>16}")
    print("  " + "-" * 57)
    for a in all_rows:
        print(f"  {a['scale']:>5.0f}"
              f"{a['energy']:>11.2f}±{a['energy_ci']:<6.2f}"
              f"{a['cost']:>11.2f}±{a['cost_ci']:<6.2f}"
              f"{a['occ_discomfort']:>11.1%}±{a['occ_ci']:<4.1%}")

    # 与规则前沿对照（只在规则有实测点的舒适度上比较）
    rule_front = []
    fp = os.path.join(PROJ, "models", "gen7_rule_emptysteps", "results.json")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            rule_front = json.load(f).get("front", [])
    if rule_front:
        print()
        print("=" * 88)
        print(" 与规则前沿对照")
        print("=" * 88)
        lo_d = min(r["occ_discomfort"] for r in rule_front)
        hi_gap = None
        ds = sorted([r["occ_discomfort"] for r in rule_front])
        # 找最大空档
        gap = (0, 0, 0)
        for i in range(len(ds) - 1):
            if ds[i + 1] - ds[i] > gap[2]:
                gap = (ds[i], ds[i + 1], ds[i + 1] - ds[i])
        print(f"  规则前沿 {len(rule_front)} 点，不达标率范围 "
              f"{lo_d:.2%} ~ {max(ds):.2%}")
        print(f"  最大空档：{gap[0]:.2%} ~ {gap[1]:.2%}（宽 {gap[2]:.1%}）")
        print()
        for a in all_rows:
            d = a["occ_discomfort"]
            if gap[0] < d < gap[1]:
                print(f"  es={a['scale']:>3.0f} 不达标 {d:.2%} 落在规则空档内 "
                      f"-> 规则无对应工作点，无法做等舒适度比较")
            else:
                pts = sorted(rule_front, key=lambda x: x["occ_discomfort"])
                lo = [p for p in pts if p["occ_discomfort"] <= d]
                hi = [p for p in pts if p["occ_discomfort"] >= d]
                if lo and hi:
                    l = max(lo, key=lambda x: x["occ_discomfort"])
                    h = min(hi, key=lambda x: x["occ_discomfort"])
                    if abs(h["occ_discomfort"] - l["occ_discomfort"]) < 1e-12:
                        rc_ = l["cost"]
                    else:
                        w = ((d - l["occ_discomfort"])
                             / (h["occ_discomfort"] - l["occ_discomfort"]))
                        rc_ = l["cost"] + w * (h["cost"] - l["cost"])
                    diff = (a["cost"] - rc_) / rc_
                    who = "规则" if diff > 0 else "RL"
                    a["rule_cost"] = rc_
                    a["vs_rule"] = diff
                    print(f"  es={a['scale']:>3.0f} 不达标 {d:.2%}  "
                          f"规则 {rc_:.3f}  RL {a['cost']:.3f}  "
                          f"{diff:>+7.1%}  {who}更省")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"scales": SCALES, "seeds": SEEDS,
                   "timesteps": TIMESTEPS, "agg": all_rows},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：VecNormalize 的 10 种子确认", "",
          f"- 生成：2026-10-18",
          f"- 四档 es × {len(SEEDS)} 种子 × {TIMESTEPS:,} 步",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}", "",
          "## 一、结果（10 种子均值 ± 95% CI）", "",
          "| es | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for a in all_rows:
        md.append(f"| {a['scale']:.0f} "
                  f"| {a['energy']:.2f}±{a['energy_ci']:.2f} "
                  f"| {a['cost']:.2f}±{a['cost_ci']:.2f} "
                  f"| {a['occ_discomfort']:.1%}±{a['occ_ci']:.1%} |")
    if any("vs_rule" in a for a in all_rows):
        md += ["", "## 二、与规则对照", "",
               "| es | 不达标 | 规则同水平 | RL 电费 | 差值 | 谁省 |",
               "|---|---|---|---|---|---|"]
        for a in all_rows:
            if "vs_rule" not in a:
                continue
            who = "规则" if a["vs_rule"] > 0 else "RL"
            md.append(f"| {a['scale']:.0f} | {a['occ_discomfort']:.1%} "
                      f"| {a['rule_cost']:.3f} | {a['cost']:.3f} "
                      f"| {a['vs_rule']:+.1%} | {who} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
