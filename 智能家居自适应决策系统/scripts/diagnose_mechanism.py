# -*- coding: utf-8 -*-
"""诊断：为什么"全程开空调"仍不舒适，以及为什么强化学习反而更费电。

背景疑问
--------
标定后出现两个反直觉结果：
  1. "全程开空调 25°C" 仍有 16.8% 的不舒适率
  2. 强化学习的日均电量（6.58 kWh）高于"全程开空调"（6.19 kWh）

本脚本直接追踪温度轨迹、空调开启比例与不舒适的来源，给出机制解释，
不做任何策略改动，只做观测。

输出
----
docs/仿真机制诊断.md
"""

import json
import os
import sys
import collections

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                        # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from stable_baselines3 import PPO                          # noqa: E402

# 动作编号与各实验脚本保持一致（见 scripts/evaluate_all.py 第 69-70 行）
AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5

OUT_MD = os.path.join(PROJ, "docs", "仿真机制诊断.md")
EPISODES = 10
SEED0 = 3000


def trace(env, action_fn, n=EPISODES, seed0=SEED0):
    """追踪温度、空调开启、不舒适来源。"""
    temps, occs, ac_on_flags, devs, tiers = [], [], [], [], []
    energy_total = 0.0
    steps_total = 0
    hot = cold = 0
    dev_steps = 0

    for ep in range(n):
        env.reset(seed=seed0 + ep)
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occupied = bool(od["occ_a"] or od["occ_b"])
            hour = int(od.get("hour", 0))
            temps.append(T)
            occs.append(occupied)
            dev = float(c.get("temp_dev", 0.0))
            devs.append(dev)
            # 判断过热还是过冷（区间随占用与睡眠变化）
            if dev > 0:
                dev_steps += 1
                band = env.rew.comfort.temp_band(occupied, hour) \
                    if hasattr(env, "rew") else None
                # 用温度与常用区间比较
                lo = 24.0 if (occupied and (hour >= 23 or hour < 7)) else (
                    22.0 if occupied else 16.0)
                hi = 27.0 if (occupied and (hour >= 23 or hour < 7)) else (
                    26.0 if occupied else 30.0)
                if T > hi:
                    hot += 1
                elif T < lo:
                    cold += 1
            ac_on_flags.append(int(c.get("ac_on", 0))
                               if "ac_on" in c else None)
            energy_total += float(c.get("energy_kwh", 0.0))
            steps_total += 1
            if term or trunc:
                break
    days = steps_total * env.sim_cfg.dt_minutes / 60.0 / 24.0
    return {
        "energy_per_day": energy_total / days,
        "temp_mean": float(np.mean(temps)),
        "temp_min": float(np.min(temps)),
        "temp_max": float(np.max(temps)),
        "temp_p05": float(np.percentile(temps, 5)),
        "temp_p95": float(np.percentile(temps, 95)),
        "discomfort_ratio": dev_steps / steps_total,
        "hot_steps": hot,
        "cold_steps": cold,
        "occ_ratio": float(np.mean(occs)),
        "occ_temp_mean": float(np.mean([t for t, o in zip(temps, occs) if o]))
        if any(occs) else None,
        "unocc_temp_mean": float(np.mean([t for t, o in zip(temps, occs)
                                          if not o]))
        if not all(occs) else None,
        "n_steps": steps_total,
    }


