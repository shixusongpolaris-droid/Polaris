# -*- coding: utf-8 -*-
"""用 10 个种子做统计上可靠的等舒适度对比。

问题
----
此前的 `equal_comfort_dual.py`、`why_rl_costs_more.py`、
`discomfort_breakdown.py` 都只用了 seed=42 的单个模型。
而多种子验证显示：同一配置下"有人时段不达标率"可从 0.00% 变到 34.59%。

因此那些单种子分析不可靠。本脚本改用 `energy_scale_ci` 已训练好的
**40 个模型（4 档 × 10 种子）**，统计每档的：

    · 有人时段不达标率          mean ± 95% CI
    · 无人时段耗电（判定是否在给空房间制冷）
    · 总电量 / 电费

并与规则"全开 25 °C"及规则前沿对比。

对规则侧的说明
--------------
规则不涉及训练，其方差只来自评估回合，已在 `rule_ceiling` 中给出。

输出
----
docs/多种子等舒适度对比.md
models/multiseed_equal_comfort.json
"""

import json
import math
import os
import sys
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
from src.env.config import ActionConfig                # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402

OUT_JSON = os.path.join(PROJ, "models", "multiseed_equal_comfort.json")
OUT_MD = os.path.join(PROJ, "docs", "多种子等舒适度对比.md")

SCALES = [10.0, 30.0, 60.0, 100.0]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]
EVAL_EPISODES = 8
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6
AC_OFF = 0
T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


def ci95(v):
    n = len(v)
    if n < 2:
        return 0.0
    return T95.get(n - 1, 1.96) * stdev(v) / math.sqrt(n)


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(sp, keep_on=True):
    def fn(env):
        obs = env.get_obs_dict()
        if not keep_on and not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        T = obs["temp_in"]
        if T > sp + 0.5:
            ac = ac_idx_for(sp, env.act_cfg)
        elif T < sp - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def rew_cfg():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=100.0, lookahead_weight=LOOKAHEAD)


def measure(action_fn, n=EVAL_EPISODES):
    rc = rew_cfg()
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    emp_e = 0.0
    ac_on_emp = emp_n = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            e = float(info["components"]["energy_kwh"])
            if not occ:
                emp_e += e
                emp_n += 1
                if env.sim.state.ac_idx > 0:
                    ac_on_emp += 1
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
    days_tot = n * 7.0
    return {"energy": mean(E), "cost": mean(C),
            "total_discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1),
            "emp_energy": emp_e / days_tot,
            "ac_on_emp": ac_on_emp / max(emp_n, 1)}


