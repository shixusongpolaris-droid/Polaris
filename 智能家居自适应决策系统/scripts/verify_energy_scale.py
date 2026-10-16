# -*- coding: utf-8 -*-
"""验证：提高能耗权重能否让强化学习在能耗上也优于规则控制。

背景
----
gen3（联合标定后）的权重扫描发现一个关键现象：

    energy_scale = 10（默认）  ->  RL 电费比规则高 249%
    energy_scale = 30          ->  高 199%
    energy_scale = 60          ->  仅高 6.6%，且
                                    电量 2.084 vs 规则 2.439 kWh（少 14.6%）
                                    不达标 10.2% vs 规则 24.5%（好 14.3pp）

而 `RewardConfig.energy_scale` 的默认值正是 10.0，有效能耗权重
= `lambda_energy(0.5) × energy_scale(10)` = 5.0。

也就是说，"强化学习电费高于规则"这一结论可能只是**能耗权重设置偏低**
造成的，而非方法本身的缺陷。本脚本用多种子重复来验证。

实验设计
--------
对 energy_scale ∈ {10, 30, 60, 100} × 5 个随机种子：
  · 训练 PPO（400k 步）
  · 在相同场景下评估：电量、不达标率、电费、切换次数

同时评估规则基线（有人开无人关 + 滞环恒温 25°C）。

关键判据
--------
  · 电量：RL 是否真的比规则少
  · 舒适度：RL 的不达标率是否更低
  · 等舒适度电费：把 RL 曲线插值到规则的不达标率处，比较电费
    （这是项目既有的"等舒适度比较"口径）

输出
----
docs/能耗权重验证.md
models/energy_scale_verify/results.json
"""

import json
import os
import sys
import time
from datetime import datetime
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402
from stable_baselines3.common.monitor import Monitor   # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "energy_scale_verify")
OUT_MD = os.path.join(PROJ, "docs", "能耗权重验证.md")

AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5

SCALES = [10.0, 30.0, 60.0, 100.0]
SEEDS = [42, 66, 2026, 7, 123]
TIMESTEPS = 400_000
EVAL_EPISODES = 12
SEED0 = 5000


def make_rew_cfg(energy_scale=None, weight_mode="scenario"):
    rc = RewardConfig(weight_mode=weight_mode)
    if energy_scale is not None:
        rc.energy_scale = float(energy_scale)
    return rc


def thermostat(env, target, deadband=0.5):
    T = env.sim.state.temp_in
    if T > target + deadband:
        return 2
    if T < target - deadband:
        return 0
    return 1


