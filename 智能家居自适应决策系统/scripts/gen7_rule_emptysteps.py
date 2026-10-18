# -*- coding: utf-8 -*-
"""gen7：按 empty_steps 切阈值的规则（修正被我自己削弱的基线）。

问题
----
前两版规则族只让规则看 `p_arrive_soon` 做阈值判断。而该信号只有 7 个
不同取值：

    0.0, 0.013, 0.560, 0.608, 0.727, 0.764, 1.0

因此阈值 0.02~0.50 全部落在同一段里、行为完全相同，规则只能切出
"预冷"与"不预冷"两种极端，**无法产生 2.2%~33% 之间的中间工作点**。

而 RL 的观测里还有 `empty_steps`（47 个取值）—— 它可以做到更细的
分级。只给规则 `p_arrive_soon` 而不给 `empty_steps`，等于我自己把
基线削弱了，这样的对比对 RL 有利、不诚实。

本脚本
------
让规则按 `empty_steps >= T` 决定是否预冷（T 取 1~47 的加密网格），
并允许"分级预冷"：越接近回归、预冷越深。这样规则族才具备与 RL
同等的信息基础。

输出
----
docs/gen7空步规则基线.md
models/gen7_rule_emptysteps/results.json
"""

import json
import math
import os
import sys
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

from src.env import (RewardConfig, SimConfig, SmartHomeEnv,  # noqa: E402
                     encode_action)

AC_OFF = 0
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

# 按已离开步数切阈值
T_LIST = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 44, 45, 46, 47]
PRE_T = [23.0, 24.0, 25.0, 26.0, 27.0]
MAINTAIN_T = [25.0, 26.0]

OUT = os.path.join(PROJ, "models", "gen7_rule_emptysteps", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7空步规则基线.md")


def rew():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=10.0, lookahead_weight=0.6)


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def _hyst(env, target):
    od = env.get_obs_dict()
    T = float(od["temp_in"])
    cfg = env.act_cfg
    if target is None:
        return encode_action(AC_OFF, 0, 0, cfg)
    if T > target + 0.5:
        ac = ac_idx_for(target, cfg)
    elif T < target - 0.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if od["light_in"] < 300 else 0
    return encode_action(ac, light, 0, cfg)


def make_empty_rule(T, precool, maintain):
    """已离开步数 >= T 就预冷。

    empty_steps 有 47 个取值，因此 T 能切出 47 档不同行为 —— 这才是与
    RL 同等的信息基础。
    """
    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        e = float(od["empty_steps"])
        if occ:
            return _hyst(env, maintain)
        if e >= T:
            return _hyst(env, precool)
        if float(od["temp_in"]) <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, precool)
    return fn


def make_graded_rule(T_near, T_far, pre_near, pre_far, maintain):
    """分级预冷：已离开 >= T_near 预冷到 pre_near；
    >= T_far（更早）只预冷到 pre_far（较浅）。

    这更接近"连续决策"的能力，用来逼近 RL 的柔度。
    """
    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        e = float(od["empty_steps"])
        if occ:
            return _hyst(env, maintain)
        if e >= T_near:
            return _hyst(env, pre_near)
        if e >= T_far:
            return _hyst(env, pre_far)
        if float(od["temp_in"]) <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, pre_far)
    return fn


def measure(fn, n=EVAL_EPISODES):
    rc = rew()
    sc = SimConfig(occupancy_mode="semi_markov")
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            a = fn(env)
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


def pareto(rows):
    out = []
    for r in rows:
        if any((o["cost"] <= r["cost"] + 1e-9
                and o["occ_discomfort"] <= r["occ_discomfort"] + 1e-9
                and (o["cost"] < r["cost"] - 1e-9
                     or o["occ_discomfort"] < r["occ_discomfort"] - 1e-9))
               for o in rows):
            continue
        out.append(r)
    return sorted(out, key=lambda x: x["occ_discomfort"])


def main():
    rows = []
    print("=" * 88)
    print(" gen7：按 empty_steps 切阈值的规则（修正被削弱的基线）")
    print("=" * 88)
    print(f"  评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}~"
          f"{EVAL_SEED0+EVAL_EPISODES-1}")
    print(f"  T 取值 {len(T_LIST)} 档 × 预冷 {len(PRE_T)} × 维持 "
          f"{len(MAINTAIN_T)} = {len(T_LIST)*len(PRE_T)*len(MAINTAIN_T)} 组")
    print()

    for T in T_LIST:
        for pc in PRE_T:
            for mt in MAINTAIN_T:
                r = measure(make_empty_rule(T, pc, mt))
                r["name"] = f"空步>={T} 预冷{pc:.0f} 维持{mt:.0f}"
                r["family"] = "empty"
                r["T"] = T
                r["precool"] = pc
                r["maintain"] = mt
                rows.append(r)
        print(f"  T={T:>2} 完成（累计 {len(rows)} 条）")

    # 分级预冷（用更接近连续的决策逼近 RL 的柔度）
    for tn in [3, 5, 8, 15, 30, 44, 46]:
        for tf in [1, 2, 3, 5]:
            if tf >= tn:
                continue
            for pn in [23.0, 24.0, 25.0]:
                for pf in [25.0, 26.0]:
                    for mt in [25.0, 26.0]:
                        r = measure(make_graded_rule(tn, tf, pn, pf, mt))
                        r["name"] = (f"分级 近>={tn}远>={tf} "
                                     f"{pn:.0f}/{pf:.0f} 维持{mt:.0f}")
                        r["family"] = "graded"
                        rows.append(r)
    print(f"  分级规则完成（累计 {len(rows)} 条）")

    front = pareto(rows)
    print()
    print("=" * 88)
    print(f" 帕累托前沿：{len(front)} 点")
    print("=" * 88)
    print(f"  {'规则':<34}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
    print("  " + "-" * 64)
    for r in front:
        print(f"  {r['name']:<34}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    in_band = [r for r in front if 0.02 <= r["occ_discomfort"] <= 0.15]
    print()
    print(f"  === 2%~15% 区间内的前沿点（{len(in_band)} 个）===")
    for r in in_band:
        print(f"  {r['name']:<34}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"episodes": EVAL_EPISODES, "results": rows,
                   "front": front, "in_band_2_15": in_band},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：按 empty_steps 切阈值的规则基线", "",
          f"- 生成：2026-10-18",
          f"- 评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}~"
          f"{EVAL_SEED0+EVAL_EPISODES-1}", "",
          "## 为什么补这一族", "",
          "前两版规则只让规则看 `p_arrive_soon`，而该信号只有 7 个取值，"
          "阈值切不出中间档。而观测里还有 `empty_steps`（47 个取值）。"
          "只给规则前者、不给后者，等于削弱基线。", "",
          "## 帕累托前沿", "",
          "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in front:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 2%~15% 区间", ""]
    if in_band:
        md += ["| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
        for r in in_band:
            md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} "
                      f"| {r['occ_discomfort']:.1%} |")
    else:
        md.append("**仍无工作点。**")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
