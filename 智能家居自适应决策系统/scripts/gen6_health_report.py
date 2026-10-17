# -*- coding: utf-8 -*-
"""生成 gen6 vs gen5 的完整体检报告。

在 gen6 参数下重跑全部 9 项关键实验后，逐项与 gen5 对照，
并标出哪些结论收敛、哪些仍不稳定。
"""

import json
import os
from datetime import datetime

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
MD = os.path.join(PROJ, "models")
OUT = os.path.join(PROJ, "docs", "gen6体检报告.md")


def L(root, rel):
    p = os.path.join(MD, root, rel.replace("/", os.sep)) if root \
        else os.path.join(MD, rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def num(d, *path):
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    if isinstance(cur, dict):
        return cur.get("mean")
    return cur


def main():
    LINES = []

    def P(s=""):
        print(s)
        LINES.append(s)

    P("# gen6 体检报告（全部 9 项实验重跑后）")
    P()
    P(f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P("- gen6 参数：ua=0.15175, gain_base=0.8925, solar=0.215, "
      "ac_coef=1.83, ac_max=2.1175")
    P("- gen5 结果保存在 `models/_gen5_partial/`；更早各代在 "
      "`models/_gen{2,3,4}_backup/`")
    P()

    # ================= 1 消融 =================
    P("## 一、消融实验")
    P()
    P("| 组别 | gen4 电量/不达标 | gen5 | **gen6** |")
    P("|---|---|---|---|")
    d6 = L(None, "ablation_multiseed/results.json")
    d5 = L("_gen5_partial", "ablation_multiseed/results.json")
    d4 = L("_gen4_backup", "ablation_multiseed/results.json")
    for grp in ["A1 固定权重", "A2 场景化权重(本方法)", "A3 随机变化权重"]:
        cells = []
        for d in (d4, d5, d6):
            e = num(d, "groups", grp, "energy_per_day_kwh") if d else None
            x = num(d, "groups", grp, "discomfort_ratio") if d else None
            cells.append(f"{e:.3f} / {x:.2%}" if e is not None else "—")
        P(f"| {grp} | {cells[0]} | {cells[1]} | **{cells[2]}** |")
    P()
    P("**跨代稳定的结论（gen4/5/6 三代一致）**：A2（场景化权重）的电量"
      "始终高于两个对照组，且舒适度收益极小。")
    P()

    # ================= 2 储能 =================
    P("## 二、显式储能")
    P()
    P("| 代次 | 对规则的收益 | 对 PPO 的收益 |")
    P("|---|---|---|")
    for name, root in [("gen2", "_gen2_backup"), ("gen3", "_gen3_backup"),
                       ("gen4", "_gen4_backup"), ("gen5", "_gen5_partial"),
                       ("**gen6**", None)]:
        d = L(root, "storage/results.json")
        if not d:
            continue
        r = d.get("results", {})
        a = r.get("A 规则控制（无储能）", {}).get("cost")
        b = r.get("B 规则+储能(4kWh)", {}).get("cost")
        c = r.get("C PPO（无储能）", {}).get("cost")
        e = r.get("D PPO+储能(4kWh)", {}).get("cost")
        if None in (a, b, c, e):
            continue
        P(f"| {name} | {(a-b)/a:+.1%} | {(c-e)/c:+.1%} |")
    P()
    P("**结论收敛**：gen2/gen3 的「储能对 RL 负收益」是热参数错误"
      "（空调压不住温度）造成的假象；gen4/5/6 连续三代均为正收益。")
    P()

    # ================= 3 循环网络 =================
    P("## 三、循环网络")
    P()
    # 注意：必须单独载入 recurrent 的 gen5 结果，不能复用 d5（那是消融的）。
    r5 = L("_gen5_partial", "recurrent/results.json")
    r6 = L(None, "recurrent/results.json")
    P("| 策略 | gen5 电费/不达标 | **gen6 电费/不达标** |")
    P("|---|---|---|")
    for k in ["B1 规则控制", "B2 普通 PPO", "B3 RecurrentPPO"]:
        c5 = num(r5, "results", k, "energy_cost_yuan") if r5 else None
        x5 = num(r5, "results", k, "discomfort_ratio") if r5 else None
        c6 = num(r6, "results", k, "energy_cost_yuan") if r6 else None
        x6 = num(r6, "results", k, "discomfort_ratio") if r6 else None
        a = f"{c5:.3f} / {x5:.1%}" if c5 is not None else "—"
        b = f"{c6:.3f} / {x6:.1%}" if c6 is not None else "—"
        P(f"| {k} | {a} | **{b}** |")
    P()
    P("规则在电费上仍占优，但舒适度明显更差。")
    P()

    # ================= 4 基线 =================
    P("## 四、统一评估基线（无分时，20 回合）")
    P()
    P("> ⚠️ 本表的「电费」实为 **kWh×1（price_flat_tariff=1.0）**，"
      "即数值等于电量。真实分时电价下的电费见 `docs/规则能力上限.md`。")
    P()
    P("| 策略 | gen5 电量/不达标 | **gen6 电量/不达标** |")
    P("|---|---|---|")
    # gen5 的 evaluation 真值在 _gen5_evaluation（在 gen6 实验前从
    # models/evaluation 移出），而 _gen5_partial 里那份其实是 gen6。
    e5 = L("_gen5_evaluation", "results_default.json")
    e6 = L(None, "evaluation/results_default.json")
    for k in ["全程开空调25°C", "全程开空调27°C", "规则控制(定温)",
              "规则控制(按时段)", "全程不开空调"]:
        a1 = num(e5, "results", k, "energy_per_day_kwh") if e5 else None
        b1 = num(e5, "results", k, "discomfort_ratio") if e5 else None
        a2 = num(e6, "results", k, "energy_per_day_kwh") if e6 else None
        b2 = num(e6, "results", k, "discomfort_ratio") if e6 else None
        s5 = f"{a1:.3f} / {b1:.1%}" if a1 is not None else "—"
        s6 = f"{a2:.3f} / {b2:.1%}" if a2 is not None else "—"
        P(f"| {k} | {s5} | **{s6}** |")
    P()

    # ================= 5 能耗权重扫描 =================
    P("## 五、能耗权重扫描（分时电价）")
    P()
    d6e = L(None, "energy_sweep/results.json")
    d5e = L("_gen5_partial", "energy_sweep/results.json")
    P("| energy_scale | gen5 电费/不达标 | **gen6 电费/不达标** |")
    P("|---|---|---|")
    if d6e and d5e:
        m5 = {(r["energy_scale"], r["lookahead_weight"]): r
              for r in d5e.get("results", [])}
        for r in d6e.get("results", []):
            key = (r["energy_scale"], r["lookahead_weight"])
            a = m5.get(key)
            s5 = (f"{a['metrics']['cost']:.3f} / "
                  f"{a['metrics']['discomfort']:.1%}") if a else "—"
            s6 = (f"{r['metrics']['cost']:.3f} / "
                  f"{r['metrics']['discomfort']:.1%}")
            P(f"| es={r['energy_scale']:.0f}, la={r['lookahead_weight']:.1f} "
              f"| {s5} | **{s6}** |")
    P()

    # ================= 6 帕累托 =================
    P("## 六、帕累托分析")
    P()
    for name, root in [("gen5", "_gen5_partial"), ("**gen6**", None)]:
        d = L(root, "pareto/results.json")
        if not d:
            continue
        for scen in d:
            rf = d[scen].get("rule_front", [])
            pf = d[scen].get("ppo_front", [])
            P(f"- {name} {scen}：规则 {len(rf)} 点，RL {len(pf)} 点")
    P()

    # ================= 7 收敛性小结 =================
    P("## 七、哪些结论在 gen6 下收敛了")
    P()
    P("| 结论 | 跨代表现 | 判定 |")
    P("|---|---|---|")
    P("| 场景化权重（A2）不省电 | gen4 +6.9%、gen5 +6.6%、"
      "gen6 +10.7% | ✅ **收敛** |")
    P("| 储能对双方都有益 | gen4/5/6 均正（PPO +26.5%/+14.9%/+14.4%）"
      " | ✅ **收敛** |")
    P("| `energy_scale` 是决定变量 | 各代均单调，默认权重下 RL 电量约为"
      "规则 2 倍 | ✅ **收敛** |")
    P("| 规则在低舒适区更省 | 各代一致 | ✅ **收敛** |")
    P("| 规则可达更低不舒适 | 各代一致（gen6 达 0.00%） | ✅ **收敛** |")
    P("| 循环网络（RecurrentPPO）优于普通 PPO | gen2 是、gen3-6 均否 "
      "| ⚠️ 不稳定 |")
    P("| 具体电量/电费数字 | 每代都变 | ❌ 不稳定（勿单独引用） |")
    P()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    print(f"  报告已保存: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
