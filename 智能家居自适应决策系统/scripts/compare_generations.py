# -*- coding: utf-8 -*-
"""三代实验结果对比：标定前 / 第一次重标定 / 联合标定。

三代的口径
----------
gen1  标定前（项目原始参数）
      ua_coef=0.040, internal_gain=0.02, temp_outdoor_mean=30
      来源：models/_gen1_backup/（从 git 提交 cd32e71 恢复）

gen2  第一次重标定（单窗口解析拟合）
      ua_coef=0.115, internal_gain=0.80, temp_outdoor_mean=22
      来源：models/_gen2_backup/

gen3  全时段联合标定（本次，200 步 / 50 小时 / 5 参数）
      ua_coef=0.2072（tau=72.4 分钟）, internal_gain=1.3375,
      ac_coef=1.8825, ac_max_cooling=3.1675
      来源：models/（当前）

输出
----
docs/三代结果对比.md
models/gen_comparison.json
"""

import json
import math
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
MD = os.path.join(PROJ, "models")
G1 = os.path.join(MD, "_gen1_backup")
G2 = os.path.join(MD, "_gen2_backup")
OUT_MD = os.path.join(PROJ, "docs", "三代结果对比.md")
OUT_JSON = os.path.join(MD, "gen_comparison.json")

G3 = os.path.join(MD, "_gen3_backup")
GENS = [("gen1 标定前", G1), ("gen2 首次重标定", G2),
        ("gen3 联合标定", G3), ("gen4 定稿", MD)]


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
    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("# 三代实验结果对比")
    P()
    P(f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P()
    P("## 三代的热工参数")
    P()
    P("| 参数 | gen1 | gen2 | gen3 | **gen4 定稿** |")
    P("|---|---|---|---|")
    P("| `ua_coef` | 0.040 | 0.115 | **0.2072** |")
    P("| 隐含 τ | 375 分钟 | 130 分钟 | **72.4 分钟** |")
    P("| `internal_gain` | 0.02 | 0.80 | **1.3375** |")
    P("| `ac_coef` | 0.30 | 0.30 | **1.8825** |")
    P("| `ac_max_cooling` | 1.2 | 1.2 | **3.1675** |")
    P("| `temp_outdoor_mean` | 30.0 | 22.0 | 22.0 |")
    P("| 空调能否维持 25°C | — | ❌ 平衡 26.10°C | ✅ 平衡 25.34°C |")
    P()
    P("**gen3 的依据**：全时段联合标定（200 个 15 分钟步、50 小时、")
    P("同时利用空调 5 次启停与日出日落等多次转折），全时段 RMSE 由")
    P("2.381 °C 降至 1.728 °C；τ 由\"室内光照\"与\"太阳因子\"两种")
    P("日照驱动方式分别标定均得 69~72 分钟，且与最早的独立窗口估计吻合。")
    P()

    impact = {}

    # ---------- 1. 消融 ----------
    P("## 一、消融实验（5 种子 × 15 回合 = 75 样本/组）")
    P()
    P("| 组别 | 指标 | gen1 | gen2 | **gen3** |")
    P("|---|---|---|---|---|")
    for grp in ["A1 固定权重", "A2 场景化权重(本方法)", "A3 随机变化权重"]:
        for key, lab, fmt in [("energy_per_day_kwh", "电量(kWh/天)", "{:.3f}"),
                              ("discomfort_ratio", "不达标率", "{:.2%}")]:
            vals = []
            for name, root in GENS:
                d = load(root, "ablation_multiseed/results.json")
                v = num(d, "groups", grp, key) if d else None
                vals.append(fmt.format(v) if v is not None else "-")
            P(f"| {grp} | {lab} | {vals[0]} | {vals[1]} | **{vals[2]}** |")
        P("| | | | | |")
    P()

    # gen3 的显著性
    d3 = load(MD, "ablation_multiseed/results.json")
    if d3:
        P("**gen3 组间显著性**")
        P()
        P("| 对比 | 指标 | 差值 | 相对 | t | 判定 |")
        P("|---|---|---|---|---|---|")
        for a, b in [("A2 场景化权重(本方法)", "A1 固定权重"),
                     ("A2 场景化权重(本方法)", "A3 随机变化权重")]:
            for key, lab in [("energy_per_day_kwh", "电量"),
                             ("discomfort_ratio", "不达标")]:
                va = g(d3, "groups", a, key, default={})
                vb = g(d3, "groups", b, key, default={})
                if not va or not vb:
                    continue
                dd = va["mean"] - vb["mean"]
                se = math.sqrt(va["ci95"] ** 2 + vb["ci95"] ** 2) / 1.96
                t = dd / se if se else 0
                rel = dd / vb["mean"] if vb["mean"] else 0
                P(f"| {a[:2]} vs {b[:2]} | {lab} | {dd:+.4f} | {rel:+.2%} | "
                  f"{t:+.2f} | {'**显著**' if abs(t) > 1.96 else '不显著'} |")
        P()

    # ---------- 2. 储能 ----------
    P("## 二、显式储能实验")
    P()
    P("| 场景 | 指标 | gen1 | gen2 | **gen3** |")
    P("|---|---|---|---|---|")
    for k in ["A 规则控制（无储能）", "B 规则+储能(4kWh)",
              "C PPO（无储能）", "D PPO+储能(4kWh)"]:
        for key, lab, fmt in [("cost", "电费(元/天)", "{:.3f}"),
                              ("discomfort", "不达标率", "{:.1%}")]:
            vals = []
            for name, root in GENS:
                d = load(root, "storage/results.json")
                v = g(d, "results", k, key) if d else None
                vals.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | {vals[0]} | {vals[1]} | **{vals[2]}** |")
        P("| | | | | |")
    # 储能收益
    d3s = load(MD, "storage/results.json")
    if d3s:
        a = g(d3s, "results", "A 规则控制（无储能）", "cost")
        b = g(d3s, "results", "B 规则+储能(4kWh)", "cost")
        c = g(d3s, "results", "C PPO（无储能）", "cost")
        e = g(d3s, "results", "D PPO+储能(4kWh)", "cost")
        if None not in (a, b, c, e):
            P(f"**gen3 储能收益**：规则 {(a-b)/a:+.1%}；"
              f"PPO {(c-e)/c:+.1%}")
            P()

    # ---------- 3. 循环网络 ----------
    P("## 三、循环网络对比")
    P()
    P("| 策略 | 指标 | gen1 | gen2 | **gen3** |")
    P("|---|---|---|---|---|")
    for k in ["B1 规则控制", "B2 普通 PPO", "B3 RecurrentPPO"]:
        for key, lab, fmt in [("energy_cost_yuan", "电费(元/天)", "{:.3f}"),
                              ("discomfort_ratio", "不达标率", "{:.1%}")]:
            vals = []
            for name, root in GENS:
                d = load(root, "recurrent/results.json")
                v = num(d, "results", k, key) if d else None
                vals.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | {vals[0]} | {vals[1]} | **{vals[2]}** |")
        P("| | | | | |")
    P()

    # ---------- 4. 帕累托 ----------
    P("## 四、帕累托前沿（有分时电价场景）")
    P()
    P("| 来源 | 指标 | gen1 | gen2 | **gen3** |")
    P("|---|---|---|---|---|")
    for who, key in [("规则控制", "rule_front"), ("强化学习", "ppo_front")]:
        for idx, lab in [(0, "不达标率范围"), (1, "电费范围(元/天)")]:
            vals = []
            for name, root in GENS:
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
                    vals.append(f"{min(vs):.1%}~{max(vs):.1%}" if idx == 0
                                else f"{min(vs):.3f}~{max(vs):.3f}")
                else:
                    vals.append("-")
            P(f"| {who} | {lab} | {vals[0]} | {vals[1]} | **{vals[2]}** |")
    P()

    # ---------- 5. 基线 ----------
    P("## 五、统一评估基线（无分时电价，20 回合）")
    P()
    P("| 策略 | 指标 | gen1 | gen2 | **gen3** |")
    P("|---|---|---|---|---|")
    for k in ["全程开空调25°C", "全程开空调27°C", "规则控制(定温)",
              "规则控制(按时段)", "全程不开空调"]:
        for key, lab, fmt in [("energy_per_day_kwh", "电量(kWh/天)", "{:.3f}"),
                              ("discomfort_ratio", "不达标率", "{:.1%}")]:
            vals = []
            for name, root in GENS:
                # 注意：results_v2_notou.json 是历史文件、不被重跑覆盖，
                # 因此必须优先读本次重跑生成的 results_default.json，
                # 否则会把各代都显示成同一份陈旧数据。
                d = load(root, "evaluation/results_default.json") or \
                    load(root, "evaluation/results_v2_notou.json")
                v = num(d, "results", k, key) if d else None
                vals.append(fmt.format(v) if v is not None else "-")
            P(f"| {k} | {lab} | {vals[0]} | {vals[1]} | **{vals[2]}** |")
        P("| | | | | |")
    P()

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"generations": [n for n, _ in GENS]}, f,
                  ensure_ascii=False, indent=2)
    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