def main():
    print("=" * 96)
    print(" 多种子等舒适度对比（40 个模型：4 档 × 10 种子）")
    print("=" * 96)
    print()

    # 规则基线
    rule = measure(make_rule(25.0, True))
    rule_off = measure(make_rule(25.0, False))
    print("=== 规则基线 ===")
    print(f"  全开 25°C      电量 {rule['energy']:.3f}  电费 {rule['cost']:.3f}  "
          f"总不达标 {rule['total_discomfort']:.2%}  "
          f"有人不达标 {rule['occ_discomfort']:.2%}")
    print(f"  25°C/无人关机  电量 {rule_off['energy']:.3f}  "
          f"电费 {rule_off['cost']:.3f}  "
          f"总不达标 {rule_off['total_discomfort']:.2%}  "
          f"有人不达标 {rule_off['occ_discomfort']:.2%}")
    print()

    res = {"rule": rule, "rule_off": rule_off, "rl": {}}
    print("=== RL 各档（10 种子）===")
    print(f"  {'es':>5}{'电量':>10}{'±CI':>8}{'电费':>8}{'±CI':>8}"
          f"{'有人不达标':>12}{'±CI':>9}{'无人耗电':>10}{'无人开机率':>12}")
    print("  " + "-" * 82)
    for es in SCALES:
        rows = []
        for seed in SEEDS:
            ck = os.path.join(PROJ, "models", "energy_scale_ci",
                              f"es{int(es)}_s{seed}", "final_model.zip")
            if not os.path.exists(ck):
                continue
            model = PPO.load(ck, device="cpu")

            def fn(env, _m=model):
                v = np.array(env.sim.to_vector(env.get_obs_dict()),
                             dtype=np.float32)
                return int(_m.predict(v, deterministic=True)[0])
            rows.append(measure(fn))
        if not rows:
            continue
        Es = [r["energy"] for r in rows]
        Cs = [r["cost"] for r in rows]
        Ds = [r["occ_discomfort"] for r in rows]
        agg = {"energy": mean(Es), "energy_ci": ci95(Es),
               "cost": mean(Cs), "cost_ci": ci95(Cs),
               "occ_discomfort": mean(Ds), "occ_discomfort_ci": ci95(Ds),
               "occ_min": min(Ds), "occ_max": max(Ds),
               "emp_energy": mean(r["emp_energy"] for r in rows),
               "ac_on_emp": mean(r["ac_on_emp"] for r in rows),
               "per_seed": [{"energy": r["energy"],
                             "occ_discomfort": r["occ_discomfort"]}
                            for r in rows]}
        res["rl"][str(es)] = agg
        print(f"  {es:>5.0f}{agg['energy']:>10.3f}{agg['energy_ci']:>8.3f}"
              f"{agg['cost']:>8.3f}{agg['cost_ci']:>8.3f}"
              f"{agg['occ_discomfort']:>12.2%}"
              f"{agg['occ_discomfort_ci']:>9.2%}"
              f"{agg['emp_energy']:>10.3f}"
              f"{agg['ac_on_emp']:>12.1%}")
    print()

    # 等舒适度（规则插值）
    FRONT = [(23.0, True), (24.0, True), (25.0, True), (25.0, False),
             (26.0, True), (26.0, False), (27.0, True), (27.0, False),
             (28.0, True), (28.0, False), (29.0, True), (29.0, False)]
    front = []
    for sp, keep in FRONT:
        r = measure(make_rule(sp, keep))
        r.update({"sp": sp, "keep": keep})
        front.append(r)
    front.sort(key=lambda x: x["occ_discomfort"])
    pf, best = [], 1e9
    for p in front:
        if p["cost"] < best - 1e-12:
            pf.append(p)
            best = p["cost"]

    print("=== 规则的（有人不达标, 电费）帕累托前沿 ===")
    print(f"  {'设定':>6}{'无人':>7}{'电量':>9}{'电费':>8}{'有人不达标':>12}")
    print("  " + "-" * 44)
    for p in pf:
        print(f"  {p['sp']:>6.0f}{'不停机' if p['keep'] else '关机':>7}"
              f"{p['energy']:>9.3f}{p['cost']:>8.3f}"
              f"{p['occ_discomfort']:>12.2%}")
    print()

    def interp(target):
        lo = max([p for p in pf if p["occ_discomfort"] <= target],
                 key=lambda x: x["occ_discomfort"], default=None)
        hi = min([p for p in pf if p["occ_discomfort"] >= target],
                 key=lambda x: x["occ_discomfort"], default=None)
        if lo and hi and hi["occ_discomfort"] > lo["occ_discomfort"]:
            w = ((target - lo["occ_discomfort"])
                 / (hi["occ_discomfort"] - lo["occ_discomfort"]))
            return lo["cost"] + w * (hi["cost"] - lo["cost"])
        return lo["cost"] if lo else (hi["cost"] if hi else None)

    print("=" * 96)
    print(" 等舒适度对比（RL 10 种子均值 vs 规则前沿插值）")
    print("=" * 96)
    print(f"  {'es':>5}{'RL有人不达标':>14}{'RL电费':>9}{'规则同水平':>11}"
          f"{'差值':>10}{'谁省':>8}")
    print("  " + "-" * 60)
    cmp_rows = []
    for es in SCALES:
        a = res["rl"].get(str(es))
        if not a:
            continue
        rc = interp(a["occ_discomfort"])
        if rc is None:
            continue
        d = (a["cost"] - rc) / rc
        cmp_rows.append({"scale": es, "rl_d": a["occ_discomfort"],
                         "rl_cost": a["cost"], "rule_cost": rc, "diff": d})
        print(f"  {es:>5.0f}{a['occ_discomfort']:>14.2%}{a['cost']:>9.3f}"
              f"{rc:>11.3f}{d:>+10.1%}{'规则' if d > 0 else 'RL':>8}")
    print()

    res["rule_front"] = pf
    res["compare"] = cmp_rows
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    md = ["# 多种子等舒适度对比", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          "- RL 使用 `energy_scale_ci` 的 **40 个模型**（4 档 × 10 种子）",
          f"- 每模型评估 {EVAL_EPISODES} 回合", "",
          "## 一、为什么必须用多种子", "",
          "修复奖励函数的多种子验证显示：同一配置（es=30、修复后）的"
          "\"有人时段不达标率\"在 3 个种子间为",
          "**0.00% / 9.36% / 34.59%**。",
          "因此此前只用一个种子（seed=42）的分析不可靠，",
          "结论须以 10 种子均值为准。", "",
          "## 二、规则基线", "",
          "| 规则 | 电量(kWh/天) | 电费(元/天) | 总不达标 | 有人不达标 |",
          "|---|---|---|---|---|",
          f"| 全开 25 °C | {rule['energy']:.3f} | {rule['cost']:.3f} | "
          f"{rule['total_discomfort']:.2%} | {rule['occ_discomfort']:.2%} |",
          f"| 25 °C / 无人关机 | {rule_off['energy']:.3f} | "
          f"{rule_off['cost']:.3f} | {rule_off['total_discomfort']:.2%} | "
          f"{rule_off['occ_discomfort']:.2%} |", "",
          "## 三、RL 各档（10 种子均值的 95% 置信区间）", "",
          "| es | 电量 | 电费 | 有人不达标 | 范围 | 无人段耗电 | 无人时开机率 |",
          "|---|---|---|---|---|---|---|"]
    for es in SCALES:
        a = res["rl"].get(str(es))
        if not a:
            continue
        md.append(f"| {es:.0f} | {a['energy']:.3f} ± {a['energy_ci']:.3f} | "
                  f"{a['cost']:.3f} ± {a['cost_ci']:.3f} | "
                  f"{a['occ_discomfort']:.2%} ± {a['occ_discomfort_ci']:.2%} | "
                  f"{a['occ_min']:.2%}~{a['occ_max']:.2%} | "
                  f"{a['emp_energy']:.3f} | {a['ac_on_emp']:.1%} |")
    md += ["", "## 四、规则的帕累托前沿（有人不达标口径）", "",
           "| 设定 | 无人时 | 电量 | 电费 | 有人不达标 |",
           "|---|---|---|---|---|"]
    for p in pf:
        md.append(f"| {p['sp']:.0f} °C | "
                  f"{'不停机' if p['keep'] else '关机'} | "
                  f"{p['energy']:.3f} | {p['cost']:.3f} | "
                  f"{p['occ_discomfort']:.2%} |")
    md += ["", "## 五、等舒适度对比", "",
           "| es | RL 有人不达标 | RL 电费 | 规则同水平电费 | 差值 | 谁更省 |",
           "|---|---|---|---|---|---|"]
    for c in cmp_rows:
        md.append(f"| {c['scale']:.0f} | {c['rl_d']:.2%} | {c['rl_cost']:.3f} | "
                  f"{c['rule_cost']:.3f} | {c['diff']:+.1%} | "
                  f"{'规则' if c['diff'] > 0 else 'RL'} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
