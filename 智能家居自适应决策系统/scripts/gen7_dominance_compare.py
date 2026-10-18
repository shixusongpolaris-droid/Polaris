# -*- coding: utf-8 -*-
"""gen7：用「占优判据」重算等舒适度对比（不用跨空档插值）。

问题
----
合并后的规则前沿仍有一个 29.7pp 的空档（2.64% ~ 32.33%）。而 RL 的
工作点（1.16% / 1.54% / 5.02% / 8.70%）里有三个落在该空档内。

在空档内做线性插值，等于用一条不存在的规则去和 RL 比 —— 这正是
本项目此前反复批评的做法，我自己又犯了一次。

正确判据（占优判据，无需插值）
------------------------------
对每个 RL 工作点 (d_rl, c_rl)：

    参照值 = min{ c | (d, c) ∈ 规则前沿 且 d <= d_rl }

即「所有不舒适率不高于 RL 的规则中，最便宜的那条」。
这是一个**真实存在的规则**，不需要任何插值。

  若 c_rl < 参照值 -> RL 在同等或更好的舒适度下更省（RL 占优）
  若 c_rl > 参照值 -> 存在一条规则，既更舒适又更便宜（规则占优）

这个判据比插值保守：它要求规则在**不更差**的舒适度下比较，因此
不会因为空档左侧那个点恰好很便宜而冤枉 RL，也不会因为插值而抬高规则。

输出
----
docs/gen7占优判据对比.md
models/gen7_final_front/dominance.json
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
FRONT = os.path.join(PROJ, "models", "gen7_final_front", "results.json")
OUT = os.path.join(PROJ, "models", "gen7_final_front", "dominance.json")
OUT_MD = os.path.join(PROJ, "docs", "gen7占优判据对比.md")


def main():
    with open(FRONT, encoding="utf-8") as f:
        d = json.load(f)
    front = d["front"]
    rl = d["rl_agg"]

    print("=" * 90)
    print(" gen7：占优判据下的等舒适度对比（不使用插值）")
    print("=" * 90)
    print(f"  规则前沿 {len(front)} 点")
    ds = sorted(r["occ_discomfort"] for r in front)
    gaps = [(ds[i], ds[i + 1], ds[i + 1] - ds[i]) for i in range(len(ds) - 1)]
    g = max(gaps, key=lambda x: x[2])
    print(f"  最大空档 {g[0]:.2%} ~ {g[1]:.2%}（宽 {g[2]:.1%}）")
    print()

    # 占优参照：不舒适率 <= d 的规则中电费最低者
    def ref_for(d_rl):
        cands = [r for r in front if r["occ_discomfort"] <= d_rl + 1e-12]
        if not cands:
            return None
        return min(cands, key=lambda r: r["cost"])

    print(f"  {'es':>4}{'RL 不达标':>11}{'RL 电费':>10}"
          f"{'参照规则':>28}{'参照电费':>10}{'差值':>9}{'t':>7}{'判定':>14}")
    print("  " + "-" * 92)

    rows = []
    for a in rl:
        d_rl = a["occ_discomfort"]
        c_rl = a["cost"]
        ref = ref_for(d_rl)
        if ref is None:
            print(f"  {a['scale']:>4.0f}{d_rl:>11.2%}{c_rl:>10.3f}"
                  f"{'无参照':>28}")
            continue
        diff = (c_rl - ref["cost"]) / ref["cost"]
        costs = [s["cost"] for s in a.get("per_seed", [])]
        t = None
        if len(costs) >= 2:
            se = stdev(costs) / math.sqrt(len(costs))
            if se > 1e-12:
                t = (c_rl - ref["cost"]) / se
        sig = "显著" if (t is not None and abs(t) > 2.0) else "不显著"
        if diff < 0:
            who = "RL 占优"
        else:
            who = "规则占优"
        t_s = f"{t:+.1f}" if t is not None else "-"
        print(f"  {a['scale']:>4.0f}{d_rl:>11.2%}{c_rl:>10.3f}"
              f"{ref['name'][:28]:>28}{ref['cost']:>10.3f}"
              f"{diff:>+9.1%}{t_s:>7}{who:>10}({sig})")
        rows.append({"scale": a["scale"], "rl_d": d_rl, "rl_cost": c_rl,
                     "ref_name": ref["name"],
                     "ref_cost": ref["cost"],
                     "ref_d": ref["occ_discomfort"],
                     "diff": diff, "t": t, "sig": sig, "who": who})

    # 也给出「RL 是否被某条规则严格支配」的判定
    print()
    print("=" * 90)
    print(" 严格支配判定（规则的舒适度不差于 RL 且电费不高于 RL）")
    print("=" * 90)
    for a in rl:
        doms = [r for r in front
                if r["occ_discomfort"] <= a["occ_discomfort"] + 1e-12
                and r["cost"] <= a["cost"] + 1e-12]
        if doms:
            best = min(doms, key=lambda r: r["cost"])
            print(f"  es={a['scale']:>3.0f} 被 {len(doms)} 条规则支配，"
                  f"其中最优：{best['name'][:34]}"
                  f"（{best['occ_discomfort']:.2%} / {best['cost']:.3f}）")
        else:
            print(f"  es={a['scale']:>3.0f} 未被任何规则支配")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n  已保存: {OUT}")

    md = ["# gen7：占优判据下的等舒适度对比", "",
          f"- 生成：2026-10-18",
          "- 不使用插值：参照值取「所有不舒适率不高于 RL 的规则中电费最低者」，"
          "是一个真实存在的规则", "",
          f"规则前沿 {len(front)} 点，最大空档 {g[0]:.2%} ~ {g[1]:.2%}"
          f"（宽 {g[2]:.1%}）", "",
          "## 结果", "",
          "| es | RL 不达标 | RL 电费 | 参照规则 | 参照电费 | 差值 | t | 判定 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t_s = f"{r['t']:+.1f}" if r["t"] is not None else "-"
        md.append(f"| {r['scale']:.0f} | {r['rl_d']:.2%} "
                  f"| {r['rl_cost']:.3f} | {r['ref_name']} "
                  f"| {r['ref_cost']:.3f} | {r['diff']:+.1%} | {t_s} "
                  f"| {r['who']}（{r['sig']}）|")
    md += ["", "## 为什么用这个判据", "",
           "在规则前沿的空档内做线性插值，等于用一条不存在的规则去比。",
           "本项目此前多次批评这种做法，本轮自己也犯了一次。",
           "占优判据只用真实存在的规则点，代价是**更保守**：",
           "它要求参照规则的舒适度不差于 RL，因此不会冤枉 RL。", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
