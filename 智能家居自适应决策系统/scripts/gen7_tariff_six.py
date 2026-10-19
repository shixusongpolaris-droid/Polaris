# -*- coding: utf-8 -*-
"""实验 3：上海居民电价表的六格敏感性实验（3 档 × 2 计费方式）。

背景
----
项目所有者指出：上海居民电价表是二维的（年用电量 3 档 × 未分时/分时），
自然应跑 6 种组合：

    第一档  未分时 0.617        第一档  分时 0.617 / 0.307
    第二档  未分时 0.667        第二档  分时 0.677 / 0.337
    第三档  未分时 0.917        第三档  分时 0.977 / 0.487

旧实验 `tariff_tier_sweep`（models/tariff_tier_sweep/summary.json）确实跑过
这 6 格，但有四个问题使它不可用：

  1. 电费字段全为 None（根本没算成本）
  2. 只有 1 个种子（seed=42）
  3. 是旧代次参数（全程开空调 6.178 kWh，而 gen7 是 5.549）
  4. PPO 电量在各档之间乱跳（6.837 / 5.965 / 5.249），单种子噪声明显

本脚本在 **gen7 + VecNormalize** 下重做，5 种子，并**同时重评规则族**
（价格感知规则的行为会随电价变化，必须按各档重算）。

设计
----
· RL：6 组合 × 5 种子 × 300,000 步，VecNormalize 开启
· 规则：6 档 × 941 条规则，与 RL 同一批评估回合（9100~9107）
· 评估口径与 `gen7_unified_protocol.py` 完全一致
· **保存模型与归一化统计**，供独立复核

要回答的问题
------------
1. 电价越高（第一档 -> 第三档），节能策略的价值是否越大？
2. 分时电价（峰谷比 2.01）是否让调度型策略获得额外收益？
3. 在各档电价下，"规则在低舒适区更省"这一结论是否依然成立？

输出
----
models/gen7_tariff_six/results.json
docs/gen7六格电价实验.md
"""

import argparse
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
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import numpy as np                                       # noqa: E402
import torch                                             # noqa: E402
from stable_baselines3 import PPO                        # noqa: E402
from stable_baselines3.common.monitor import Monitor     # noqa: E402
from stable_baselines3.common.vec_env import (           # noqa: E402
    DummyVecEnv, VecNormalize)

from src.env import (RewardConfig, SimConfig,          # noqa: E402
                     SmartHomeEnv)

# 复用统一协议里的规则构造器，保证规则族一致
import importlib.util                                    # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "_up", os.path.join(PROJ, "scripts", "gen7_unified_protocol.py"))
_up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_up)

# ---------- 六格 ----------
TARIFFS = [
    # (tag, 档位, 是否分时, 平/峰/谷)
    ("t1_flat", 1, False, (0.617, 0.617, 0.307)),
    ("t1_tou", 1, True, (0.617, 0.617, 0.307)),
    ("t2_flat", 2, False, (0.667, 0.667, 0.337)),
    ("t2_tou", 2, True, (0.667, 0.677, 0.337)),
    ("t3_flat", 3, False, (0.917, 0.917, 0.487)),
    ("t3_tou", 3, True, (0.917, 0.977, 0.487)),
]

SEEDS = [42, 66, 2026, 7, 123]
TIMESTEPS = 300_000
EVAL_EPISODES = 8
EVAL_SEED0 = 9100
ENERGY_SCALE = 30.0        # 与现有主结论一致的中档
# S3 验证：RL 奖励里的电价前视权重。默认 0.6 与旧实验一致；
# 设为 0.0 即让 RL 与规则/评估一样只看当前电价。
LOOKAHEAD_WEIGHT = 0.6

OUT_DIR = os.path.join(PROJ, "models", "gen7_tariff_six")
OUT = os.path.join(OUT_DIR, "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7六格电价实验.md")

