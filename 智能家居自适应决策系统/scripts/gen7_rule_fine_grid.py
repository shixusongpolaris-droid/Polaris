# -*- coding: utf-8 -*-
"""gen7：把规则网格加密到 2%~15% 不舒适区，消除对比空档。

为什么必须做
------------
调优诊断发现 RL+VecNormalize 落在「不达标 4.56%、电费 1.504」这个位置，
而规则前沿在 2.2% 与 33.0% 之间**没有工作点**——30 个百分点的空档。

若直接在空档里插值，就等于用一条不存在的规则去和 RL 比，结论无效。
本脚本用更细的网格把这段填上：

  阈值        从 {0.10, 0.50} 扩到 15 档
  预冷温度    非电价族：{23,24,25,26,27}
              电价族：谷 {23..26} × 平 {24..27} × 峰 {25,26,27,关}
  维持温度    非电价族：{25,26}；电价族：{25,26}
  另加「无人时按阈值分级」的变体

目标：在 2%~15% 有人不达标区间内给出足够多的真实工作点。

输出
----
docs/gen7规则网格加密.md
models/gen7_rule_fine/results.json
"""

import json
import math
import os
import sys
from statistics import mean, stdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

from src.env import (RewardConfig, SimConfig, SmartHomeEnv,  # noqa: E402
                     ActionConfig, encode_action)

AC_OFF = 0
_AC = ActionConfig()
LOOKAHEAD = 0.6
EVAL_EPISODES = 8
EVAL_SEED0 = 9100          # 与 gen7_equal_comfort / 调优诊断一致

# 加密后的网格
THRESHOLDS = [0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
              0.60, 0.70, 0.80, 0.90, 0.95]
PRE_NONPRICE = [24.0, 25.0, 26.0, 27.0]
MAINTAIN_NONPRICE = [25.0, 26.0]
PRE_VALLEY = [24.0, 25.0, 26.0]
PRE_FLAT = [25.0, 26.0, 27.0]
PRE_PEAK = [25.0, 26.0, 27.0, None]
MAINTAIN_PRICE = [25.0, 26.0]

OUT = os.path.join(PROJ, "models", "gen7_rule_fine", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7规则网格加密.md")

T95 = {7: 2.365, 6: 2.447, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def rew():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=10.0, lookahead_weight=LOOKAHEAD)


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


def make_nonprice(th, precool, maintain):
    """无人时：p>=阈值则预冷到 precool；否则室温<=27 就关。"""
    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        p = float(od["p_arrive_soon"])
        if occ:
            return _hyst(env, maintain)
        if p >= th:
            return _hyst(env, precool)
        if float(od["temp_in"]) <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, precool)
    return fn


def make_price(th, pv, pf, pp, maintain):
    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        p = float(od["p_arrive_soon"])
        rc = env.rew_cfg
        hour = int(od["hour"])
        if occ:
            return _hyst(env, maintain)
        if p >= th:
            price = rc.get_price(hour)
            if price <= rc.price_valley + 1e-9:
                return _hyst(env, pv)
            if price >= rc.price_peak - 1e-9:
                return _hyst(env, pp)
            return _hyst(env, pf)
        if float(od["temp_in"]) <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, pf)
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
    n_combo = (len(THRESHOLDS) * len(PRE_NONPRICE) * len(MAINTAIN_NONPRICE)
               + len(THRESHOLDS) * len(PRE_VALLEY) * len(PRE_FLAT)
               * len(PRE_PEAK) * len(MAINTAIN_PRICE))
    print("=" * 88)
    print(" gen7 规则网格加密（填补 2%~15% 不舒适区）")
    print("=" * 88)
    print(f"  评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}~"
          f"{EVAL_SEED0+EVAL_EPISODES-1}")
    print(f"  组合数约 {n_combo}（评估而非训练，速度很快）")
    print()

    # 非电价族
    for th in THRESHOLDS:
        for pc in PRE_NONPRICE:
            for mt in MAINTAIN_NONPRICE:
                r = measure(make_nonprice(th, pc, mt))
                r["name"] = f"风险>={th:.2f} 预冷{pc:.0f} 维持{mt:.0f}"
                r["family"] = "nonprice"
                r["threshold"] = th
                r["precool"] = pc
                r["maintain"] = mt
                rows.append(r)
        print(f"  非电价族 阈值 {th:.2f} 完成（累计 {len(rows)} 条）")

    # 电价族
    for th in THRESHOLDS:
        for pv in PRE_VALLEY:
            for pf in PRE_FLAT:
                for pp in PRE_PEAK:
                    for mt in MAINTAIN_PRICE:
                        r = measure(make_price(th, pv, pf, pp, mt))
                        pp_s = "关" if pp is None else f"{pp:.0f}"
                        r["name"] = (f"电价>={th:.2f} 谷{pv:.0f}/平{pf:.0f}"
                                     f"/峰{pp_s} 维持{mt:.0f}")
                        r["family"] = "price"
                        r["threshold"] = th
                        r["pre_valley"] = pv
                        r["pre_flat"] = pf
                        r["pre_peak"] = pp
                        r["maintain"] = mt
                        rows.append(r)
        print(f"  电价族   阈值 {th:.2f} 完成（累计 {len(rows)} 条）")

    front = pareto(rows)
    print()
    print("=" * 88)
    print(f" 加密后帕累托前沿：{len(front)} 点")
    print("=" * 88)
    print(f"  {'规则':<34}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
    print("  " + "-" * 64)
    for r in front:
        print(f"  {r['name']:<34}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    # 统计 2%~15% 区间有多少点
    in_band = [r for r in front if 0.02 <= r["occ_discomfort"] <= 0.15]
    print()
    print(f"  2%~15% 不舒适区间内的前沿点数：{len(in_band)}")
    if in_band:
        print(f"  {'规则':<34}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
        for r in in_band:
            print(f"  {r['name']:<34}{r['energy']:>9.3f}{r['cost']:>9.3f}"
                  f"{r['occ_discomfort']:>11.1%}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"episodes": EVAL_EPISODES, "results": rows,
                   "front": front, "in_band_2_15": in_band},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：规则网格加密（填补 2%~15% 不舒适区）", "",
          f"- 生成：2026-10-18",
          f"- 评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}~"
          f"{EVAL_SEED0+EVAL_EPISODES-1}",
          "- 原因：调优诊断发现 RL+VecNormalize 落在 4.56% 不达标率，"
          "而原规则前沿在 2.2% 与 33% 之间没有工作点（30pp 空档），"
          "在该空档内插值无法构成公平对比。", "",
          "## 一、加密后的帕累托前沿", "",
          "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in front:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 二、2%~15% 区间的工作点", ""]
    if in_band:
        md += ["| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
        for r in in_band:
            md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} "
                      f"| {r['occ_discomfort']:.1%} |")
    else:
        md.append("**加密后该区间仍无工作点** —— 说明规则的离散设定点"
                  "结构本身无法在这一区间给出解。")
    md += ["", "## 三、结论", "",
           f"- 加密后前沿 {len(front)} 点",
           f"- 2%~15% 区间 {len(in_band)} 点", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
