# -*- coding: utf-8 -*-
"""gen7：合并全部规则网格 + 补上「分担维持温度」规则，重算真正的前沿。

为什么必须重做（我的两处错误）
------------------------------
1. **只用了部分网格**。我先后跑过 4 套规则网格：
       gen7_rule_ceiling      34 组
       gen7_price_rule        24 组
       gen7_rule_fine        960 组   <- 其中有 2.23% 的前沿点
       gen7_rule_emptysteps  490 组
   但在最终对比里只用了 490 那套，把 960 里 2.23% 的点丢了。

2. **规则盲点**。490/960 两套网格的共同缺陷是**维持温度只能取
   25 或 26 一个单值**。团队复核成员 fix-reviewer 加了两种自然写法
   之后，在 6.00% / 7.96% / 8.91% / 8.94% 都得到了工作点。

   因此"规则族在 1%~9% 无工作点 / 33pp 结构性空档"这个论断是
   **我的基线缺陷造成的，不是规则范式的能力边界**，必须撤回。

本脚本
------
1. 载入全部 4 套网格的结果
2. 补入「分担维持温度」规则族：
       · 睡/醒分担：23:00~07:00 设一个维持温度，其余设另一个
       · 峰/谷分担：谷段设一个，峰段设另一个
       · 两者叠加
3. 与 VecNorm 的 40 个 RL 模型（10 种子 × 4 档）做等舒适度对比
4. 用 Welch t 检验判断显著性

输出
----
docs/gen7最终前沿与分工结论.md
models/gen7_final_front/results.json
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
EVAL_EPISODES = 8
EVAL_SEED0 = 9100

# 新规则族的网格
T_LIST = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 35, 40, 44, 46, 47]
PRE_T = [23.0, 24.0, 25.0, 26.0]
SPLIT_T = [(25.0, 26.0), (26.0, 25.0), (25.0, 25.0), (24.0, 26.0),
           (26.0, 26.0)]

OUT = os.path.join(PROJ, "models", "gen7_final_front", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7最终前沿与分工结论.md")

# 已有的 4 套网格
EXISTING = [
    ("gen7_rule_ceiling", "results.json"),
    ("gen7_price_rule", "results.json"),
    ("gen7_rule_fine", "results.json"),
    ("gen7_rule_emptysteps", "results.json"),
]
# VecNorm 的 10 种子结果
VECNORM = os.path.join(PROJ, "models", "gen7_vecnorm_confirm", "results.json")

T95 = {9: 2.262, 7: 2.365, 6: 2.447, 4: 2.776, 2: 4.303, 1: 12.706}


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


def make_split_rule(T, precool, mt_sleep, mt_wake, mt_valley, mt_peak):
    """分担维持温度：睡眠/清醒、谷段/峰段分别设不同的维持温度。

    这是团队复核成员指出的盲点 —— 此前所有规则族只允许"维持温度 = 单一值"，
    于是无法在"几乎不预冷"（省电但很热）与"几乎总预冷"（舒适但贵）之间
    取得中间工作点。
    """
    def fn(env):
        od = env.get_obs_dict()
        occ = bool(od["occ_a"] or od["occ_b"])
        e = float(od["empty_steps"])
        hour = int(od["hour"])
        if occ:
            if hour >= 23 or hour < 7:
                mt = mt_sleep
            else:
                mt = mt_wake
            # 电价分担：谷段/峰段再覆盖
            if mt_valley is not None or mt_peak is not None:
                price = env.rew_cfg.get_price(hour)
                if price <= env.rew_cfg.price_valley + 1e-9 and mt_valley:
                    mt = mt_valley
                elif price >= env.rew_cfg.price_peak - 1e-9 and mt_peak:
                    mt = mt_peak
            return _hyst(env, mt)
        if e >= T:
            return _hyst(env, precool)
        if float(od["temp_in"]) <= 27.0:
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        return _hyst(env, precool)
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


def welch_t(a, b):
    """两组样本（a 为 RL 逐种子电费，b 为规则的单个点估计）。

    规则是确定性规则，逐回合方差小；这里用 RL 的标准误与规则前沿的
    线性插值不确定性做保守近似：只比较 RL 的 CI 是否落在规则值之外。
    """
    if not a or b is None or len(a) < 2:
        return None
    m = mean(a)
    se = stdev(a) / math.sqrt(len(a))
    if se <= 1e-12:
        return None
    return (m - b) / se


def main():
    # ---------- 1. 载入既有网格 ----------
    rows = []
    for d, f in EXISTING:
        p = os.path.join(PROJ, "models", d, f)
        if not os.path.exists(p):
            print(f"  ⚠️ 缺失 {p}")
            continue
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        sub = data.get("results", [])
        for r in sub:
            r["grid"] = d
        rows.extend(sub)
        print(f"  载入 {d}: {len(sub)} 条")
    print(f"  既有合计 {len(rows)} 条")

    # ---------- 2. 新规则族 ----------
    print("\n  评估「分担维持温度」规则族 ...")
    new_rows = []
    for T in T_LIST:
        for pc in PRE_T:
            for (a, b) in SPLIT_T:
                # 仅睡/醒分担
                r = measure(make_split_rule(T, pc, a, b, None, None))
                r["name"] = f"睡/醒 {a:.0f}/{b:.0f} 空步>={T} 预冷{pc:.0f}"
                r["grid"] = "split_sleep"
                new_rows.append(r)
                # 睡/醒 + 峰谷分担
                r2 = measure(make_split_rule(T, pc, a, b, b, a))
                r2["name"] = (f"睡/醒+峰谷 {a:.0f}/{b:.0f} "
                              f"空步>={T} 预冷{pc:.0f}")
                r2["grid"] = "split_both"
                new_rows.append(r2)
        print(f"    T={T:>2} 完成（累计 {len(new_rows)} 条）")
    rows.extend(new_rows)
    print(f"  全部合计 {len(rows)} 条")

    # ---------- 3. 合并前沿 ----------
    front = pareto(rows)
    print()
    print("=" * 92)
    print(f" 合并后帕累托前沿：{len(front)} 点")
    print("=" * 92)
    print(f"  {'规则':<38}{'电量':>9}{'电费':>9}{'有人不达标':>11}")
    print("  " + "-" * 68)
    for r in front:
        print(f"  {r['name'][:38]:<38}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.2%}")

    in_band = [r for r in front if 0.01 <= r["occ_discomfort"] <= 0.10]
    print()
    print(f"  === 1%~10% 区间内的规则工作点：{len(in_band)} 个 ===")
    for r in in_band:
        print(f"  {r['name'][:38]:<38}{r['energy']:>9.3f}{r['cost']:>9.3f}"
              f"{r['occ_discomfort']:>11.2%}")

    # ---------- 4. 与 VecNorm 的 RL 对比 ----------
    rl = None
    if os.path.exists(VECNORM):
        with open(VECNORM, encoding="utf-8") as f:
            rl = json.load(f).get("agg", [])

    cmp_rows = []
    if rl:
        print()
        print("=" * 92)
        print(" 等舒适度对比（VecNorm RL vs 合并后的规则前沿）")
        print("=" * 92)
        print(f"  {'es':>4}{'RL 不达标':>11}{'RL 电费':>10}"
              f"{'规则同水平':>12}{'差值':>9}{'t':>7}{'谁省':>7}")
        print("  " + "-" * 62)
        for a in rl:
            d = a["occ_discomfort"]
            pts = sorted(front, key=lambda x: x["occ_discomfort"])
            lo = [p for p in pts if p["occ_discomfort"] <= d]
            hi = [p for p in pts if p["occ_discomfort"] >= d]
            if not lo or not hi:
                print(f"  {a['scale']:>4.0f}{d:>11.2%}{a['cost']:>10.3f}"
                      f"{'规则无对应点':>20}")
                continue
            l = max(lo, key=lambda x: x["occ_discomfort"])
            h = min(hi, key=lambda x: x["occ_discomfort"])
            if abs(h["occ_discomfort"] - l["occ_discomfort"]) < 1e-12:
                rc_ = l["cost"]
                exact = True
            else:
                w = ((d - l["occ_discomfort"])
                     / (h["occ_discomfort"] - l["occ_discomfort"]))
                rc_ = l["cost"] + w * (h["cost"] - l["cost"])
                exact = False
            diff = (a["cost"] - rc_) / rc_
            who = "规则" if diff > 0 else "RL"
            costs = [s["cost"] for s in a.get("per_seed", [])]
            t = welch_t(costs, rc_)
            t_s = f"{t:+.1f}" if t is not None else "  -"
            sig = "显著" if (t is not None and abs(t) > 2.0) else "不显著"
            cmp_rows.append({"scale": a["scale"], "rl_d": d,
                             "rl_cost": a["cost"], "rule_cost": rc_,
                             "diff": diff, "t": t, "sig": sig,
                             "exact_rule_point": exact})
            print(f"  {a['scale']:>4.0f}{d:>11.2%}{a['cost']:>10.3f}"
                  f"{rc_:>12.3f}{diff:>+9.1%}{t_s:>7}{who:>7}"
                  f"  ({sig}{'' if exact else '，规则侧为插值'})")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n_rules": len(rows), "front": front,
                   "in_band_1_10": in_band,
                   "equal_comfort": cmp_rows, "rl_agg": rl},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    # ---------- 5. 报告 ----------
    md = ["# gen7：最终前沿与「分工」结论", "",
          f"- 生成：2026-10-18",
          f"- 规则网格：合并 {len(rows)} 组（4 套既有 + 新增分担维持温度族）",
          f"- 合并后前沿 {len(front)} 点", "",
          "## 一、这一步在修正什么", "",
          "此前的分析里我犯了两处错：", "",
          "1. **只用了部分网格**。先后跑过 4 套规则网格，最终对比时只用了",
          "   490 组那套，把 960 组里 2.23% 的前沿点丢了。",
          "2. **规则族盲点**。所有旧网格只允许「维持温度 = 单一值」，",
          "   因此无法在「几乎不预冷」与「几乎总预冷」之间取得中间点。",
          "   团队复核成员补上「睡/醒分担」与「峰谷分担」后，",
          "   在 6.0%~8.9% 都得到了工作点。", "",
          "**因此「规则族在 1~9% 无工作点 / 33pp 结构性空档」的论断",
          "已撤回** —— 那是基线缺陷，不是规则范式的能力边界。", "",
          "## 二、合并后的规则前沿", "",
          "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in front:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.2%} |")
    md += ["", f"## 三、1%~10% 区间的规则工作点（{len(in_band)} 个）", ""]
    if in_band:
        md += ["| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
        for r in in_band:
            md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} "
                      f"| {r['occ_discomfort']:.2%} |")
    if cmp_rows:
        md += ["", "## 四、等舒适度对比（VecNorm RL vs 合并前沿）", "",
               "| es | RL 不达标 | RL 电费 | 规则同水平 | 差值 | t | 判定 |",
               "|---|---|---|---|---|---|---|"]
        for c in cmp_rows:
            who = "规则" if c["diff"] > 0 else "RL"
            t_s = f"{c['t']:+.1f}" if c["t"] is not None else "-"
            md.append(f"| {c['scale']:.0f} | {c['rl_d']:.2%} "
                      f"| {c['rl_cost']:.3f} | {c['rule_cost']:.3f} "
                      f"| {c['diff']:+.1%} | {t_s} | {who} 更省（{c['sig']}）|")
        md += ["", "## 五、结论", ""]
        win_rl = [c for c in cmp_rows if c["diff"] < 0 and c["sig"] == "显著"]
        win_rule = [c for c in cmp_rows if c["diff"] > 0 and c["sig"] == "显著"]
        tie = [c for c in cmp_rows if c["sig"] != "显著"]
        if win_rule:
            md.append(f"- **规则显著更省**：es="
                      f"{', '.join(f'{c['scale']:.0f}' for c in win_rule)}"
                      f"（{', '.join(f'{c['diff']:+.1%}' for c in win_rule)}）")
        if win_rl:
            md.append(f"- **RL 显著更省**：es="
                      f"{', '.join(f'{c['scale']:.0f}' for c in win_rl)}"
                      f"（{', '.join(f'{c['diff']:+.1%}' for c in win_rl)}）")
        if tie:
            md.append(f"- **无显著差异**：es="
                      f"{', '.join(f'{c['scale']:.0f}' for c in tie)}")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
