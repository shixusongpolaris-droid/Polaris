# -*- coding: utf-8 -*-
"""gen7：统一协议下的规则族重建 + 与 RL 的等舒适度对比（Welch t）。

为什么必须完全重做
------------------
团队统计盲评（stats-reviewer）指出四个严重问题：

1. **混用评估回合**：gen7_rule_ceiling 用 12 回合/种子 9600、
   gen7_price_rule 用 10/9900、gen7_rule_fine 与 gen7_rule_emptysteps
   用 8/9100。把不同回合算出的数字合并成一条前沿在统计上没有意义。
   我引用最多的两个"最优规则点"（2.183/1.33%、2.056/2.64%）
   全部重评到 9100 后分别退化为 2.282/0.98% 与 2.181/2.23%。

2. **规则族漏了两个自然维度**：小时时段块、滞环宽度（我一直用 0.5）。
   盲评员用「睡26/醒25 + 21~23 或 7~9 设26 + 滞环0.8 + 无人p>=0.02预冷」
   构造出 1.819 元 / 6.49% 的规则，跨 9200/9600/9900 复核为
   1.71~1.87 / 5.75~6.55% —— 满足我给的"3~8% 区间 <1.9 元"挑战。

3. **71 点前沿实际只有 12 个不同工作点**（并列放大）。

4. **t 值用的是单样本**，忽略规则侧方差；应改用 Welch t。

本脚本
------
1. 在**统一协议**（种子 9100~9107，8 回合）下评估一个扩充后的规则族，
   含小时时段块与可变滞环宽度：
       baseline   全开 / 无人关 / 分时三档
       A 概率阈值  p_arrive_soon >= th 触发预冷
       B 空步阈值  empty_steps   >= T  触发预冷
       C 时段块    额外在某些小时把维持温度提高
       D 电价分档  谷/平/峰分别设预冷温度
2. 重建帕累托前沿，并**按 (cost, occ) 去重**报告不同工作点数
3. 与 VecNorm 的 40 个 RL 模型做等舒适度对比，**用 Welch t**
4. 同时给出占优判据与插值两种口径

输出
----
docs/gen7统一协议最终对比.md
models/gen7_unified/results.json
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
                     encode_action)

AC_OFF = 0
# 统一协议
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

OUT = os.path.join(PROJ, "models", "gen7_unified", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7统一协议最终对比.md")

VECNORM = os.path.join(PROJ, "models", "gen7_vecnorm_confirm", "results.json")

T95_8 = 2.365   # df=7


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


def hyst_action(env, target, h):
    """滞环 h（此前固定 0.5，盲评指出这是被漏掉的维度）。"""
    od = env.get_obs_dict()
    T = float(od["temp_in"])
    cfg = env.act_cfg
    if target is None:
        return encode_action(AC_OFF, 0, 0, cfg)
    if T > target + h:
        ac = ac_idx_for(target, cfg)
    elif T < target - h:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if od["light_in"] < 300 else 0
    return encode_action(ac, light, 0, cfg)


def build_rule(spec):
    """按 spec 生成规则函数。"""
    trig = spec.get("trig")          # 'p' | 'empty' | None
    th = spec.get("th", 0.02)
    precool = spec.get("precool", 25.0)
    pre_v = spec.get("pre_valley")   # 电价分档时的谷段预冷
    pre_f = spec.get("pre_flat")
    pre_p = spec.get("pre_peak")
    mt_s = spec.get("mt_sleep", 25.0)
    mt_w = spec.get("mt_wake", 25.0)
    blocks = spec.get("blocks", [])   # [(h0, h1, temp)]
    h = spec.get("hyst", 0.5)
    always_on = spec.get("always_on")
    off_empty = spec.get("off_empty")
    tou = spec.get("tou")

    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        hour = int(od["hour"])
        T = float(od["temp_in"])
        rc = env.rew_cfg

        # ⚠️ 顺序很重要：先判「无人关」，再判「恒定温度」。
        # 2026-10-18 自审发现：原先 always_on 在前，会让 "25°C/无人关"
        # 退化成 "全开 25°C"（off_empty 被短路），基线全部失真。
        if (not occ) and (off_empty is not None):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)

        if always_on is not None:
            return hyst_action(env, always_on, h)

        # 维持温度（有人时生效）：睡/醒 + 时段块 + 分时三档
        if occ:
            if tou:
                price = rc.get_price(hour)
                if price <= rc.price_valley + 1e-9:
                    mt = tou[0]
                elif price >= rc.price_peak - 1e-9:
                    mt = tou[2]
                else:
                    mt = tou[1]
            else:
                mt = mt_s if (hour >= 23 or hour < 7) else mt_w
            for (a, b, t) in blocks:
                if a <= hour < b:
                    mt = t
            return hyst_action(env, mt, h)

        # 无人：按触发条件决定是否预冷
        fire = False
        if trig == "p":
            fire = float(od["p_arrive_soon"]) >= th
        elif trig == "empty":
            fire = float(od["empty_steps"]) >= th
        if fire:
            if pre_v is not None:
                price = rc.get_price(hour)
                if price <= rc.price_valley + 1e-9:
                    return hyst_action(env, pre_v, h)
                if price >= rc.price_peak - 1e-9:
                    return hyst_action(env, pre_p, h)
                return hyst_action(env, pre_f, h)
            return hyst_action(env, precool, h)
        if T <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return hyst_action(env, pre_f if pre_f is not None else precool, h)
    return fn


def measure(fn, n=EVAL_EPISODES):
    rc = rew()
    sc = SimConfig(occupancy_mode="semi_markov")
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    per_ep = []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        e_tot = 0.0
        while True:
            a = fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            e = float(info["components"]["energy_kwh"])
            e_tot += e
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
    return {"energy": mean(E), "energy_all": E, "cost": mean(C),
            "cost_all": C,
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


def dedupe(front):
    uniq = []
    for r in front:
        hit = None
        for u in uniq:
            if (abs(u["cost"] - r["cost"]) < 1e-9
                    and abs(u["occ_discomfort"] - r["occ_discomfort"]) < 1e-9):
                hit = u
                break
        if hit:
            hit["n_tied"] += 1
        else:
            u = dict(r)
            u["n_tied"] = 1
            uniq.append(u)
    return uniq


def welch_t(a, b_mean, b_all):
    """Welch t：两侧都是样本。规则侧用逐回合电费作样本。"""
    if len(a) < 2 or len(b_all) < 2:
        return None
    va = stdev(a) ** 2 / len(a)
    vb = stdev(b_all) ** 2 / len(b_all)
    se = math.sqrt(va + vb)
    if se <= 1e-12:
        return None
    return (mean(a) - b_mean) / se


def build_specs():
    specs = []
    # baseline
    specs.append({"name": "全开 25°C", "always_on": 25.0, "hyst": 0.5})
    specs.append({"name": "全开 26°C", "always_on": 26.0, "hyst": 0.5})
    for t in (25.0, 26.0):
        specs.append({"name": f"{t:.0f}°C/无人关", "off_empty": True,
                      "always_on": t, "hyst": 0.5})
    for mts in ((25.0, 26.0, 28.0),):
        specs.append({"name": "分时三档 谷25/平26/峰28", "off_empty": True,
                      "tou": mts, "hyst": 0.5})

    MT = [(25.0, 25.0), (26.0, 25.0), (25.0, 26.0), (26.0, 26.0)]
    HY = [0.5, 0.8, 1.0]

    # A 概率阈值
    for th in (0.02, 0.10, 0.50, 0.90):
        for pc in (23.0, 24.0, 25.0, 26.0):
            for (a, b) in MT:
                for h in HY:
                    specs.append({"name": f"A p>={th:.2f} 预冷{pc:.0f} 睡{a:.0f}/醒{b:.0f} h{h}",
                                  "trig": "p", "th": th, "precool": pc,
                                  "mt_sleep": a, "mt_wake": b, "hyst": h})
    # B 空步阈值
    for T in (1, 2, 3, 5, 8, 15, 30, 44, 46, 47):
        for pc in (23.0, 24.0, 25.0, 26.0):
            for (a, b) in [(25.0, 25.0), (26.0, 25.0), (26.0, 26.0)]:
                for h in HY:
                    specs.append({"name": f"B 空步>={T} 预冷{pc:.0f} 睡{a:.0f}/醒{b:.0f} h{h}",
                                  "trig": "empty", "th": T, "precool": pc,
                                  "mt_sleep": a, "mt_wake": b, "hyst": h})
    # C 时段块（盲评指出的遗漏维度）
    #
    # 2026-10-18 补强：团队统计盲评（t7）指出原来只有 2 小时块
    # （21-23、7-9），缺 1 小时块。补 22-23 后得到
    #   22-23 pc25 → 1.9156 元 / 0.98%   （支配原 1.9354/0.98%）
    #   22-23 pc26 → 1.8628 元 / 2.70%   （支配原 1.8961/3.11%）
    # 因此并入 1 小时块与更多时段，避免再次因网格太粗而低估规则族。
    BLOCKS = [[], [(21, 23, 26.0)], [(7, 9, 26.0)],
              [(21, 23, 26.0), (7, 9, 26.0)],
              [(22, 23, 26.0)], [(21, 22, 26.0)],
              [(7, 8, 26.0)], [(8, 9, 26.0)],
              [(18, 19, 26.0)], [(14, 15, 26.0)]]
    for bl in BLOCKS:
        for a in (25.0, 26.0):
            for pc in (25.0, 26.0):
                for h in HY:
                    for trig, th in (("p", 0.02), ("empty", 2)):
                        # 名字必须能区分不同的时段块组合 —— 只用 len(bl)
                        # 会让 [(21,23,26)] 与 [(7,9,26)] 撞名（自审发现）
                        bl_tag = ("无" if not bl
                                  else "+".join(f"{a0}-{b0}" for a0, b0, _ in bl))
                        specs.append({
                            "name": f"C 块[{bl_tag}] 睡{a:.0f} 预冷{pc:.0f} h{h} {trig}{th}",
                            "trig": trig, "th": th, "precool": pc,
                            "mt_sleep": a, "mt_wake": 25.0, "blocks": bl,
                            "hyst": h})
    # D 电价分档预冷
    for pv in (24.0, 25.0, 26.0):
        for pf in (25.0, 26.0):
            for pp in (25.0, 26.0, None):
                for (a, b) in [(25.0, 25.0), (26.0, 25.0)]:
                    for h in (0.5, 0.8):
                        for th in (0.02, 0.10):
                            specs.append({
                                "name": f"D 谷{pv:.0f}/平{pf:.0f}/峰{pp if pp else '关'} 睡{a:.0f}/醒{b:.0f} h{h} p{th}",
                                "trig": "p", "th": th,
                                "pre_valley": pv, "pre_flat": pf,
                                "pre_peak": pp,
                                "mt_sleep": a, "mt_wake": b, "hyst": h})
    return specs


def main():
    specs = build_specs()
    print("=" * 92)
    print(" gen7：统一协议下的规则族重建（种子 9100~9107，8 回合）")
    print("=" * 92)
    print(f"  规则数 {len(specs)}")
    print()

    rows = []
    for i, sp in enumerate(specs, 1):
        r = measure(build_rule(sp))
        r["name"] = sp["name"]
        r["spec"] = {k: v for k, v in sp.items() if k != "name"}
        rows.append(r)
        if i % 100 == 0:
            print(f"    已评估 {i}/{len(specs)}")

    front = pareto(rows)
    uniq = dedupe(front)
    print()
    print("=" * 92)
    print(f" 前沿：{len(front)} 个条目 -> 去重后 {len(uniq)} 个不同工作点")
    print("=" * 92)
    print(f"  {'#':>3}{'有人不达标':>11}{'电费':>9}{'电量':>9}  代表规则")
    print("  " + "-" * 78)
    for i, u in enumerate(uniq, 1):
        print(f"  {i:>3}{u['occ_discomfort']:>11.2%}{u['cost']:>9.3f}"
              f"{u['energy']:>9.3f}  {u['name'][:44]}")

    ds = sorted(u["occ_discomfort"] for u in uniq)
    gaps = [(ds[j], ds[j + 1], ds[j + 1] - ds[j]) for j in range(len(ds) - 1)]
    g = max(gaps, key=lambda x: x[2]) if gaps else (0, 0, 0)
    print()
    print(f"  最大空档：{g[0]:.2%} ~ {g[1]:.2%}（宽 {g[2]:.2%}）")

    # 与 RL 对比
    rl = []
    if os.path.exists(VECNORM):
        with open(VECNORM, encoding="utf-8") as f:
            rl = json.load(f).get("agg", [])

    cmp_rows = []
    if rl:
        print()
        print("=" * 92)
        print(" 等舒适度对比（占优判据 + 插值口径，Welch t）")
        print("=" * 92)
        print(f"  {'es':>4}{'RL不达标':>10}{'RL电费':>9}"
              f"{'占优参照':>9}{'差值':>8}{'Welch':>8}"
              f"{'插值参照':>9}{'差值':>8}{'Welch':>8}")
        print("  " + "-" * 78)
        for a in rl:
            d = a["occ_discomfort"]
            c = a["cost"]
            costs = [s["cost"] for s in a.get("per_seed", [])]
            # 占优参照
            cands = [u for u in uniq if u["occ_discomfort"] <= d + 1e-12]
            ref_dom = min(cands, key=lambda u: u["cost"]) if cands else None
            # 插值参照
            lo = [u for u in uniq if u["occ_discomfort"] <= d]
            hi = [u for u in uniq if u["occ_discomfort"] >= d]
            ref_int = None
            if lo and hi:
                l = max(lo, key=lambda u: u["occ_discomfort"])
                hh = min(hi, key=lambda u: u["occ_discomfort"])
                if abs(hh["occ_discomfort"] - l["occ_discomfort"]) < 1e-12:
                    ref_int = l
                else:
                    w = ((d - l["occ_discomfort"])
                         / (hh["occ_discomfort"] - l["occ_discomfort"]))
                    ref_int = {"cost": l["cost"] + w * (hh["cost"] - l["cost"]),
                               "cost_all": None,
                               "occ_discomfort": d, "name": "interp"}
            def fmt(ref):
                if ref is None:
                    return f"{'无':>9}{'':>8}{'':>8}", None, None
                diff = (c - ref["cost"]) / ref["cost"]
                if ref.get("cost_all"):
                    t = welch_t(costs, ref["cost"], ref["cost_all"])
                else:
                    # 插值点无逐回合样本，用邻近点方差的较大者近似
                    t = welch_t(costs, ref["cost"], l.get("cost_all") or [])
                t_s = f"{t:+.2f}" if t is not None else "-"
                return (f"{ref['cost']:>9.3f}{diff:>+8.1%}{t_s:>8}",
                        diff, t)
            s1, d1, t1 = fmt(ref_dom)
            s2, d2, t2 = fmt(ref_int)
            print(f"  {a['scale']:>4.0f}{d:>10.2%}{c:>9.3f}{s1}{s2}")
            cmp_rows.append({"scale": a["scale"], "rl_d": d, "rl_cost": c,
                             "dom_ref": ref_dom["name"] if ref_dom else None,
                             "dom_ref_cost": ref_dom["cost"] if ref_dom else None,
                             "dom_diff": d1, "dom_t": t1,
                             "int_ref_cost": ref_int["cost"] if ref_int else None,
                             "int_diff": d2, "int_t": t2})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"episodes": EVAL_EPISODES, "seed0": EVAL_SEED0,
                   "n_rules": len(rows), "front": front,
                   "distinct_points": uniq,
                   "max_gap": {"lo": g[0], "hi": g[1], "width": g[2]},
                   "equal_comfort": cmp_rows, "rl_agg": rl},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：统一协议下的最终对比", "",
          f"- 生成：2026-10-18",
          f"- **统一协议**：种子 {EVAL_SEED0}~{EVAL_SEED0+EVAL_EPISODES-1}，"
          f"{EVAL_EPISODES} 回合；全部 {len(rows)} 条规则在同一批回合上重评",
          f"- 前沿 {len(front)} 个条目 -> **{len(uniq)} 个不同工作点**",
          f"- 最大空档 {g[0]:.2%} ~ {g[1]:.2%}（宽 {g[2]:.2%}）", "",
          "## 一、为什么重做", "",
          "团队统计盲评（stats-reviewer）指出我此前四个错误：", "",
          "1. 混用评估回合（9600 / 9900 / 9100）后合并前沿",
          "2. 规则族漏了「小时时段块」与「滞环宽度」两个维度",
          "3. 「71 点前沿」实际只有 12 个不同工作点",
          "4. t 值用单样本而非 Welch", "",
          "本前的规则结构已由盲评员构造验证："
          "「睡26/醒25 + 21~23 或 7~9 设26 + 滞环0.8 + 无人p>=0.02预冷」"
          "得到 1.819 元 / 6.49%，跨多批回合复核 1.71~1.87 / 5.75~6.55%。", "",
          "## 二、不同工作点", "",
          "| # | 有人不达标 | 电费 | 电量 | 代表规则 |",
          "|---|---|---|---|---|"]
    for i, u in enumerate(uniq, 1):
        md.append(f"| {i} | {u['occ_discomfort']:.2%} | {u['cost']:.3f} "
                  f"| {u['energy']:.3f} | {u['name']} |")
    if cmp_rows:
        md += ["", "## 三、与 RL 的对比（Welch t）", "",
               "| es | RL 不达标 | RL 电费 | 占优参照 | 差值 | 占优 t "
               "| 插值参照 | 差值 | 插值 t |",
               "|---|---|---|---|---|---|---|---|---|"]
        for c in cmp_rows:
            dom_ref = (f"{c['dom_ref_cost']:.3f}"
                       if c["dom_ref_cost"] is not None else "-")
            dom_diff = (f"{c['dom_diff']:+.1%}"
                        if c["dom_diff"] is not None else "-")
            dom_t = (f"{c['dom_t']:+.2f}"
                     if c["dom_t"] is not None else "-")
            int_ref = (f"{c['int_ref_cost']:.3f}"
                       if c["int_ref_cost"] is not None else "-")
            int_diff = (f"{c['int_diff']:+.1%}"
                        if c["int_diff"] is not None else "-")
            int_t = (f"{c['int_t']:+.2f}"
                     if c["int_t"] is not None else "-")
            md.append(f"| {c['scale']:.0f} | {c['rl_d']:.2%} "
                      f"| {c['rl_cost']:.3f} | {dom_ref} | {dom_diff} "
                      f"| {dom_t} | {int_ref} | {int_diff} | {int_t} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
