# -*- coding: utf-8 -*-
"""对比「标定前（旧参数）」与「标定后（新参数）」的实验结果。

目的
----
2026-10-16 用 49.9 小时实测数据标定了仿真热参数，四个参数显著变化：

    temp_outdoor_mean   30.0 -> 22.0
    temp_outdoor_swing   5.0 ->  3.0
    ua_coef             0.040 -> 0.115
    internal_gain        0.02 -> 0.80

旧结果已备份到 models/_oldparams_backup/。本脚本把新旧结果并排比较，
量化"参数标定对结论的影响"，供结题报告使用。

输出
----
docs/参数标定前后结果对比.md
models/calibration_impact.json
"""

import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
MODELS = os.path.join(PROJ, "models")
BACKUP = os.path.join(MODELS, "_oldparams_backup")
OUT_MD = os.path.join(PROJ, "docs", "参数标定前后结果对比.md")
OUT_JSON = os.path.join(MODELS, "calibration_impact.json")


def load(rel, backup=False):
    base = BACKUP if backup else MODELS
    p = os.path.join(base, rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def g(d, *path, default=None):
    """安全取嵌套值。"""
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def main():
    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("# 参数标定前后结果对比")
    P()
    P(f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P("- 标定依据：2026-10-14~16 实测 17918 条样本（49.9 小时）")
    P()
    P("## 参数变化")
    P()
    P("| 参数 | 标定前 | 标定后 | 含义 |")
    P("|---|---|---|---|")
    P("| `temp_outdoor_mean` | 30.0 °C | **22.0 °C** | 室外平均气温（实测十月上海） |")
    P("| `temp_outdoor_swing` | 5.0 °C | **3.0 °C** | 室外日内波动 |")
    P("| `ua_coef` | 0.040 | **0.115** | 围护结构传热（隐含 τ 375→130 分钟） |")
    P("| `internal_gain` | 0.02 | **0.80** | 内部热增益（稳态偏移 0.5→7.0 °C） |")
    P()
    P("---")
    P()

    impact = {}

    # ============ 1. 消融实验 ============
    # 注意：备份的旧消融结果文件为 0 字节（历史遗留的损坏文件），
    # 无法直接对比。因此旧值取自 docs/项目状态总结.md 中已归档的记载
    # （5 种子 × 15 回合 = 75 样本/组，与本次重跑口径一致）。
    OLD_ABLATION_DOC = {
        "A1 固定权重": {"energy": 4.771, "energy_sd": 0.106,
                        "discomfort": 0.1204, "discomfort_sd": 0.0030},
        "A2 场景化权重(本方法)": {"energy": 5.018, "energy_sd": 0.164,
                                  "discomfort": 0.1189, "discomfort_sd": 0.0032},
        "A3 随机变化权重": {"energy": 4.794, "energy_sd": 0.148,
                            "discomfort": 0.1192, "discomfort_sd": 0.0030},
    }
    new_ab = load("ablation_multiseed/results.json")
    if new_ab:
        P("## 一、消融实验（5 种子 × 15 回合 = 75 样本/组）")
        P()
        P("> 旧值来源：`docs/项目状态总结.md` 已归档的记载。")
        P("> 备份目录中的旧消融结果文件为 0 字节（历史遗留损坏文件），")
        P("> 无法直接读取，故以文档记载为准。")
        P()
        P("| 组别 | 指标 | 标定前（文档） | 标定后（重跑） | 变化 |")
        P("|---|---|---|---|---|")
        rows = []
        for grp in ["A1 固定权重", "A2 场景化权重(本方法)", "A3 随机变化权重"]:
            ov = OLD_ABLATION_DOC.get(grp, {})
            nv_e = g(new_ab, "groups", grp, "energy_per_day_kwh", "mean")
            nv_d = g(new_ab, "groups", grp, "discomfort_ratio", "mean")
            nv_s = g(new_ab, "groups", grp, "switch_per_day", "mean")
            if nv_e is not None and "energy" in ov:
                delta = f"{(nv_e-ov['energy'])/ov['energy']*100:+.1f}%"
                P(f"| {grp} | 日均电量(kWh) | {ov['energy']:.3f}±{ov['energy_sd']:.3f} | "
                  f"**{nv_e:.3f}** | {delta} |")
                rows.append((grp, "energy", ov["energy"], nv_e))
            if nv_d is not None and "discomfort" in ov:
                delta = f"{(nv_d-ov['discomfort'])*100:+.2f} 个百分点"
                P(f"| {grp} | 不达标率 | {ov['discomfort']:.2%} | **{nv_d:.2%}** | {delta} |")
                rows.append((grp, "discomfort", ov["discomfort"], nv_d))
            if nv_s is not None:
                P(f"| {grp} | 日均切换 | — | **{nv_s:.1f}** | — |")
            P("| | | | | |")
        impact["ablation"] = [{"group": r[0], "metric": r[1], "old": r[2], "new": r[3]}
                              for r in rows]
        P()

        # 组间结论是否变化
        P("**组间比较（能耗）**")
        P()
        P("| 对比 | 标定前 | 标定后 | 方向是否一致 |")
        P("|---|---|---|---|")
        for a, b, name in [("A2 场景化权重(本方法)", "A1 固定权重", "A2 vs A1"),
                           ("A2 场景化权重(本方法)", "A3 随机变化权重", "A2 vs A3")]:
            oa = OLD_ABLATION_DOC.get(a, {}).get("energy")
            ob = OLD_ABLATION_DOC.get(b, {}).get("energy")
            na = g(new_ab, "groups", a, "energy_per_day_kwh", "mean")
            nb = g(new_ab, "groups", b, "energy_per_day_kwh", "mean")
            if None in (oa, ob, na, nb):
                continue
            od = (oa - ob) / ob
            nd = (na - nb) / nb
            same = "一致" if (od > 0) == (nd > 0) else "**相反**"
            P(f"| {name} | {od:+.2%} | {nd:+.2%} | {same} |")
        P()

    # ============ 2. 储能实验 ============
    new_st = load("storage/results.json")
    old_st = load("storage/results.json", backup=True)
    if new_st and old_st:
        P("## 二、显式储能实验")
        P()
        P("| 场景 | 指标 | 标定前 | 标定后 | 变化 |")
        P("|---|---|---|---|---|")
        for k in ["A 规则控制（无储能）", "B 规则+储能(4kWh)",
                  "C PPO（无储能）", "D PPO+储能(4kWh)"]:
            for metric, label, fmt in [("cost", "电费(元/天)", "{:.3f}"),
                                       ("energy", "电量(kWh/天)", "{:.3f}"),
                                       ("discomfort", "不达标率", "{:.1%}")]:
                o = g(old_st, "results", k, metric)
                n = g(new_st, "results", k, metric)
                if o is None or n is None:
                    continue
                delta = f"{(n-o)/o*100:+.1f}%" if o else "-"
                P(f"| {k} | {label} | {fmt.format(o)} | **{fmt.format(n)}** | {delta} |")
            P("| | | | | |")
        P()

    # ============ 3. 循环网络 ============
    new_rc = load("recurrent/results.json")
    old_rc = load("recurrent/results.json", backup=True)
    if new_rc and old_rc:
        P("## 三、循环网络对比")
        P()
        P("| 策略 | 指标 | 标定前 | 标定后 | 变化 |")
        P("|---|---|---|---|---|")
        for k in ["B1 规则控制", "B2 普通 PPO", "B3 RecurrentPPO"]:
            for metric, label, fmt in [("energy_cost_yuan", "电费(元/天)", "{:.3f}"),
                                       ("discomfort_ratio", "不达标率", "{:.1%}")]:
                o = g(old_rc, "results", k, metric, "mean")
                n = g(new_rc, "results", k, metric, "mean")
                if o is None or n is None:
                    continue
                delta = f"{(n-o)/o*100:+.1f}%" if o else "-"
                P(f"| {k} | {label} | {fmt.format(o)} | **{fmt.format(n)}** | {delta} |")
            P("| | | | | |")
        P()

    # ============ 4. 帕累托前沿 ============
    new_pa = load("pareto/results.json")
    old_pa = load("pareto/results.json", backup=True)
    if new_pa and old_pa:
        P("## 四、帕累托前沿（按场景）")
        P()
        for scene in ["无分时电价", "有分时电价"]:
            P(f"**{scene}**")
            P()
            P("| 来源 | 标定前 不达标率范围 | 标定后 不达标率范围 |")
            P("|---|---|---|")
            for who, key in [("规则控制", "rule_front"), ("强化学习", "ppo_front")]:
                of = g(old_pa, scene, key) or []
                nf = g(new_pa, scene, key) or []
                # 前沿元素格式为 [不达标率, 电费, 来源]
                def rng(fr, idx=0, pct=True):
                    vs = []
                    for x in fr:
                        if isinstance(x, (list, tuple)) and len(x) > idx:
                            try:
                                vs.append(float(x[idx]))
                            except (TypeError, ValueError):
                                pass
                    if not vs:
                        return "-"
                    return (f"{min(vs):.1%} ~ {max(vs):.1%}" if pct
                            else f"{min(vs):.3f} ~ {max(vs):.3f}")
                P(f"| {who} | {rng(of)} | {rng(nf)} |")
            P()
            # 电费范围（有分时电价时才有意义）
            P("| 来源 | 标定前 电费范围(元/天) | 标定后 电费范围(元/天) |")
            P("|---|---|---|")
            for who, key in [("规则控制", "rule_front"), ("强化学习", "ppo_front")]:
                of = g(old_pa, scene, key) or []
                nf = g(new_pa, scene, key) or []
                def rngc(fr):
                    vs = []
                    for x in fr:
                        if isinstance(x, (list, tuple)) and len(x) > 1:
                            try:
                                vs.append(float(x[1]))
                            except (TypeError, ValueError):
                                pass
                    return f"{min(vs):.3f} ~ {max(vs):.3f}" if vs else "-"
                P(f"| {who} | {rngc(of)} | {rngc(nf)} |")
            P()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(impact, f, ensure_ascii=False, indent=2)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    P()
    P(f"报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