def policy_rule(env):
    """规则控制：有人维持 25°C，无人关闭。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    return encode_action(thermostat(env, 25.0), 0, 0, env.act_cfg)


def evaluate(env_maker, action_fn, n_episodes=EVAL_EPISODES, seed0=SEED0):
    E, C, D, S = [], [], [], []
    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                D.append(float(m.get("discomfort_ratio", 0.0)))
                S.append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {"energy": mean(E), "energy_sd": pstdev(E),
            "cost": mean(C), "cost_sd": pstdev(C),
            "discomfort": mean(D), "discomfort_sd": pstdev(D),
            "switch": mean(S), "n": len(E)}


def train_one(energy_scale, seed, tag):
    """训练一个 PPO 并返回评估结果。"""
    MODEL = os.path.join(OUT_DIR, tag)
    os.makedirs(MODEL, exist_ok=True)
    ckpt = os.path.join(MODEL, "final_model.zip")

    def mk():
        rc = make_rew_cfg(energy_scale)
        return Monitor(SmartHomeEnv(rew_cfg=rc, seed=seed))
    venv = DummyVecEnv([mk])
    if os.path.exists(ckpt):
        model = PPO.load(ckpt, env=venv, device="cpu")
    else:
        model = PPO("MlpPolicy", venv, verbose=0, seed=seed,
                    n_steps=2048, batch_size=256, learning_rate=3e-4,
                    gamma=0.99, gae_lambda=0.95, ent_coef=0.01,
                    device="cpu")
        model.learn(total_timesteps=TIMESTEPS)
        model.save(ckpt)
    venv.close()

    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(v, deterministic=True)[0])

    return evaluate(lambda: SmartHomeEnv(rew_cfg=make_rew_cfg(energy_scale),
                                         seed=SEED0), fn)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print("=" * 82)
    print(" 验证：提高能耗权重能否让强化学习在能耗上优于规则控制")
    print("=" * 82)
    print(f"  energy_scale 取值：{SCALES}")
    print(f"  种子：{SEEDS}    每种子 {TIMESTEPS:,} 步")
    print(f"  评估 {EVAL_EPISODES} 回合")
    print()

    # 规则基线（与 energy_scale 无关）
    rule = evaluate(lambda: SmartHomeEnv(
        rew_cfg=make_rew_cfg(10.0), seed=SEED0), policy_rule)
    print(f"  规则基线：电量 {rule['energy']:.3f} kWh/天  "
          f"不达标 {rule['discomfort']:.1%}  电费 {rule['cost']:.3f} 元/天  "
          f"切换 {rule['switch']:.1f}")
    print()

    results = {"rule": rule, "scales": {}}
    for es in SCALES:
        key = f"es{int(es)}"
        results["scales"][key] = {"energy_scale": es, "seeds": {}}
        print(f"  --- energy_scale = {es:.0f} ---")
        for seed in SEEDS:
            tag = f"{key}_s{seed}"
            r = train_one(es, seed, tag)
            results["scales"][key]["seeds"][str(seed)] = r
            print(f"      s{seed:<6} 电量 {r['energy']:.3f}  "
                  f"不达标 {r['discomfort']:.1%}  "
                  f"电费 {r['cost']:.3f}  切换 {r['switch']:.1f}")
        e = [v["energy"] for v in results["scales"][key]["seeds"].values()]
        d = [v["discomfort"] for v in results["scales"][key]["seeds"].values()]
        c = [v["cost"] for v in results["scales"][key]["seeds"].values()]
        results["scales"][key]["summary"] = {
            "energy": mean(e), "energy_sd": pstdev(e) if len(e) > 1 else 0,
            "discomfort": mean(d), "cost": mean(c),
        }
        print(f"      均值：电量 {mean(e):.3f}±"
              f"{(pstdev(e) if len(e)>1 else 0):.3f}  "
              f"不达标 {mean(d):.1%}  电费 {mean(c):.3f}")
        print()

    # ---------- 汇总 ----------
    print("=" * 82)
    print(" 汇总对比")
    print("=" * 82)
    print(f"  {'能耗权重':<12}{'电量kWh/天':>14}{'不达标':>10}{'电费元/天':>12}"
          f"{'电量 vs 规则':>14}{'舒适度差':>11}")
    print("  " + "-" * 74)
    print(f"  {'规则控制':<12}{rule['energy']:>14.3f}{rule['discomfort']:>10.1%}"
          f"{rule['cost']:>12.3f}{'—':>14}{'—':>11}")
    rows = []
    for es in SCALES:
        key = f"es{int(es)}"
        s = results["scales"][key]["summary"]
        de = (s["energy"] - rule["energy"]) / rule["energy"]
        dd = s["discomfort"] - rule["discomfort"]
        rows.append({"es": es, **s, "d_energy": de, "d_disc": dd})
        print(f"  {es:<12.0f}{s['energy']:>14.3f}{s['discomfort']:>10.1%}"
              f"{s['cost']:>12.3f}{de:>+13.1%}{dd:>+10.1%}")

    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    md = ["# 能耗权重验证", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 种子：{SEEDS}，每种子 {TIMESTEPS:,} 步，评估 {EVAL_EPISODES} 回合",
          f"- 热参数：gen3 全时段联合标定值", "",
          "## 一、为什么要验证", "",
          "`RewardConfig.energy_scale` 的**默认值是 10.0**，",
          "有效能耗权重 = `lambda_energy(0.5) × energy_scale(10)` = 5.0。",
          "而权重扫描发现 `energy_scale = 60` 时强化学习在电量上反超规则。", "",
          "\"强化学习电费高于规则\"这一结论可能只是**能耗权重偏低**造成的，",
          "而非方法本身的缺陷，需要用多种子重复确认。", "",
          "## 二、结果", "",
          "| 能耗权重 | 电量(kWh/天) | 不达标 | 电费(元/天) | 电量 vs 规则 | 舒适度差 |",
          "|---|---|---|---|---|---|",
          f"| **规则控制** | **{rule['energy']:.3f}** | "
          f"**{rule['discomfort']:.1%}** | **{rule['cost']:.3f}** | — | — |"]
    for r in rows:
        md.append(f"| energy_scale={r['es']:.0f} | {r['energy']:.3f} | "
                  f"{r['discomfort']:.1%} | {r['cost']:.3f} | "
                  f"{r['d_energy']:+.1%} | {r['d_disc']:+.1%} |")
    md += ["", "## 三、逐种子明细", ""]
    for es in SCALES:
        key = f"es{int(es)}"
        md += [f"**energy_scale = {es:.0f}**", "",
               "| 种子 | 电量 | 不达标 | 电费 | 切换 |",
               "|---|---|---|---|---|"]
        for seed, v in results["scales"][key]["seeds"].items():
            md.append(f"| {seed} | {v['energy']:.3f} | {v['discomfort']:.1%} | "
                      f"{v['cost']:.3f} | {v['switch']:.1f} |")
        md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  总耗时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
