# -*- coding: utf-8 -*-
"""energy_scale 的置信区间实验。

目的
----
此前的权重结论只有 2~5 个种子，无法判断"RL 优于规则"是否稳健。
本脚本用 10 个种子、4 档 `energy_scale` 给出 95% 置信区间，
并用**不重叠区间**作为"显著优于"的保守判据。

设计
----
    energy_scale ∈ {10, 30, 60, 100}
    种子        10 个
    训练        各 300,000 步（PPO, MLP[128,128]）
    评估        各 8 回合（回合无关的固定评估种子）
    场景        启用分时电价（tou_enabled=True），lookahead_weight=0.6
                （与 energy_weight_sweep.py 口径一致，便于对照）

判据
----
  · 95% 置信区间用 t 分布（df = n-1 = 9）
  · "显著优于规则"要求 RL 的 CI 上界 < 规则的点估计，
    即两区间不重叠——这是比 t 检验更保守的判据

输出
----
docs/能耗权重置信区间.md
models/energy_scale_ci/results.json
"""

import json
import math
import os
import sys
import time
from datetime import datetime
from statistics import mean, stdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from src.env.config import ActionConfig, SimConfig      # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402
from stable_baselines3.common.monitor import Monitor   # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "energy_scale_ci")
OUT_MD = os.path.join(PROJ, "docs", "能耗权重置信区间.md")

SCALES = [10.0, 30.0, 60.0, 100.0]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]
TIMESTEPS = 300_000
EVAL_EPISODES = 8
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6

# 占用模型（gen7）：加 --semi-markov 则启用半马尔可夫 + 27 维观测
_OCC = "semi_markov" if "--semi-markov" in sys.argv else "iid"
SIM_CFG = SimConfig(occupancy_mode=_OCC)

AC_OFF = 0
_AC = ActionConfig()
AC_25C = _AC.ac_setpoints.index(25.0) + 1
AC_27C = _AC.ac_setpoints.index(27.0) + 1
AC_28C = _AC.ac_setpoints.index(28.0) + 1

# t 分布 95% 双侧临界值（df = n-1）
T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(values):
    n = len(values)
    if n < 2:
        return 0.0
    t = T95.get(n - 1, 1.96)
    return t * stdev(values) / math.sqrt(n)


