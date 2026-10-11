r"""
正面结果清点 —— 逐条核实并区分"真赢"与"有条件成立"

用途：
    在写中期报告前，系统清点本项目的所有正面结果，
    并逐条核实原始数据，明确每条结论的：
      · 是否与规则控制的正面对比（而非只与弱基线比）
      · 是否在同等条件下取得（同能耗 / 同舒适度）
      · 代价是什么

    目的是避免两种错误：
      (1) 夸大：把"相对恒温运行的节能"说成"相对规则控制的节能"
      (2) 埋没：把真实的正面结果说得过于保守

运行方式：
    python scripts\positive_results_audit.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def load(path):
    p = PROJECT_ROOT / "models" / path
    if not p.exists():
        return None
    return json.load(open(p, encoding="utf-8"))


def g(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def main():
    print("=" * 94)
    print(" 正面结果清点 —— 逐条核实，区分「真赢」与「有条件成立」")
    print("=" * 94)

    findings = []

    # ============================================================
    # 1. 舒适度优势（对照规则控制）
    # ============================================================
    d = load("evaluation/results_v2_notou.json")
    if d:
        r = d["results"]
        rule_d = g(r, "规则控制(定温)", "discomfort_ratio", "mean")
        ppo_d = g(r, "PPO:rl2_notou", "discomfort_ratio", "mean")
        rule_e = g(r, "规则控制(定温)", "energy_per_day_kwh", "mean")
        ppo_e = g(r, "PPO:rl2_notou", "energy_per_day_kwh", "mean")
        red = (rule_d - ppo_d) / rule_d
        cost = (ppo_e - rule_e) / rule_e
        findings.append({
            "name": "舒适度不达标率显著更低",
            "vs": "规则控制（定温）",
            "value": f"{rule_d:.2%} -> {ppo_d:.2%}（降低 {red:.1%}）",
            "condition": f"代价：能耗高 {cost:.1%}",
            "verdict": "真赢（同类对比，但非免费）",
        })

        # 切换次数
        rule_s = g(r, "规则控制(定温)", "switch_per_day", "mean")
        ppo_s = g(r, "PPO:rl2_notou", "switch_per_day", "mean")
        findings.append({
            "name": "设备切换次数显著更少",
            "vs": "规则控制（定温）",
            "value": f"{rule_s:.1f} -> {ppo_s:.1f} 次/天（降低 {1-ppo_s/rule_s:.1%}）",
            "condition": "几乎无代价（压缩机启停减少，有利于设备寿命）",
            "verdict": "真赢（近免费）",
        })

        # RMSE
        rule_rm = g(r, "规则控制(定温)", "temp_dev_rmse", "mean")
        ppo_rm = g(r, "PPO:rl2_notou", "temp_dev_rmse", "mean")
        findings.append({
            "name": "温度控制精度显著更高（RMSE）",
            "vs": "规则控制（定温）",
            "value": f"{rule_rm:.3f} -> {ppo_rm:.3f}（降低 {1-ppo_rm/rule_rm:.1%}）",
            "condition": "与舒适度优势同源（都是控制精度）",
            "verdict": "真赢",
        })

        # 相对恒温运行的节能
        al_e = g(r, "全程开空调25°C", "energy_per_day_kwh", "mean")
        al_d = g(r, "全程开空调25°C", "discomfort_ratio", "mean")
        save = (al_e - ppo_e) / al_e
        findings.append({
            "name": "相对恒温运行节能",
            "vs": "全程开空调 25°C（弱基线）",
            "value": f"{al_e:.3f} -> {ppo_e:.3f} kWh/天（节能 {save:.1%}）",
            "condition": f"舒适度基本持平（{al_d:.2%} vs {ppo_d:.2%}）",
            "verdict": "成立，但基线较弱（恒温运行不是有竞争力的策略）",
        })

    # ============================================================
    # 2. 循环网络的增益
    # ============================================================
    d = load("recurrent/results.json")
    if d:
        res = d["results"]
        ppo_c = g(res, "B2 普通 PPO", "energy_cost_yuan", "mean")
        rec_c = g(res, "B3 RecurrentPPO", "energy_cost_yuan", "mean")
        ppo_v = g(res, "B2 普通 PPO", "tier_share", "valley")
        rec_v = g(res, "B3 RecurrentPPO", "tier_share", "valley")
        findings.append({
            "name": "循环网络（LSTM）降低电费",
            "vs": "普通 PPO（MLP）",
            "value": f"{ppo_c:.3f} -> {rec_c:.3f} 元/天（降低 {(ppo_c-rec_c)/ppo_c:.1%}）",
            "condition": f"但谷段占比反而下降（{ppo_v:.1%} -> {rec_v:.1%}），"
                         f"改善来自总用电下降而非错峰",
            "verdict": "真赢（同类对比，机制已查明）",
        })

    # ============================================================
    # 3. 储能的增益
    # ============================================================
    d = load("storage/results.json")
    if d:
        res = d["results"]
        a1 = g(res, "A 规则控制（无储能）", "cost")
        b1 = g(res, "B 规则+储能(4kWh)", "cost")
        a2 = g(res, "C PPO（无储能）", "cost")
        b2 = g(res, "D PPO+储能(4kWh)", "cost")
        b1v = g(res, "B 规则+储能(4kWh)", "valley_share")
        findings.append({
            "name": "显式储能降低规则控制电费",
            "vs": "规则控制（无储能）",
            "value": f"{a1:.3f} -> {b1:.3f} 元/天（降低 {(a1-b1)/a1:.1%}）",
            "condition": f"谷段占比提升到 {b1v:.1%}，峰段接近 0",
            "verdict": "真赢（储能的有效性已验证）",
        })
        findings.append({
            "name": "显式储能降低 PPO 电费",
            "vs": "PPO（无储能）",
            "value": f"{a2:.3f} -> {b2:.3f} 元/天（降低 {(a2-b2)/a2:.1%}）",
            "condition": "储能对双方都有效，因此不改变相对比较关系",
            "verdict": "真赢（但非差异化优势）",
        })

    # ============================================================
    # 4. 工作区间更宽（汇总多个实验，避免以偏概全）
    # ============================================================
    # 说明：不同实验的规则配置不同，因此"规则最低不达标率"不同。
    #   帕累托综合分析 / 参数扫描 : 规则 29.1%  vs  PPO 8.2%
    #   储能实验（单旋钮规则）    : 规则 24.2%  vs  PPO 11.5%
    #   储能实验（三旋钮规则）    : 规则 26.4%  vs  PPO 19.4%
    # 因此稳健的表述是"规则 24%~29%，PPO 8%~19%"，而非单一数字。
    work_range = []
    d = load("pareto/results.json")
    if d:
        for scen, v in d.items():
            if "有" in scen:
                rf = v.get("rule_front") or []
                pf = v.get("ppo_front") or []
                if rf and pf:
                    work_range.append(("帕累托综合分析",
                                       min(x[0] for x in rf),
                                       min(x[0] for x in pf)))
    d = load("storage_pareto/results.json")
    if d:
        rf = d.get("rule_front") or []
        pf = d.get("ppo_front") or []
        if rf and pf:
            work_range.append(("储能实验（单旋钮规则）",
                               min(x[0] for x in rf), min(x[0] for x in pf)))
    d = load("storage_equal/results.json")
    if d:
        rf = d.get("rule_front") or []
        pf = d.get("ppo_front") or []
        if rf and pf:
            work_range.append(("储能实验（三旋钮规则）",
                               min(x[0] for x in rf), min(x[0] for x in pf)))

    if work_range:
        rule_lo = min(w[1] for w in work_range)
        rule_hi = max(w[1] for w in work_range)
        ppo_lo = min(w[2] for w in work_range)
        ppo_hi = max(w[2] for w in work_range)
        findings.append({
            "name": "可达的舒适度工作区间更宽",
            "vs": "规则控制（多种配置）",
            "value": f"规则最低 {rule_lo:.1%}~{rule_hi:.1%}，"
                     f"PPO 可达 {ppo_lo:.1%}~{ppo_hi:.1%}",
            "condition": "两者都能达到的区间内规则电费更低；"
                         "此为「能力边界」而非「同条件胜出」",
            "verdict": "成立（良性差异化，需说明是能力边界）",
            "detail": work_range,
        })

    # ============================================================
    # 5. 规则的隐含适应成本（本轮新发现）
    # ============================================================
    d = load("tariff_change/results.json")
    if d:
        crit = d.get("criteria", {})
        res = d.get("results", {})
        b1 = g(res, "B1 规则（未适配）", "cost")
        b2 = g(res, "B2 规则（结构2重新调参）", "cost")
        if b1 and b2:
            findings.append({
                "name": "量化了规则的隐含适应成本",
                "vs": "规则自身（未适配 vs 重新调参）",
                "value": f"{b1:.3f} -> {b2:.3f} 元/天（未适配代价 "
                         f"{(b1-b2)/b2:+.1%}）",
                "condition": "这是规则方案此前未被计入的成本，"
                             "在电价频繁变化场景下会反复发生",
                "verdict": "方法学新发现（对学习型方法有利的论据）",
            })

    # ============================================================
    # 6. 工程与验证性成果
    # ============================================================
    eng = [
        ("环境自检全部通过", "6 类检查：接口/观测/动力学/奖励/约束/指标"),
        ("数据标定链路已验证", "合成数据反推热工参数，误差 0.2% / 0.4%"),
        ("马尔可夫性检验通过", "状态分箱后的动作一致性检验"),
        ("约束机制无冲突", "C4 绕过 C1/C2 的震荡问题已修复，异常样本归零"),
        ("评估口径统一", "修正了早期同一策略给出 2.0 与 3.9 kWh/天两个矛盾值的问题"),
        ("文档数据一致性", "自动核对脚本覆盖全部实验，0 处不符"),
    ]

    # ---------- 输出 ----------
    print("\n" + "-" * 94)
    print(" A. 与规则控制的正面对比（最有分量）")
    print("-" * 94)
    for f in findings:
        if "规则" in f["vs"] and "真赢" in f["verdict"]:
            print(f"\n  ● {f['name']}")
            print(f"    对照: {f['vs']}")
            print(f"    结果: {f['value']}")
            print(f"    条件: {f['condition']}")
            print(f"    判定: {f['verdict']}")

    print("\n" + "-" * 94)
    print(" B. 相对弱基线的正向结果（成立，但需说明基线）")
    print("-" * 94)
    for f in findings:
        if "弱基线" in f["vs"] or "PPO（MLP）" in f["vs"] \
           or "无储能" in f["vs"] or "规则自身" in f["vs"]:
            print(f"\n  ● {f['name']}")
            print(f"    对照: {f['vs']}")
            print(f"    结果: {f['value']}")
            print(f"    条件: {f['condition']}")
            print(f"    判定: {f['verdict']}")

    print("\n" + "-" * 94)
    print(" C. 能力边界类结果（差异化，非同条件胜出）")
    print("-" * 94)
    for f in findings:
        if "边界" in f["verdict"]:
            print(f"\n  ● {f['name']}")
            print(f"    对照: {f['vs']}")
            print(f"    结果: {f['value']}")
            print(f"    条件: {f['condition']}")
            print(f"    判定: {f['verdict']}")
            if f.get("detail"):
                print(f"    分实验明细：")
                for src, rd, pd in f["detail"]:
                    print(f"      {src:<22} 规则 {rd:.1%}  PPO {pd:.1%}"
                          f"  差距 {rd-pd:+.1%}")

    print("\n" + "-" * 94)
    print(" D. 工程与验证性成果（支撑可信度，非性能优势）")
    print("-" * 94)
    for name, desc in eng:
        print(f"  ✓ {name}")
        print(f"      {desc}")

    # ---------- 汇总 ----------
    n_head2head = sum(1 for f in findings
                      if "规则" in f["vs"] and "真赢" in f["verdict"])
    print("\n" + "=" * 94)
    print(" 汇总")
    print("=" * 94)
    print(f"\n  与规则控制的正面对比（真赢）: {n_head2head} 条")
    print(f"  相对弱基线的正向结果        : "
          f"{sum(1 for f in findings if '弱基线' in f['vs'] or 'MLP' in f['vs'] or '无储能' in f['vs'])} 条")
    print(f"  能力边界类                  : "
          f"{sum(1 for f in findings if '边界' in f['verdict'])} 条")
    print(f"  工程与验证性成果            : {len(eng)} 条")

    print("\n  最诚实的一句话总结：")
    print("    本方法**确实**在舒适度与控制平稳性上显著优于规则控制，")
    print("    并且可达的舒适度工作区间更宽；")
    print("    但在**电费**这一维度上，始终未能超过人工规则，")
    print("    即使尝试了七条技术路线。")
    print("\n  因此正确定位是：**这是一个「舒适度优先」的方法，")
    print("    而非「电费优先」的方法。**")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
