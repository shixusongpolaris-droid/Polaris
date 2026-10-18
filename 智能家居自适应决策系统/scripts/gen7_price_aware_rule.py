# -*- coding: utf-8 -*-
"""gen7：电价感知的风险阈值规则（选项 B）。

为什么要补这一族
----------------
`gen7_rule_ceiling.py` 里的风险阈值规则**完全没用电价**：

    有人在                 ->  维持固定温度
    无人且 p_arrive_soon>=阈值 ->  预冷到固定温度
    其余                   ->  关

它把 3 个电价特征都扔了。而电价恰恰是唯一能让"提前多久、预冷多深"
变成真正二维决策的量：

    谷段 22-06（0.307 元）-> 值得提前预冷，甚至压到 23°C
    峰段 06-22（0.617 元）-> 不值得，宁可等

因此强化学习如果还有胜算，最可能就在这里 —— 它可以学出"按电价深浅
不一的预冷曲线"，而上面那族规则没有这一维。

本脚本把这一维补进规则族，得到**更强的规则基线**，再与既有结果合并
重算帕累托前沿。

意义：如果 RL 连这族规则都打不过，那就是一个可靠的负结论；
如果打过了，说明 RL 学到的确实超出"按电价分档"的手写逻辑。
无论哪种，都是诚实且完整的对比。
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
EVAL_EPISODES = 10
EVAL_SEED0 = 9900

# 网格
THRESHOLDS = [0.10, 0.50]
# 谷段 = 22:00~06:00，正好覆盖睡眠时段（睡眠舒适带 24~27°C）。
# 因此谷段预冷到 24 会把室温压到带下沿、滞环再往下就跌破 24，
# 造成睡眠不达标。原网格只有 23/24 两档，导致电价规则最好的点
# 不达标率停在 2.6% 而无法与"全温度预冷到 25"的 0.7% 比较。
# 现补入 25 与 26 两档。
PRE_VALLEY = [24.0, 25.0, 26.0]   # 谷段预冷温度
PRE_FLAT = [24.0, 25.0]           # 平段预冷温度
PRE_PEAK = [25.0, 26.0, None]     # 峰段预冷温度；None = 峰段不预冷
MAINTAIN = [25.0]

BASE_JSON = os.path.join(PROJ, "models", "gen7_rule_ceiling", "results.json")
OUT = os.path.join(PROJ, "models", "gen7_price_rule", "results.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7电价感知规则上限.md")

T95 = {9: 2.262, 7: 2.365, 4: 2.776, 2: 4.303, 1: 12.706}


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


def make_price_rule(th, pre_valley, pre_flat, pre_peak, maintain):
    """电价感知的风险阈值规则。

    有人在               ->  维持 maintain
    无人且 p >= 阈值      ->  按当前电价档位选预冷温度
                             （峰段 pre_peak=None 表示峰段不预冷）
    其余                 ->  室温 >27°C 才开，否则关
    """
    def fn(env):
        od = env.get_obs_dict()
        T = float(od["temp_in"])
        occ = bool(od["occ_a"] or od["occ_b"])
        p = float(od["p_arrive_soon"])
        cfg = env.act_cfg
        rc = env.rew_cfg
        hour = int(od["hour"])

        if occ:
            target = maintain
        elif p >= th:
            price = rc.get_price(hour)
            if price <= rc.price_valley + 1e-9:
                target = pre_valley
            elif price >= rc.price_peak - 1e-9:
                target = pre_peak
            else:
                target = pre_flat
        else:
            if T <= 27.0:
                return encode_action(AC_OFF, 0, 0, cfg)
            target = pre_flat

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
    return fn


def evaluate(fn, n=EVAL_EPISODES):
    """与 gen7_rule_ceiling.py 完全相同的度量口径。"""
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


def pareto(rows, key_x="cost", key_y="occ_discomfort"):
    front = []
    for r in rows:
        dom = any((o[key_x] <= r[key_x] + 1e-9
                   and o[key_y] <= r[key_y] + 1e-9
                   and (o[key_x] < r[key_x] - 1e-9
                        or o[key_y] < r[key_y] - 1e-9))
                  for o in rows)
        if not dom:
            front.append(r)
    front.sort(key=lambda x: x[key_x])
    return front


def main():
    rows = []

    # 载入既有的非电价感知规则结果，稍后合并
    base_rows = []
    if os.path.exists(BASE_JSON):
        with open(BASE_JSON, encoding="utf-8") as f:
            base_rows = json.load(f).get("results", [])
        print(f"  已载入既有规则 {len(base_rows)} 条（用于合并前沿）")

    print("=" * 92)
    print(" gen7：电价感知的风险阈值规则（选项 B）")
    print("=" * 92)
    print(f"  评估 {EVAL_EPISODES} 回合 x 7 天")
    print(f"  网格：阈值 {THRESHOLDS} x 谷 {PRE_VALLEY} x 平 {PRE_FLAT}"
          f" x 峰 {PRE_PEAK} x 维持 {MAINTAIN}")
    print()

    for th in THRESHOLDS:
        for pv in PRE_VALLEY:
            for pf in PRE_FLAT:
                for pp in PRE_PEAK:
                    pp_s = "关" if pp is None else f"{pp:.0f}"
                    name = (f"电价风险>={th:.2f} 谷{pv:.0f}/平{pf:.0f}"
                            f"/峰{pp_s} 维持{MAINTAIN[0]:.0f}")
                    r = evaluate(make_price_rule(th, pv, pf, pp,
                                                MAINTAIN[0]))
                    r["name"] = name
                    r["threshold"] = th
                    r["pre_valley"] = pv
                    r["pre_flat"] = pf
                    r["pre_peak"] = pp
                    r["family"] = "price"
                    rows.append(r)

    print(f"  {'规则':<34}{'电量':>8}{'电费':>8}{'有人不达标':>11}")
    print("  " + "-" * 62)
    for r in sorted(rows, key=lambda x: x["cost"]):
        print(f"  {r['name']:<34}{r['energy']:>8.3f}{r['cost']:>8.3f}"
              f"{r['occ_discomfort']:>11.1%}")

    # 合并两族后重算前沿
    allrows = base_rows + rows
    front = pareto(allrows)
    print()
    print("=" * 92)
    print(f" 合并两族后的帕累托前沿：{len(front)} 点")
    print("=" * 92)
    print(f"  {'规则':<34}{'电量':>8}{'电费':>8}{'有人不达标':>11}")
    print("  " + "-" * 62)
    for r in front:
        tag = "" if r.get("family") == "price" else "  "
        print(f"  {r['name']:<34}{r['energy']:>8.3f}{r['cost']:>8.3f}"
              f"{r['occ_discomfort']:>11.1%}{tag}")

    best = min(allrows, key=lambda x: (x["occ_discomfort"], x["cost"]))
    print()
    print(f"  最佳舒适度：{best['name']}")
    print(f"    有人不达标 {best['occ_discomfort']:.2%}，"
          f"电费 {best['cost']:.3f} 元/天")

    # 与非电价感知规则做**等舒适度**对比（不能用"各自最舒适的点"比，
    # 那是不同舒适度水平，结论无意义 —— 初版这里写错了一次）。
    non_price = [r for r in base_rows if r.get("threshold") is not None]

    def interp(front_rows, d):
        """在给定前沿上按有人不达标率线性插值电费。"""
        pts = sorted(front_rows, key=lambda x: x["occ_discomfort"])
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

    front_price = pareto(rows)
    front_non = pareto(non_price + [
        r for r in base_rows if r.get("name", "").startswith(("全开", "25°C", "26°C"))])
    print()
    print("=" * 92)
    print(" 等舒适度对比：电价感知 vs 非电价感知（同为规则的两种写法）")
    print("=" * 92)
    print(f"  {'有人不达标':>10}{'电价规则':>11}{'非电价规则':>12}{'差值':>10}")
    print("  " + "-" * 46)
    cmp_rows = []
    for d in [0.007, 0.013, 0.026, 0.05, 0.10, 0.20, 0.30]:
        cp = interp(front_price, d)
        cn = interp(front_non, d)
        if cp is None or cn is None:
            miss = "电价无此水平" if cp is None else "非电价无此水平"
            print(f"  {d:>10.1%}{miss:>35}")
            continue
        diff = (cp - cn) / cn
        cmp_rows.append({"d": d, "price": cp, "non_price": cn, "diff": diff})
        print(f"  {d:>10.1%}{cp:>11.3f}{cn:>12.3f}{diff:>+10.1%}")
    if cmp_rows:
        best_gain = max(cmp_rows, key=lambda x: -x["diff"])
        print()
        print(f"  电价维度带来的最大收益：在 {best_gain['d']:.1%} 不达标率处"
              f"省 {abs(best_gain['diff']):.1%}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"episodes": EVAL_EPISODES, "results": rows,
                   "combined_front": front}, f, ensure_ascii=False,
                  indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：电价感知的风险阈值规则（选项 B）", "",
          f"- 生成：2026-10-18",
          f"- 场景：semi_markov 占用 + 分时电价，{EVAL_EPISODES} 回合 x 7 天",
          "- 目的：把电价维度补进规则族，得到更强的基线，"
          "避免用「不用电价」的规则去对比会用电价的策略。", "",
          "## 一、为什么补这一族", "",
          "`gen7_rule_ceiling.py` 的风险规则**完全没用电价**，"
          "它只是「快要回来就开到固定温度」。",
          "而电价是唯一能让「预冷多深」变成二维决策的量："
          "谷段便宜值得压深，峰段贵应该等。", "",
          "## 二、规则定义", "",
          "```",
          "有人在              ->  维持 maintain",
          "无人且 p >= 阈值     ->  按电价档位选预冷温度",
          "                        谷段 -> pre_valley",
          "                        峰段 -> pre_peak（可为「不预冷」）",
          "                        平段 -> pre_flat",
          "其余                ->  室温 >27°C 才开，否则关",
          "```", "",
          "## 三、全部结果", "",
          "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["cost"]):
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 四、合并两族后的帕累托前沿", "",
           "| 规则 | 电量 | 电费 | 有人不达标 |", "|---|---|---|---|"]
    for r in front:
        md.append(f"| {r['name']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['occ_discomfort']:.1%} |")
    md += ["", "## 五、结论", "",
           f"- 最佳舒适度：**{best['occ_discomfort']:.2%}**"
           f"（{best['name']}），电费 **{best['cost']:.3f} 元/天**",
           f"- 合并前沿共 **{len(front)}** 点", "",
           "**这份前沿是强化学习在 gen7 下的最终对手。**", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