def rew_for(scale):
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=float(scale),
                        lookahead_weight=LOOKAHEAD)


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def policy_rule_tou(env):
    """规则控制：按分时电价区分设定温度（谷 25 / 平 26 / 峰 28）。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    price = env.rew_cfg.get_price(int(obs["hour"]))
    if price <= env.rew_cfg.price_valley + 1e-9:
        target = 25.0
    elif price >= env.rew_cfg.price_peak - 1e-9:
        target = 28.0
    else:
        target = 26.0
    T = obs["temp_in"]
    if T > target + 0.5:
        ac = ac_idx_for(target, env.act_cfg)
    elif T < target - 0.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def evaluate(action_fn, scale, n=EVAL_EPISODES):
    rc = rew_for(scale)
    E, C, D = [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=SIM_CFG, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                days = max(env.episode_steps * env.sim_cfg.dt_minutes / 60 / 24,
                           1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                D.append(float(m.get("discomfort_ratio", 0.0)))
                C.append(cost / days)
                break
        env.close()
    return {"energy": E, "cost": C, "discomfort": D}


def train(scale, seed):
    d = os.path.join(OUT_DIR, f"es{int(scale)}_s{seed}")
    os.makedirs(d, exist_ok=True)
    ck = os.path.join(d, "final_model.zip")
    if os.path.exists(ck):
        return ck
    venv = DummyVecEnv([lambda: Monitor(
        SmartHomeEnv(rew_cfg=rew_for(scale), sim_cfg=SIM_CFG, seed=seed))])
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                gae_lambda=0.95, ent_coef=0.01, device="cpu")
    model.learn(total_timesteps=TIMESTEPS)
    model.save(ck)
    venv.close()
    return ck


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print("=" * 90)
    print(" energy_scale 置信区间实验（10 种子 × 4 档）")
    print("=" * 90)
    print(f"  energy_scale : {SCALES}")
    print(f"  种子         : {len(SEEDS)} 个")
    print(f"  训练步数     : {TIMESTEPS:,} / 个")
    print(f"  评估回合     : {EVAL_EPISODES}")
    print(f"  分时电价     : 启用，lookahead_weight={LOOKAHEAD}")
    print()

    # ---- 规则基线 ----
    print("=== 规则基线（分时电价，按谷/平/峰分别设 25/26/28 °C）===")
    rb = evaluate(policy_rule_tou, 60.0)
    rule = {"energy": mean(rb["energy"]), "energy_ci": ci95(rb["energy"]),
            "cost": mean(rb["cost"]), "cost_ci": ci95(rb["cost"]),
            "discomfort": mean(rb["discomfort"]),
            "discomfort_ci": ci95(rb["discomfort"]), "raw": rb}
    print(f"  电量 {rule['energy']:.3f} ± {rule['energy_ci']:.3f} kWh/天")
    print(f"  电费 {rule['cost']:.3f} ± {rule['cost_ci']:.3f} 元/天")
    print(f"  不达标 {rule['discomfort']:.2%} ± "
          f"{rule['discomfort_ci']:.2%}")
    print()

    # ---- RL 各档 ----
    res = {"rule": rule, "rl": {}, "scales": SCALES, "seeds": SEEDS}
    for sc in SCALES:
        per = []
        print(f"=== energy_scale = {sc:.0f} ===")
        for i, seed in enumerate(SEEDS, 1):
            ck = train(sc, seed)
            model = PPO.load(ck, device="cpu")

            def fn(env, _m=model):
                v = np.array(env.sim.to_vector(env.get_obs_dict()),
                             dtype=np.float32)
                return int(_m.predict(v, deterministic=True)[0])
            r = evaluate(fn, sc)
            per.append({"seed": seed,
                        "energy": mean(r["energy"]),
                        "cost": mean(r["cost"]),
                        "discomfort": mean(r["discomfort"])})
            print(f"  [{i:>2}/{len(SEEDS)}] seed={seed:<5} "
                  f"电量 {per[-1]['energy']:>6.3f}  "
                  f"电费 {per[-1]['cost']:>5.3f}  "
                  f"不达标 {per[-1]['discomfort']:>6.2%}")
        Es = [x["energy"] for x in per]
        Cs = [x["cost"] for x in per]
        Ds = [x["discomfort"] for x in per]
        res["rl"][str(sc)] = {
            "energy": mean(Es), "energy_ci": ci95(Es),
            "energy_min": min(Es), "energy_max": max(Es),
            "cost": mean(Cs), "cost_ci": ci95(Cs),
            "cost_min": min(Cs), "cost_max": max(Cs),
            "discomfort": mean(Ds), "discomfort_ci": ci95(Ds),
            "per_seed": per, "n": len(per)}
        s = res["rl"][str(sc)]
        print(f"  --> 电量 {s['energy']:.3f} ± {s['energy_ci']:.3f} "
              f"[{s['energy_min']:.3f}, {s['energy_max']:.3f}]")
        print(f"  --> 电费 {s['cost']:.3f} ± {s['cost_ci']:.3f} "
              f"[{s['cost_min']:.3f}, {s['cost_max']:.3f}]")
        print(f"  --> 不达标 {s['discomfort']:.2%} ± "
              f"{s['discomfort_ci']:.2%}")
        print()

    # ---- 对比 ----
    print("=" * 90)
    print(" 与规则对比（判据：RL 的 95% CI 上界 < 规则点估计 才算显著更省）")
    print("=" * 90)
    print(f"  规则: 电量 {rule['energy']:.3f}  电费 {rule['cost']:.3f}  "
          f"不达标 {rule['discomfort']:.2%}")
    print()
    print(f"  {'es':>5}{'电量':>10}{'电量CI上界':>12}{'省电?':>8}"
          f"{'电费':>10}{'电费CI上界':>12}{'省费?':>8}{'不达标':>10}")
    print("  " + "-" * 76)
    verdict = []
    for sc in SCALES:
        s = res["rl"][str(sc)]
        e_up = s["energy"] + s["energy_ci"]
        c_up = s["cost"] + s["cost_ci"]
        win_e = e_up < rule["energy"]
        win_c = c_up < rule["cost"]
        verdict.append({"scale": sc, "win_energy": win_e, "win_cost": win_c})
        print(f"  {sc:>5.0f}{s['energy']:>10.3f}{e_up:>12.3f}"
              f"{'✅' if win_e else '❌':>8}"
              f"{s['cost']:>10.3f}{c_up:>12.3f}"
              f"{'✅' if win_c else '❌':>8}"
              f"{s['discomfort']:>10.2%}")
    print()

    res["verdict"] = verdict
    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    md = ["# energy_scale 置信区间实验", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- energy_scale：{SCALES}",
          f"- 种子：{len(SEEDS)} 个；训练各 {TIMESTEPS:,} 步；"
          f"评估各 {EVAL_EPISODES} 回合",
          f"- 场景：启用分时电价，`lookahead_weight={LOOKAHEAD}`", "",
          "## 一、为什么需要这个实验", "",
          "此前的权重结论只有 2~5 个种子，无法判断"
          "\"RL 优于规则\"是否稳健。",
          "某些档位（如 `energy_scale=30`）在不同配置下给出 5.421 与 7.199 "
          "两种结果，差异达 33%。",
          "本实验用 10 个种子给出 95% 置信区间，并用"
          "**区间不重叠**作为保守的显著性判据。", "",
          "## 二、规则基线", "",
          "规则按分时电价区分设定温度：谷段 25 °C、平段 26 °C、峰段 28 °C；",
          "无人在房间时关闭空调。", "",
          f"| 指标 | 规则 |",
          "|---|---|",
          f"| 电量(kWh/天) | {rule['energy']:.3f} ± {rule['energy_ci']:.3f} |",
          f"| 电费(元/天) | {rule['cost']:.3f} ± {rule['cost_ci']:.3f} |",
          f"| 不达标率 | {rule['discomfort']:.2%} ± "
          f"{rule['discomfort_ci']:.2%} |", "",
          "## 三、各 energy_scale 的结果", "",
          "| energy_scale | 电量(kWh/天) | 电费(元/天) | 不达标率 |",
          "|---|---|---|---|"]
    for sc in SCALES:
        s = res["rl"][str(sc)]
        md.append(f"| {sc:.0f} | {s['energy']:.3f} ± {s['energy_ci']:.3f} | "
                  f"{s['cost']:.3f} ± {s['cost_ci']:.3f} | "
                  f"{s['discomfort']:.2%} ± {s['discomfort_ci']:.2%} |")
    md += ["", "## 四、与规则的对比（区间不重叠判据）", "",
           "判据：RL 的 95% 置信区间上界 < 规则的点估计，"
           "即两区间不重叠，才算显著更省。", "",
           "| energy_scale | 电量 CI 上界 | 比规则省电 | 电费 CI 上界 | 比规则省费 |",
           "|---|---|---|---|---|"]
    for v in verdict:
        s = res["rl"][str(v["scale"])]
        md.append(f"| {v['scale']:.0f} | "
                  f"{s['energy']+s['energy_ci']:.3f} | "
                  f"{'✅ 是' if v['win_energy'] else '❌ 否'} | "
                  f"{s['cost']+s['cost_ci']:.3f} | "
                  f"{'✅ 是' if v['win_cost'] else '❌ 否'} |")
    md += ["", "## 五、逐种子数据", ""]
    for sc in SCALES:
        md += [f"**energy_scale = {sc:.0f}**", "",
               "| 种子 | 电量 | 电费 | 不达标 |", "|---|---|---|---|"]
        for x in res["rl"][str(sc)]["per_seed"]:
            md.append(f"| {x['seed']} | {x['energy']:.3f} | "
                      f"{x['cost']:.3f} | {x['discomfort']:.2%} |")
        md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  总耗时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
