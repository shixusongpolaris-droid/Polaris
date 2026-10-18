# -*- coding: utf-8 -*-
"""gen7：规则与强化学习在**同一批评估回合**上的等舒适度对比。

为什么必须重算（方法学问题）
----------------------------
在 gen7 的既有结果里，两侧用的评估回合不同：

    规则评估   EVAL_SEED0 = 9600 / 9900（gen7_rule_ceiling / price_aware）
    RL 评估    EVAL_SEED0 = 9100（energy_scale_ci）

不同随机回合意味着不同的占用序列、不同的天气，两个数字之间不可比。
gen6 也犯过同一个问题（multiseed_equal_comfort 用自选的种子）。
本脚本把两侧拉到同一批回合上重算，这是唯一干净的做法。

做法
----
1. 从两个规则结果 JSON 里取出各自的帕累托前沿配置
2. 与 40 个 RL 模型一起，在**同一批种子**（9100~9107，8 回合）上评估
3. 在"有人时段不达标率"口径上做等舒适度插值
4. 双向判据：
     · RL 支配规则（电费与不舒适都不高于它）
     · 规则在等舒适度下更便宜

输出
----
docs/gen7等舒适度对比.md
models/gen7_equal_comfort/results.json
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
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import numpy as np                                     # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402

from src.env import (RewardConfig, SimConfig, SmartHomeEnv,  # noqa: E402
                     ActionConfig, encode_action)

AC_OFF = 0
_AC = ActionConfig()
LOOKAHEAD = 0.6
SCALES = [10.0, 30.0, 60.0, 100.0]
SEEDS = [42, 66, 2026, 7, 123, 11, 22, 33, 44, 55]

# ⚠️ 统一到同一批评估回合（与 energy_scale_ci 一致）
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

MODEL_DIR = os.path.join(PROJ, "models", "energy_scale_ci")
RULE_JSONS = [os.path.join(PROJ, "models", "gen7_rule_ceiling", "results.json"),
              os.path.join(PROJ, "models", "gen7_price_rule", "results.json")]
OUT = os.path.join(PROJ, "models", "gen7_equal_comfort", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7等舒适度对比.md")

T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


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


# ---------------- 规则构造（与两个规则脚本一致） ----------------
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


def make_risk_rule(th, precool, maintain):
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


def make_price_rule(th, pv, pf, pp, maintain):
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


def make_always(target):
    return lambda env: _hyst(env, target)


def make_off_empty(target):
    def fn(env):
        od = env.get_obs_dict()
        if not (od["occ_a"] or od["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, target)
    return fn


# ---------------- 统一评估 ----------------
def measure(action_fn, n=EVAL_EPISODES):
    """与规则脚本完全相同的度量口径，保证两侧可比。"""
    rc = rew()
    sc = SimConfig(occupancy_mode="semi_markov")
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, seed=EVAL_SEED0 + ep)
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
    return {"energy": mean(E), "energy_ci": ci95(E),
            "cost": mean(C), "cost_ci": ci95(C),
            "discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1)}


def model_fn(path):
    m = PPO.load(path)

    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(m.predict(v, deterministic=True)[0])
    return fn


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


def interp(front, d):
    pts = sorted(front, key=lambda x: x["occ_discomfort"])
    lo = [p for p in pts if p["occ_discomfort"] <= d]
    hi = [p for p in pts if p["occ_discomfort"] >= d]
    if not lo or not hi:
        return None
    l = max(lo, key=lambda x: x["occ_discomfort"])
    h = min(hi, key=lambda x: x["occ_discomfort"])
    if abs(h["occ_discomfort"] - l["occ_discomfort"]) < 1e-12:
        return l["cost"]
    w = ((d - l["occ_discomfort"])
         / (h["occ_discomfort"] - l["occ_discomfort"]))
    return l["cost"] + w * (h["cost"] - l["cost"])


def main():
    print("=" * 88)
    print(" gen7 等舒适度对比（规则 vs RL，同一批评估回合）")
    print("=" * 88)
    print(f"  评估回合：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}，"
          f"{EVAL_EPISODES} 回合 × 7 天")
    print()

    # 1) 规则：重算既有前沿的配置 + 基线
    rule_specs = []
    for jp in RULE_JSONS:
        if not os.path.exists(jp):
            continue
        with open(jp, encoding="utf-8") as f:
            rows = json.load(f).get("results", [])
        fr = pareto(rows)
        for r in fr:
            if r.get("pre_valley") is not None:
                rule_specs.append((r["name"],
                                   make_price_rule(r["threshold"],
                                                   r["pre_valley"],
                                                   r["pre_flat"],
                                                   r["pre_peak"],
                                                   25.0)))
            elif r.get("threshold") is not None:
                rule_specs.append((r["name"],
                                   make_risk_rule(r["threshold"],
                                                  r["precool"],
                                                  r["maintain"])))
    for nm, t in [("全开 25°C", 25.0), ("全开 26°C", 26.0)]:
        rule_specs.append((nm, make_always(t)))
    for nm, t in [("25°C/无人关", 25.0), ("26°C/无人关", 26.0)]:
        rule_specs.append((nm, make_off_empty(t)))

    # 去重
    seen = set()
    uniq = []
    for nm, fn in rule_specs:
        if nm in seen:
            continue
        seen.add(nm)
        uniq.append((nm, fn))
    rule_specs = uniq
    print(f"  待评估规则 {len(rule_specs)} 条")

    rule_res = []
    for nm, fn in rule_specs:
        r = measure(fn)
        r["name"] = nm
        r["kind"] = "rule"
        rule_res.append(r)
    print(f"  规则评估完成")

    # 2) RL：40 个模型
    rl_res = []
    for sc in SCALES:
        for sd in SEEDS:
            p = os.path.join(MODEL_DIR, f"es{int(sc)}_s{sd}", "final_model.zip")
            if not os.path.exists(p):
                continue
            r = measure(model_fn(p))
            r["name"] = f"RL es={int(sc)} seed={sd}"
            r["scale"] = sc
            r["seed"] = sd
            r["kind"] = "rl"
            rl_res.append(r)
    print(f"  RL 评估完成（{len(rl_res)} 个模型）")

    # 3) 聚合
    agg = []
    for sc in SCALES:
        sub = [r for r in rl_res if r["scale"] == sc]
        if not sub:
            continue
        agg.append({
            "scale": sc,
            "cost": mean([r["cost"] for r in sub]),
            "cost_ci": ci95([r["cost"] for r in sub]),
            "energy": mean([r["energy"] for r in sub]),
            "energy_ci": ci95([r["energy"] for r in sub]),
            "occ_discomfort": mean([r["occ_discomfort"] for r in sub]),
            "occ_ci": ci95([r["occ_discomfort"] for r in sub]),
        })

    rf = pareto(rule_res)
    print()
    print("=" * 88)
    print(f" 规则帕累托前沿（{len(rf)} 点）")
    print("=" * 88)
    print(f"  {'规则':<34}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
    print("  " + "-" * 64)
    for r in rf:
        print(f"  {r['name']:<34}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    print()
    print("=" * 88)
    print(" RL 各档")
    print("=" * 88)
    print(f"  {'es':>5}{'电量':>16}{'电费':>16}{'有人不达标':>14}")
    print("  " + "-" * 52)
    for a in agg:
        print(f"  {a['scale']:>5.0f}"
              f"{a['energy']:>10.2f}±{a['energy_ci']:<4.2f}"
              f"{a['cost']:>10.2f}±{a['cost_ci']:<4.2f}"
              f"{a['occ_discomfort']:>10.1%}±{a['occ_ci']:<4.1%}")

    # 4) 等舒适度对比
    print()
    print("=" * 88)
    print(" 等舒适度对比（有人时段不达标率，同一批回合）")
    print("=" * 88)
    print(f"  {'es':>5}{'RL 不达标':>12}{'RL 电费':>10}"
          f"{'规则同水平':>12}{'差值':>10}{'谁省':>8}")
    print("  " + "-" * 60)
    cmp_rows = []
    for a in agg:
        rc = interp(rf, a["occ_discomfort"])
        if rc is None:
            print(f"  {a['scale']:>5.0f}{a['occ_discomfort']:>12.1%}"
                  f"{a['cost']:>10.3f}{'超出规则范围':>22}")
            continue
        diff = (a["cost"] - rc) / rc
        who = "规则" if diff > 0 else "RL"
        cmp_rows.append({"scale": a["scale"],
                         "rl_d": a["occ_discomfort"], "rl_cost": a["cost"],
                         "rule_cost": rc, "diff": diff})
        print(f"  {a['scale']:>5.0f}{a['occ_discomfort']:>12.1%}"
              f"{a['cost']:>10.3f}{rc:>12.3f}{diff:>+10.1%}{who:>8}")

    # 5) 支配关系
    print()
    print("=" * 88)
    print(" 支配关系")
    print("=" * 88)
    for a in agg:
        beat = [r for r in rule_res
                if a["cost"] <= r["cost"] + 1e-9
                and a["occ_discomfort"] <= r["occ_discomfort"] + 1e-9]
        print(f"  RL es={a['scale']:>3.0f} 支配 {len(beat)} 个规则点")
    rl_agg_dom = [r for r in rule_res
                  if any(a["cost"] <= r["cost"] + 1e-9
                         and a["occ_discomfort"] <= r["occ_discomfort"] + 1e-9
                         for a in agg)]
    print(f"  规则点中，被任一档 RL 支配的：{len(rl_agg_dom)} / {len(rule_res)}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"rule_front": rf, "rule_all": rule_res,
                   "rl_agg": agg, "rl_all": rl_res,
                   "equal_comfort": cmp_rows}, f, ensure_ascii=False,
                  indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7 等舒适度对比（规则 vs RL，同一批评估回合）", "",
          f"- 生成：2026-10-18",
          f"- 评估：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}，"
          f"{EVAL_EPISODES} 回合 x 7 天",
          "- 两侧使用**同一批**评估回合（此前规则与 RL 用了不同种子，不可比）",
          "", "## 一、规则帕累托前沿", "",
          "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in rf:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 二、RL 各档（10 种子均值 ± 95% CI）", "",
           "| es | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for a in agg:
        md.append(f"| {a['scale']:.0f} | {a['energy']:.2f}±{a['energy_ci']:.2f} "
                  f"| {a['cost']:.2f}±{a['cost_ci']:.2f} "
                  f"| {a['occ_discomfort']:.1%}±{a['occ_ci']:.1%} |")
    md += ["", "## 三、等舒适度对比", "",
           "| es | RL 不达标 | RL 电费 | 规则同水平 | 差值 | 谁省 |",
           "|---|---|---|---|---|---|"]
    for c in cmp_rows:
        who = "规则" if c["diff"] > 0 else "RL"
        md.append(f"| {c['scale']:.0f} | {c['rl_d']:.1%} | {c['rl_cost']:.3f} "
                  f"| {c['rule_cost']:.3f} | {c['diff']:+.1%} | {who} |")
    md += ["", "## 四、结论", ""]
    if cmp_rows:
        best_rl = min(cmp_rows, key=lambda x: x["diff"])
        if best_rl["diff"] < 0:
            md.append(f"- RL 在 es={best_rl['scale']:.0f} "
                      f"（{best_rl['rl_d']:.1%} 不达标）处比规则省 "
                      f"**{abs(best_rl['diff']):.1%}**")
        win_rule = [c for c in cmp_rows if c["diff"] > 0]
        if win_rule:
            md.append(f"- 规则在 {len(win_rule)}/{len(cmp_rows)} 档更省")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