def main():
    rc = RewardConfig(weight_mode="scenario")
    env = SmartHomeEnv(rew_cfg=rc, seed=SEED0)

    print("=" * 78)
    print(" 仿真机制诊断")
    print("=" * 78)
    print(f"  热参数：ua_coef={env.sim_cfg.ua_coef}, "
          f"internal_gain={env.sim_cfg.internal_gain}, "
          f"ac_coef={env.sim_cfg.ac_coef}, "
          f"ac_max_cooling={env.sim_cfg.ac_max_cooling}")
    print(f"  室外温度：均值 {env.sim_cfg.temp_outdoor_mean}"
          f" ± {env.sim_cfg.temp_outdoor_swing}")
    print()

    # 理论平衡点
    ua = env.sim_cfg.ua_coef
    gain = env.sim_cfg.internal_gain
    ac = env.sim_cfg.ac_coef
    Tout = env.sim_cfg.temp_outdoor_mean
    T_eq_occ = (ua * Tout + gain + ac * 25.0) / (ua + ac)
    T_eq_unocc = (ua * Tout + ac * 25.0) / (ua + ac)
    T_no_ac = Tout + gain / ua
    print("  理论平衡室温（一阶模型）：")
    print(f"    空调全开@25°C 且有人：{T_eq_occ:.2f} °C")
    print(f"    空调全开@25°C 且无人：{T_eq_unocc:.2f} °C")
    print(f"    完全不开空调 且有人：{T_no_ac:.2f} °C")
    print(f"    有人舒适区间：22 ~ 26 °C")
    print()

    results = {}

    # A. 全程开空调 25°C
    r = trace(env, lambda e: encode_action(AC_25C, 0, 0, e.act_cfg))
    results["全程开空调25°C"] = r
    # B. 全程开空调 27°C
    r2 = trace(env, lambda e: encode_action(AC_27C, 0, 0, e.act_cfg))
    results["全程开空调27°C"] = r2
    # C. 全程不开空调
    r3 = trace(env, lambda e: encode_action(AC_OFF, 0, 0, e.act_cfg))
    results["全程不开空调"] = r3

    # D. 强化学习
    mp = os.path.join(PROJ, "models", "ms_flat_t1_s42", "final_model.zip")
    if os.path.exists(mp):
        model = PPO.load(mp, device="cpu")

        def ppo_fn(e):
            v = np.array(e.sim.to_vector(e.get_obs_dict()), dtype=np.float32)
            return int(model.predict(v, deterministic=True)[0])
        results["强化学习(PPO)"] = trace(env, ppo_fn)

    print("=" * 78)
    print(f"  {'策略':<20}{'电量':>8}{'温度均值':>9}{'温度区间':>16}"
          f"{'不达标':>8}{'过热':>7}{'过冷':>7}")
    print("  " + "-" * 74)
    for k, v in results.items():
        print(f"  {k:<20}{v['energy_per_day']:>8.2f}{v['temp_mean']:>9.2f}"
              f"{v['temp_min']:>8.1f}~{v['temp_max']:<7.1f}"
              f"{v['discomfort_ratio']:>8.1%}{v['hot_steps']:>7}{v['cold_steps']:>7}")
    print()
    print("  （过热/过冷为超出舒适区间的步数统计）")
    print()

    print("=" * 78)
    print(" 占用时与无人时的温度对比")
    print("=" * 78)
    print(f"  {'策略':<20}{'有人时温度':>12}{'无人时温度':>12}{'有人在室比例':>14}")
    print("  " + "-" * 60)
    for k, v in results.items():
        ot = v["occ_temp_mean"]
        ut = v["unocc_temp_mean"]
        print(f"  {k:<20}{(f'{ot:.2f} °C' if ot else '-'):>12}"
              f"{(f'{ut:.2f} °C' if ut else '-'):>12}"
              f"{v['occ_ratio']:>13.1%}")

    # ---- 结论 ----
    md = ["# 仿真机制诊断", "",
          "回答两个反直觉现象：", "",
          "1. 为什么\"全程开空调 25°C\"仍有明显不舒适率？",
          "2. 为什么强化学习的日均电量高于\"全程开空调\"？", "",
          "## 一、理论平衡点（一阶热模型）", "",
          "在没有空调时，室内温度由围护传热与内部热增益平衡决定：",
          "",
          "```",
          f"T_平衡(无空调,有人) = T_室外 + internal_gain / ua_coef",
          f"                    = {Tout} + {gain} / {ua} = {T_no_ac:.2f} °C",
          "```",
          "",
          "空调开启时按 `dT -= min(ac_coef·(T − 设定温度), ac_max_cooling)` 制冷，",
          "因此平衡温度由\"传热+内热\"与\"制冷能力\"共同决定：",
          "",
          "```",
          f"T_平衡(空调@25°C,有人) = {T_eq_occ:.2f} °C",
          f"T_平衡(空调@25°C,无人) = {T_eq_unocc:.2f} °C",
          "```",
          "",
          f"**关键：有人时即使空调全开在 25°C，平衡温度仍为 {T_eq_occ:.2f} °C**，",
          "高于有人舒适区间上限 26 °C。",
          "原因是标定后的 `internal_gain = 0.80`（内部热源强）",
          "而 `ac_coef = 0.30` 的制冷能力不足以把温度压到 25 °C。", "",
          "## 二、实测追踪", "",
          "| 策略 | 电量(kWh/天) | 温度均值 | 温度区间 | 不达标率 | 过热步数 | 过冷步数 |",
          "|---|---|---|---|---|---|---|"]
    for k, v in results.items():
        md.append(f"| {k} | {v['energy_per_day']:.2f} | {v['temp_mean']:.2f} °C | "
                  f"{v['temp_min']:.1f}~{v['temp_max']:.1f} °C | "
                  f"{v['discomfort_ratio']:.1%} | {v['hot_steps']} | {v['cold_steps']} |")
    md += ["", "## 三、有人时 / 无人时温度", "",
           "| 策略 | 有人时温度 | 无人时温度 | 有人在室比例 |",
           "|---|---|---|---|"]
    for k, v in results.items():
        ot = f"{v['occ_temp_mean']:.2f} °C" if v["occ_temp_mean"] else "-"
        ut = f"{v['unocc_temp_mean']:.2f} °C" if v["unocc_temp_mean"] else "-"
        md.append(f"| {k} | {ot} | {ut} | {v['occ_ratio']:.1%} |")
    md.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(PROJ, "models", "mechanism_diag.json"), "w",
              encoding="utf-8") as f:
        json.dump({"sim": {
            "ua_coef": ua, "internal_gain": gain, "ac_coef": ac,
            "temp_outdoor_mean": Tout,
            "T_eq_occ": T_eq_occ, "T_eq_unocc": T_eq_unocc, "T_no_ac": T_no_ac,
        }, "results": results}, f, ensure_ascii=False, indent=2)

    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
