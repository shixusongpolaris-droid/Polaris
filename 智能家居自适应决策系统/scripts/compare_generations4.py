# -*- coding: utf-8 -*-
"""四代实验结果对比（通用版，代数可扩展）。

代次定义
--------
gen1  标定前（项目原始参数）
gen2  第一次热参数标定
gen3  全时段联合标定（但 occupancy 判定有字符串包含 bug）
gen4  修正 occupancy bug + 热模型加入 gain_base / solar_coef

来源
----
gen1  models/_gen1_backup/
gen2  models/_gen2_backup/
gen3  models/_gen3_backup/
gen4  models/（当前）

输出
----
docs/四代结果对比.md
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
MD = os.path.join(PROJ, "models")
OUT_MD = os.path.join(PROJ, "docs", "四代结果对比.md")

GENS = [("gen1", os.path.join(MD, "_gen1_backup"), "标定前"),
        ("gen2", os.path.join(MD, "_gen2_backup"), "首次热参数标定"),
        ("gen3", os.path.join(MD, "_gen3_backup"), "联合标定（占用判定有误）"),
        ("gen4", MD, "修占用 bug + 热模型扩展（定稿）")]

PARAMS = [
    ("ua_coef", ["0.040", "0.115", "0.2072", "0.1383"]),
    ("tau(分钟)", ["375", "130", "72", "108"]),
    ("internal_gain", ["0.02", "0.80", "1.3375", "0.00"]),
    ("gain_base", ["—", "—", "—", "0.76"]),
    ("solar_coef", ["—", "—", "—", "0.23"]),
    ("ac_coef", ["0.30", "0.30", "1.8825", "2.0125"]),
    ("ac_max_cooling", ["1.2", "1.2", "3.1675", "2.1475"]),
    ("temp_outdoor_mean", ["30.0", "22.0", "22.0", "22.0"]),
    ("标定用在室率", ["—", "—", "100%(错)", "67.5%(对)"]),
]


def load(root, rel):
    p = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def g(d, *path, default=None):
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def num(d, *path):
    v = g(d, *path)
    if isinstance(v, dict):
        v = v.get("mean")
    return v


def main():
    L = []
    def P(s=""):
        print(s)
        L.append(s)

    names = [n for n, _, _ in GENS]
    P("# 四代实验结果对比")
    P()
    P(f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P()
    P("## 代次说明")
    P()
    P("| 代次 | 内容 |")
    P("|---|---|")
    for name, _, desc in GENS:
        P(f"| {name} | {desc} |")
    P()
    P("## 一、热工参数")
    P()
    P("| 参数 | " + " | ".join(names) + " |")
    P("|---" * (len(names) + 1) + "|")
    for pname, vals in PARAMS:
        P(f"| `{pname}` | " + " | ".join(vals) + " |")
    P()

    P("## 二、消融实验（5 种子 × 15 回合）")
    P()
    for metric, lab, fmt in [("energy_per_day_kwh", "电量(kWh/天)", "{:.3f}"),
                             ("discomfort_ratio", "不达标率", "{:.2%}")]:
        P(f"**{lab}**")
        P()
        P("| 组别 | " + " | ".join(names) + " |")
        P("|---" * (len(names) + 1) + "|")
        for grp in ["A1 固定权重", "A2 场景化权重(本方法)", "A3 随机变化权重"]:
            cells = []
            for _, root, _ in GENS:
                d = load(root, "ablation_multiseed/results.json")
                v = num(d, "groups", grp, metric) if d else None
                cells.append(fmt.format(v) if v is not None else "-")
            P(f"| {grp} | " + " | ".join(cells) + " |")
        P()

    P("## 三、显式储能实验")
    P()
    P("| 场景 | 指标 | " + " | ".join(names) + " |")
    P("|---" * (len(names) + 2) + "|")
    for k in ["A 规则控制（无储能）", "B 规则+储能(4kWh)",
              "C PPO（无储能）", "D PPO+储能(4kWh)"]:
        for key, lab, fmt in [("cost", "电费", "{:.3f}"),
                              ("discomfort", "不达标", "{:.1%}")]:
            cells = []
            for _, root, _ in GENS:
                d = load(root, "storage/results.json")
                v = g(d, "results", k, key) if d else None
                cells.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | " + " | ".join(cells) + " |")
    P()

    P("## 四、循环网络对比")
    P()
    P("| 策略 | 指标 | " + " | ".join(names) + " |")
    P("|---" * (len(names) + 2) + "|")
    for k in ["B1 规则控制", "B2 普通 PPO", "B3 RecurrentPPO"]:
        for key, lab, fmt in [("energy_cost_yuan", "电费", "{:.3f}"),
                              ("discomfort_ratio", "不达标", "{:.1%}")]:
            cells = []
            for _, root, _ in GENS:
                d = load(root, "recurrent/results.json")
                v = num(d, "results", k, key) if d else None
                cells.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | " + " | ".join(cells) + " |")
    P()

    P("## 五、统一评估基线（无分时电价，20 回合）")
    P()
    P("| 策略 | 指标 | " + " | ".join(names) + " |")
    P("|---" * (len(names) + 2) + "|")
    for k in ["全程开空调25°C", "全程开空调27°C", "规则控制(定温)",
              "规则控制(按时段)", "全程不开空调"]:
        for key, lab, fmt in [("energy_per_day_kwh", "电量", "{:.3f}"),
                              ("discomfort_ratio", "不达标", "{:.1%}")]:
            cells = []
            for _, root, _ in GENS:
                d = load(root, "evaluation/results_default.json")
                v = num(d, "results", k, key) if d else None
                cells.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | " + " | ".join(cells) + " |")
    P()

    P("## 六、帕累托前沿（有分时电价）")
    P()
    P("| 来源 | 指标 | " + " | ".join(names) + " |")
    P("|---" * (len(names) + 2) + "|")
    for who, key in [("规则控制", "rule_front"), ("强化学习", "ppo_front")]:
        for idx, lab in [(0, "不达标率范围"), (1, "电费范围")]:
            cells = []
            for _, root, _ in GENS:
                d = load(root, "pareto/results.json")
                fr = g(d, "有分时电价", key, default=[]) if d else []
                vs = []
                for x in fr:
                    if isinstance(x, (list, tuple)) and len(x) > idx:
                        try:
                            vs.append(float(x[idx]))
                        except (TypeError, ValueError):
                            pass
                if vs:
                    cells.append(f"{min(vs):.1%}~{max(vs):.1%}" if idx == 0
                                 else f"{min(vs):.2f}~{max(vs):.2f}")
                else:
                    cells.append("-")
            P(f"| {who} | {lab} | " + " | ".join(cells) + " |")
    P()

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
