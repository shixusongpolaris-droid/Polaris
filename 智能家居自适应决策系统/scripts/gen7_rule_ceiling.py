# -*- coding: utf-8 -*-
"""gen7：在"可预测回家"的环境里，风险阈值规则的能力上限。

为什么要有这一步
----------------
gen1~gen6 的占用是 i.i.d. 的，"预测回家"在数学上不可能，所以
"一直开着"就是最优、规则自然赢。gen7 改为半马尔可夫占用，
观测里给出 empty_steps 与 p_arrive_soon，预测才真正变得可能。

但**光有信号不等于 RL 就赢** —— 一条会读这个信号的阈值规则
（"p_arrive_soon > 阈值就开始预冷"）同样能吃下这份收益。
因此必须先把这个规则族的上限算出来，否则又是与稻草人比较。

本脚本
------
1. 定义并评估"风险阈值规则"族：
       p_arrive_soon >= 阈值  ->  设预冷温度
       有人在                 ->  设维持温度
       其余                   ->  关空调
2. 同时评估既有基线（全开 25、25/无人关、谷平峰规则）
3. 输出帕累托前沿与等舒适度所需的数据

不训练任何模型，只评估规则（约 2~3 分钟）。
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
AC_25C = _AC.ac_setpoints.index(25.0) + 1
AC_26C = _AC.ac_setpoints.index(26.0) + 1
AC_27C = _AC.ac_setpoints.index(27.0) + 1
AC_28C = _AC.ac_setpoints.index(28.0) + 1

LOOKAHEAD = 0.6
EVAL_EPISODES = 12
EVAL_SEED0 = 9600

# 网格
THRESHOLDS = [0.10, 0.25, 0.50, 0.70, 0.90]
PRECOOL_T = [23.0, 24.0, 25.0]
MAINTAIN_T = [25.0, 26.0]

OUT = os.path.join(PROJ, "models", "gen7_rule_ceiling", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7风险规则上限.md")

T95 = {11: 2.201, 9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(values):
    n = len(values)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(values) / math.sqrt(n)


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


# ---------------- 规则族 ----------------
def make_risk_rule(threshold, precool_t, maintain_t):
    """风险阈值规则：快要回来就预冷，有人在就维持，其余关机。"""
    def fn(env):
        od = env.get_obs_dict()
        T = od["temp_in"]
        occ = bool(od["occ_a"] or od["occ_b"])
        p = od["p_arrive_soon"]
        cfg = env.act_cfg

        if occ:
            target = maintain_t
        elif p >= threshold:
            target = precool_t          # 预冷
        else:
            # 不预冷：房间已经够凉就不开
            if T <= 27.0:
                return encode_action(AC_OFF, 0, 0, cfg)
            target = precool_t

        # 滞环 ±0.5
        if T > target + 0.5:
            ac = ac_idx_for(target, cfg)
        elif T < target - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if od["light_in"] < 300 else 0
        return encode_action(ac, light, 0, cfg)
    return fn


def make_always_on(target_t):
    def fn(env):
        od = env.get_obs_dict()
        T = od["temp_in"]
        cfg = env.act_cfg
        if T > target_t + 0.5:
            ac = ac_idx_for(target_t, cfg)
        elif T < target_t - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if od["light_in"] < 300 else 0
        return encode_action(ac, light, 0, cfg)
    return fn


def make_off_when_empty(target_t):
    def fn(env):
        od = env.get_obs_dict()
        cfg = env.act_cfg
        if not (od["occ_a"] or od["occ_b"]):
            return encode_action(AC_OFF, 0, 0, cfg)
        T = od["temp_in"]
        if T > target_t + 0.5:
            ac = ac_idx_for(target_t, cfg)
        elif T < target_t - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if od["light_in"] < 300 else 0
        return encode_action(ac, light, 0, cfg)
    return fn


# ---------------- 评估 ----------------
def evaluate(fn, n=EVAL_EPISODES):
    """评估一条规则，返回日均电量、电费、总/有人不达标率。

    口径与 `multiseed_equal_comfort.py` 保持一致（便于跨脚本比较）：
      · 电费按当步电价逐笔累加后除以天数
      · 不达标判定在 step 之后读取观测（与既有脚本同约定）
      · 有人不达标率只统计有人在房间的步数
    """
    rc = rew()
    sc = SimConfig(occupancy_mode="semi_markov")
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    sw = 0
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
                sw += float(m.get("switch_per_day", 0.0))
                break
        env.close()
    return {"energy": mean(E), "energy_ci": ci95(E),
            "cost": mean(C), "cost_ci": ci95(C),
            "discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1),
            "switch": sw / n}


def main():
    rows = []

    print("=" * 90)
    print(" gen7：风险阈值规则的能力上限（semi_markov 占用，分时电价）")
    print("=" * 90)
    print(f"  评估 {EVAL_EPISODES} 回合，回合长 7 天")
    print()

    base = [
        ("全开 25°C", make_always_on(25.0)),
        ("全开 26°C", make_always_on(26.0)),
        ("25°C/无人关", make_off_when_empty(25.0)),
        ("26°C/无人关", make_off_when_empty(26.0)),
    ]
    for name, fn in base:
        r = evaluate(fn)
        r["name"] = name
        rows.append(r)

    for th in THRESHOLDS:
        for pc in PRECOOL_T:
            for mt in MAINTAIN_T:
                name = f"风险>={th:.2f} 预冷{pc:.0f} 维持{mt:.0f}"
                r = evaluate(make_risk_rule(th, pc, mt))
                r["name"] = name
                r["threshold"] = th
                r["precool"] = pc
                r["maintain"] = mt
                rows.append(r)

    print(f"  {'规则':<30}{'电量':>9}{'电费':>9}{'总不达标':>10}"
          f"{'有人不达标':>11}{'切换':>8}")
    print("  " + "-" * 78)
    for r in sorted(rows, key=lambda x: x["cost"]):
        print(f"  {r['name']:<30}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['discomfort']:>10.1%}{r['occ_discomfort']:>11.1%}"
              f"{r['switch']:>8.1f}")

    # 帕累托（按电费与有人不达标）
    front = []
    for r in rows:
        dom = any((o["cost"] <= r["cost"] + 1e-9
                   and o["occ_discomfort"] <= r["occ_discomfort"] + 1e-9
                   and (o["cost"] < r["cost"] - 1e-9
                        or o["occ_discomfort"] < r["occ_discomfort"] - 1e-9))
                  for o in rows)
        if not dom:
            front.append(r)
    front.sort(key=lambda x: x["cost"])

    print()
    print("=" * 90)
    print(f" 帕累托前沿（按电费 × 有人不达标），共 {len(front)} 点")
    print("=" * 90)
    print(f"  {'规则':<30}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
    print("  " + "-" * 62)
    for r in front:
        print(f"  {r['name']:<30}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    print()
    best_comfort = min(rows, key=lambda x: (x["occ_discomfort"], x["cost"]))
    print(f"  最佳舒适度：{best_comfort['name']} —— "
          f"有人不达标 {best_comfort['occ_discomfort']:.2%}，"
          f"电费 {best_comfort['cost']:.3f} 元/天")
    print()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"episodes": EVAL_EPISODES, "results": rows,
                   "front": front}, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {OUT}")
    print(f"  报告生成中 ...")

    md = ["# gen7：风险阈值规则的能力上限", "",
          f"- 生成：2026-10-18",
          f"- 场景：semi_markov 占用 + 分时电价，{EVAL_EPISODES} 回合 × 7 天",
          "- 目的：在「可预测回家」的环境里，先算出规则族能吃下多少收益，",
          "  避免再用稻草人规则做对比。", "",
          "## 一、规则定义", "",
          "```",
          "有人在        ->  维持 target 温度（滞环 ±0.5°C）",
          "无人且 p>=阈值 ->  设为预冷温度",
          "其余          ->  室温 >27°C 时也预冷，否则关机",
          "```", "",
          "## 二、全部结果", "",
          "| 规则 | 电量(kWh/天) | 电费(元/天) | 总不达标 | 有人不达标 | 切换(次/天) |",
          "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["cost"]):
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['discomfort']:.1%} | {r['occ_discomfort']:.1%} | "
                  f"{r['switch']:.1f} |")
    md += ["", "## 三、帕累托前沿（按电费 × 有人不达标）", "",
           "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in front:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 四、结论", "",
           f"- 最佳舒适度：**{best_comfort['occ_discomfort']:.2%}**"
           f"（{best_comfort['name']}），电费 {best_comfort['cost']:.3f} 元/天",
           f"- 规则前沿共 **{len(front)}** 点", "",
           "**这份前沿就是强化学习在 gen7 场景下的真正对手。**", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