T95 = {9: 2.262, 7: 2.365, 4: 2.776, 3: 3.182, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew_for(flat, peak, valley, tou):
    """按某一格电价构造 RewardConfig。"""
    rc = RewardConfig(weight_mode="scenario", tou_enabled=bool(tou),
                      energy_scale=ENERGY_SCALE,
                      lookahead_weight=LOOKAHEAD_WEIGHT)
    rc.price_flat = float(flat)
    rc.price_peak = float(peak)
    rc.price_valley = float(valley)
    if tou:
        # 上海分时只有两段：峰 6-22、谷 22-6
        rc.peak_periods = [(6, 22)]
        rc.valley_periods = [(22, 6)]
    else:
        # 未分时：全天按该档"未分时"单价计费
        rc.price_flat_tariff = float(flat)
        rc.peak_periods = None
        rc.valley_periods = None
    return rc


def sim_cfg():
    return SimConfig(occupancy_mode="semi_markov")


# ---------------- RL 部分 ----------------
def train_one(rc, seed, tag):
    torch.set_num_threads(8)
    venv = DummyVecEnv([lambda: Monitor(SmartHomeEnv(
        rew_cfg=rc, sim_cfg=sim_cfg(), seed=seed))])
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
    mdir = os.path.join(OUT_DIR, f"{tag}_s{seed}")
    os.makedirs(mdir, exist_ok=True)
    model.save(os.path.join(mdir, "model"))
    np.savez(os.path.join(mdir, "norm_stats.npz"),
             mean=stats["mean"], var=stats["var"])
    venv.close()
    return model, stats, dt


def measure_metrics(rc, action_fn, n=EVAL_EPISODES):
    """统一度量口径（与 gen7_unified_protocol.py 一致）。"""
    sc = sim_cfg()
    E, C, V = [], [], []
    tot = bad = occ_steps = occ_bad = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        e_valley = 0.0
        e_total = 0.0
        while True:
            a = action_fn(env)
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
            e_total += e
            if rc.get_tier_name(hour) == "valley":
                e_valley += e
            if term or trunc:
                m = info["episode_metrics"]
                days = max(env.episode_steps * env.sim_cfg.dt_minutes
                           / 60 / 24, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                V.append(e_valley / max(e_total, 1e-9))
                break
        env.close()
    return {"energy": mean(E), "energy_all": E, "cost": mean(C),
            "cost_all": C,
            "valley_share": mean(V) if V else 0.0,
            "discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1)}


def model_fn(model, stats):
    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        v = (v - stats["mean"]) / np.sqrt(stats["var"] + 1e-8)
        v = np.clip(v, -10.0, 10.0)
        return int(model.predict(v, deterministic=True)[0])
    return fn


def main():
    global TIMESTEPS, LOOKAHEAD_WEIGHT, OUT_DIR, OUT, OUT_MD
    parser = argparse.ArgumentParser(description="上海电价六格实验（支持单格/前视消融）")
    parser.add_argument("--tags", default="",
                        help="逗号分隔的 tag，如 t1_tou；空=全部六格")
    parser.add_argument("--seeds", default="42,66,2026,7,123")
    parser.add_argument("--timesteps", type=int, default=TIMESTEPS)
    parser.add_argument("--lookahead", type=float, default=LOOKAHEAD_WEIGHT,
                        help="RL 奖励的前视电价权重；0=只看当前电价")
    parser.add_argument("--suffix", default="",
                        help="输出后缀；非空时写到 gen7_tariff_six_<suffix>")
    args = parser.parse_args()

    TIMESTEPS = int(args.timesteps)
    LOOKAHEAD_WEIGHT = float(args.lookahead)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    tags = {s.strip() for s in args.tags.split(",") if s.strip()}
    selected = [t for t in TARIFFS if (not tags or t[0] in tags)]
    if not selected:
        print(f"没有匹配的 tag：{args.tags}")
        return 2

    if args.suffix:
        OUT_DIR = os.path.join(PROJ, "models",
                               f"gen7_tariff_six_{args.suffix}")
        OUT = os.path.join(OUT_DIR, "results.json")
        OUT_MD = os.path.join(PROJ, "docs",
                              f"gen7六格电价实验_{args.suffix}.md")

    os.makedirs(OUT_DIR, exist_ok=True)
    all_out = {}

    # ---------- 规则族（选中档各评一遍）----------
    specs = _up.build_specs()
    print("=" * 90)
    print(" 实验 3：上海电价六格敏感性（3 档 × 2 计费方式）")
    print("=" * 90)
    print(f"  RL：{len(selected)} 组合 × {len(seeds)} 种子 × {TIMESTEPS:,} 步")
    print(f"  规则：{len(selected)} 档 × {len(specs)} 条，同一批评估回合")
    print(f"  前视电价权重：{LOOKAHEAD_WEIGHT}")
    print()

    for tag, tier, tou, (flat, peak, valley) in selected:
        rc = rew_for(flat, peak, valley, tou)
        print(f"\n{'='*90}")
        print(f"  {tag}  第{tier}档  {'分时' if tou else '未分时'}  "
              f"平{flat} 峰{peak} 谷{valley}")
        print(f"{'='*90}")

        # --- 规则 ---
        t0 = time.perf_counter()
        rule_rows = []
        for sp in specs:
            fn = _up.build_rule(sp)
            r = measure_metrics(rc, fn)
            r["name"] = sp["name"]
            rule_rows.append(r)
        front = _up.pareto(rule_rows)
        uniq = _up.dedupe(front)
        print(f"  规则评估完成（{time.perf_counter()-t0:.0f}s）："
              f"{len(specs)} 条 -> 前沿 {len(front)} 条目 / "
              f"{len(uniq)} 个工作点")

        # --- RL ---
        rl_rows = []
        for sd in seeds:
            model, stats, dt = train_one(rc, sd, tag)
            r = measure_metrics(rc, model_fn(model, stats))
            r["seed"] = sd
            r["train_seconds"] = dt
            rl_rows.append(r)
            print(f"    seed={sd:<5} 电量 {r['energy']:>6.3f} "
                  f"电费 {r['cost']:>6.3f} "
                  f"谷占比 {r['valley_share']:>6.2%} "
                  f"有人不达标 {r['occ_discomfort']:>7.2%}")
        agg = {
            "energy": mean([r["energy"] for r in rl_rows]),
            "energy_ci": ci95([r["energy"] for r in rl_rows]),
            "cost": mean([r["cost"] for r in rl_rows]),
            "cost_ci": ci95([r["cost"] for r in rl_rows]),
            "valley_share": mean([r["valley_share"] for r in rl_rows]),
            "occ_discomfort": mean([r["occ_discomfort"] for r in rl_rows]),
            "occ_ci": ci95([r["occ_discomfort"] for r in rl_rows]),
            "per_seed": rl_rows,
        }
        print(f"  RL 均值: 电量 {agg['energy']:.3f}  "
              f"电费 {agg['cost']:.3f}±{agg['cost_ci']:.3f}  "
              f"谷占比 {agg['valley_share']:.2%}  "
              f"有人不达标 {agg['occ_discomfort']:.2%}")

        # --- 与规则对比（占优判据）---
        cands = [u for u in uniq
                 if u["occ_discomfort"] <= agg["occ_discomfort"] + 1e-12]
        ref = min(cands, key=lambda u: u["cost"]) if cands else None
        if ref:
            diff = (agg["cost"] - ref["cost"]) / ref["cost"]
            print(f"  规则参照：{ref['cost']:.3f} 元 "
                  f"({ref['occ_discomfort']:.2%} / {ref['name'][:34]})")
            print(f"  差值：{diff:+.1%}")
        else:
            diff = None
            print("  规则参照：无（RL 的不达标率低于规则最低点）")

        all_out[tag] = {
            "tier": tier, "tou": bool(tou),
            "prices": {"flat": flat, "peak": peak, "valley": valley},
            "rule_n": len(specs),
            "rule_front": front,
            "rule_distinct": uniq,
            "rl": agg,
            "rule_ref_cost": ref["cost"] if ref else None,
            "rule_ref_d": ref["occ_discomfort"] if ref else None,
            "rule_ref_name": ref["name"] if ref else None,
            "diff": diff,
        }

    # ---------- 汇总 ----------
    print()
    print("=" * 90)
    print(" 六格汇总")
    print("=" * 90)
    print(f"  {'配置':<10}{'平/峰':>12}{'谷':>8}{'RL电量':>9}{'RL电费':>10}"
          f"{'规则参照':>10}{'差值':>9}")
    print("  " + "-" * 70)
    for tag, tier, tou, (flat, peak, valley) in selected:
        o = all_out[tag]
        rc_ = o["rule_ref_cost"]
        dd = o["diff"]
        print(f"  {tag:<10}{f'{flat}/{peak}':>12}{valley:>8.3f}"
              f"{o['rl']['energy']:>9.3f}{o['rl']['cost']:>10.3f}"
              f"{(f'{rc_:.3f}' if rc_ else '-'):>10}"
              f"{(f'{dd:+.1%}' if dd is not None else '-'):>9}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"tiers": all_out, "seeds": seeds,
                   "timesteps": TIMESTEPS,
                   "energy_scale": ENERGY_SCALE,
                   "lookahead_weight": LOOKAHEAD_WEIGHT},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：上海电价六格敏感性实验", "",
          f"- 生成：2026-10-20",
          f"- {len(selected)} 组合 × {len(seeds)} 种子 × {TIMESTEPS:,} 步，VecNormalize 开启",
          f"- energy_scale 固定为 {ENERGY_SCALE:.0f}；前视电价权重 {LOOKAHEAD_WEIGHT}",
          f"- 规则族 {len(specs)} 条，每档重评（价格感知规则的行为随电价变化）",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}", "",
          "## 一、六格结果", "",
          "| 配置 | 档位 | 计费 | 平/峰 | 谷 | RL 电量 | RL 电费 "
          "| 规则参照 | 差值 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for tag, tier, tou, (flat, peak, valley) in TARIFFS:
        o = all_out[tag]
        ref_s = (f"{o['rule_ref_cost']:.3f}"
                 if o["rule_ref_cost"] is not None else "-")
        diff_s = (f"{o['diff']:+.1%}"
                  if o["diff"] is not None else "-")
        md.append(f"| {tag} | 第{tier}档 | {'分时' if tou else '未分时'} "
                  f"| {flat}/{peak} | {valley} "
                  f"| {o['rl']['energy']:.3f} "
                  f"| {o['rl']['cost']:.3f}±{o['rl']['cost_ci']:.3f} "
                  f"| {ref_s} | {diff_s} |")
    md += ["", "## 二、要回答的问题", "",
           "1. 电价越高，节能策略的价值是否越大？",
           "2. 分时电价是否让调度型策略获得额外收益？",
           "3. 各档下「规则在低舒适区更省」是否仍成立？", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
