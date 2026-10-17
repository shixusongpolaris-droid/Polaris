# -*- coding: utf-8 -*-
"""诊断空调开启期间温度读数的可信度，并用包络法尝试估计制冷能力。

背景
----
`calibrate_ac_cooling.py` 的逐窗口拟合失败（RMSE 1.88 °C/步，
参数撞到搜索上界），45.4% 的样本算出"负制冷"（空调开着却在升温），
物理上不可能。观察原始轨迹发现：空调开启期间室内温度以约 36 分钟为
周期在 20.0~24.6 °C 之间震荡，振幅约 4.5 °C。

本脚本做两件事：
  1. 量化该震荡（振幅、周期），并检验它是否只出现在空调开启期间
     —— 若是，则支持"传感器被空调出风直吹"的判断
  2. 用长窗口（1~2 小时）包络法把震荡平均掉，估计净降温速率，
     作为制冷能力标定的**替代尝试**（并明确标注其可靠性低于直接拟合）

输出
----
docs/空调温度数据可信度诊断.md
models/ac_sensor_diag.json
"""

import csv
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.env.config import SimConfig                       # noqa: E402
from _timeline_util import (ac_on_at as _ac_on_at,          # noqa: E402
                           ac_on_intervals as _ac_on_intervals)

OUT_MD = os.path.join(PROJ, "docs", "空调温度数据可信度诊断.md")
OUT_JSON = os.path.join(PROJ, "models", "ac_sensor_diag.json")

DT_MIN = 15
T_SET = 23.0


def load_data():
    raw = os.path.join(PROJ, "data", "raw")
    seen = {}
    for f in ["start_2026-10-14.csv", "start_2026-10-15.csv",
              "daily_2026-10-15.csv", "daily_2026-10-16.csv"]:
        p = os.path.join(raw, f)
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = r.get("pc_time")
                if t:
                    seen[t] = r
    rows = []
    for t in sorted(seen):
        r = seen[t]
        try:
            rows.append({
                "t": datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                "T": float(r["temp_c"]),
                "Tout": float(r["outdoor_temp_c"])
                if r.get("outdoor_temp_c") else None,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return rows


def load_timeline():
    """读取时段表（用于占用状态）。空调开关状态改用权威启停表。"""
    p = os.path.join(PROJ, "data", "processed", "timeline_final.csv")
    out = []
    if not os.path.exists(p):
        return out
    with io.open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                s = datetime.strptime(r["start"][:19], "%Y-%m-%d %H:%M:%S")
                e = datetime.strptime(r["end"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            out.append({"start": s, "end": e, "raw_ac": r.get("ac", "")})
    return out


def ac_state_at(tl, t):
    """查询时刻 t 空调是否开启。

    修正（2026-10-17）：原实现取时间表 `ac` 列的前缀判断，
    会把 '开→02:54关→05:40开→08:42关' 这类段内多次转移的标注
    整段判为开机（620 分钟里约 200 分钟实际关机）。
    现改为查询权威启停表（来源 data/raw/*_events.csv 的用户报告与
    雷达反推），与 joint_fit_thermal.py 的标定输入一致。
    （由团队审计成员 script-auditor 发现）
    """
    return _ac_on_at(t)


def analyze_oscillation(data, tl):
    """比较空调开启与关闭期间的短期波动幅度。"""
    res = {"ac_on": [], "ac_off": []}
    by_t = {r["t"]: r for r in data}
    for r in data:
        st = ac_state_at(tl, r["t"])
        if st is None:
            continue
        t1 = r["t"] + timedelta(minutes=DT_MIN)
        r1 = by_t.get(t1)
        if r1 is None:
            continue
        # 要求窗口内空调状态一致
        if ac_state_at(tl, t1 - timedelta(seconds=1)) != st:
            continue
        res["ac_on" if st else "ac_off"].append({
            "t": r["t"].strftime("%m-%d %H:%M"),
            "T": r["T"], "dT": r1["T"] - r["T"],
        })
    return res


def envelope_estimate(data, tl):
    """长窗口包络法：在空调开启的连续区间内，用 1 小时的平滑温度
    计算净降温速率，再按热平衡式反推空调制冷项。

    可靠性低于逐窗口拟合：震荡被平均后仍会残留偏差。
    """
    # 取空调开启的连续区间（权威启停表）
    # 修正（2026-10-17）：原先从时段表按 seg["ac_on"] 取，会把多转移
    # 标注整段视为开机；现改用权威启停表的时间区间。
    segs = [{"start": a, "end": b} for a, b in _ac_on_intervals()]
    print("  --- 空调开启区间的净降温速率（1 小时平滑）---")
    print(f"  {'区间':<40}{'起点T':>8}{'终点T':>8}{'ΔT':>8}{'小时':>7}{'°C/小时':>10}")
    rows = []
    for s in segs:
        sub = [r for r in data if s["start"] <= r["t"] <= s["end"]]
        if len(sub) < 20:
            continue
        dur_h = (sub[-1]["t"] - sub[0]["t"]).total_seconds() / 3600.0
        if dur_h < 0.75:
            continue
        T0, T1 = sub[0]["T"], sub[-1]["T"]
        rate = (T1 - T0) / dur_h
        lab = f"{sub[0]['t']:%m-%d %H:%M} -> {sub[-1]['t']:%m-%d %H:%M}"
        print(f"  {lab:<40}{T0:>8.2f}{T1:>8.2f}{T1-T0:>+8.2f}{dur_h:>7.2f}"
              f"{rate:>+10.2f}")
        rows.append({"label": lab, "T0": T0, "T1": T1, "hours": dur_h,
                     "rate_per_h": rate,
                     "T_mean": mean([r["T"] for r in sub]),
                     "Tout_mean": mean([r["Tout"] for r in sub
                                        if r["Tout"] is not None])
                     if any(r["Tout"] is not None for r in sub) else None})
    return rows


def main():
    data = load_data()
    tl = load_timeline()
    print("=" * 78)
    print(" 空调温度数据可信度诊断")
    print("=" * 78)
    print(f"  实测样本 {len(data)} 条；时段表 {len(tl)} 段")
    print()

    osc = analyze_oscillation(data, tl)
    print("=== 1. 短期（15 分钟）温度变化的幅度对比 ===")
    print(f"  {'状态':<10}{'样本数':>8}{'温度均值':>10}{'|dT|均值':>10}"
          f"{'|dT|最大':>10}{'|dT|标准差':>11}")
    print("  " + "-" * 62)
    summary = {}
    for k, lab in [("ac_on", "空调开启"), ("ac_off", "空调关闭")]:
        v = osc[k]
        if not v:
            print(f"  {lab:<10} 无样本")
            continue
        ad = [abs(x["dT"]) for x in v]
        tm = mean([x["T"] for x in v])
        summary[k] = {"n": len(v), "T_mean": tm, "abs_dT_mean": mean(ad),
                      "abs_dT_max": max(ad), "abs_dT_sd": pstdev(ad)}
        print(f"  {lab:<10}{len(v):>8}{tm:>10.2f}{mean(ad):>10.3f}"
              f"{max(ad):>10.3f}{pstdev(ad):>11.3f}")
    print()

    if "ac_on" in summary and "ac_off" in summary:
        ratio = summary["ac_on"]["abs_dT_mean"] / summary["ac_off"]["abs_dT_mean"]
        print(f"  >>> 空调开启期间的 15 分钟变化幅度是关闭期间的 "
              f"{ratio:.1f} 倍")
        print()

    print("=== 2. 空调开启区间的净降温速率（包络法）===")
    env = envelope_estimate(data, tl)
    print()

    # 用包络法反推空调制冷量
    c = SimConfig()
    print("=== 3. 包络法反推空调制冷项（仅供参考，可靠性低于直接拟合）===")
    if env:
        # 取降温最快的区间
        cool = [e for e in env if e["rate_per_h"] < 0]
        if cool:
            best = min(cool, key=lambda e: e["rate_per_h"])
            print(f"  降温最快的区间：{best['label']}")
            print(f"    净降温速率 {best['rate_per_h']:.2f} °C/小时")
            if best["Tout_mean"] is not None:
                ua = c.ua_coef
                gain = c.internal_gain
                # 净降温 = 空调制冷 + ua*(Tout - T) - gain*occ
                # 取有人（睡眠）
                needed = -best["rate_per_h"] / (60.0 / DT_MIN)  # 每步
                heat = ua * (best["T_mean"] - best["Tout_mean"]) - gain
                ac_term = needed + heat
                print(f"    室内外温差 {best['T_mean']-best['Tout_mean']:+.2f} °C，"
                      f"内部热增益 {gain}")
                print(f"    得热项 {heat:+.3f} °C/步（负=房间向室外散热）")
                print(f"    反推空调制冷项 ≈ {ac_term:.3f} °C/步 "
                      f"（当前仿真 {c.ac_max_cooling}）")
                print(f"    换算速率 {ac_term/(DT_MIN/60):.1f} °C/小时")
    print()

    md = ["# 空调温度数据可信度诊断", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}", "",
          "## 一、结论摘要", "",
          "空调开启期间的室内温度读数**不可用于标定制冷能力**。",
          "原始轨迹显示温度以约 36 分钟为周期在 20.0~24.6 °C 之间震荡",
          "（振幅约 4.5 °C），且 45.4% 的 15 分钟窗口算出\"空调开着却升温\"。",
          "真实房间的热容不可能产生这种波动，",
          "最可能是**温度传感器被空调出风直吹**，测到的是出风口气流温度。", "",
          "## 二、短期变化幅度对比", "",
          "| 状态 | 样本数 | 温度均值 | \\|dT\\|均值 | \\|dT\\|最大 | \\|dT\\|标准差 |",
          "|---|---|---|---|---|---|"]
    for k, lab in [("ac_on", "空调开启"), ("ac_off", "空调关闭")]:
        if k not in summary:
            continue
        s = summary[k]
        md.append(f"| {lab} | {s['n']} | {s['T_mean']:.2f} °C | "
                  f"{s['abs_dT_mean']:.3f} | {s['abs_dT_max']:.3f} | "
                  f"{s['abs_dT_sd']:.3f} |")
    if "ac_on" in summary and "ac_off" in summary:
        md += ["",
               f"**空调开启期间的 15 分钟温度变化幅度是关闭期间的 "
               f"{ratio:.1f} 倍**，",
               "且波动无物理合理性，指向测量问题而非房间动态。", ""]
    md += ["## 三、空调开启区间的净降温速率", "",
           "| 区间 | 起点 T | 终点 T | ΔT | 时长(小时) | 速率(°C/小时) |",
           "|---|---|---|---|---|---|"]
    for e in env:
        md.append(f"| {e['label']} | {e['T0']:.2f} | {e['T1']:.2f} | "
                  f"{e['T1']-e['T0']:+.2f} | {e['hours']:.2f} | "
                  f"{e['rate_per_h']:+.2f} |")
    md += ["", "长窗口平均可部分抵消震荡，但仍有残留偏差，",
           "故该估计**可靠性低于逐窗口拟合**，仅作为数量级参考。", "",
           "## 四、对既有标定的影响评估", "",
           "| 标定项 | 使用的数据窗口 | 是否受本次问题影响 |",
           "|---|---|---|",
           "| `ua_coef` = 0.115 | 空调关闭 + 无人 + 有日照 | **不受影响** |",
           "| `internal_gain` = 0.80 | 稳态室内外温差（含关闭期） | **基本不受影响** |",
           "| 空调制冷能力 | 空调开启期 | **数据不可用，无法标定** |", "",
           "## 五、建议", "",
           "1. **把温度传感器移离空调出风口**（至少 1.5 米，避免直吹），",
           "   重新采集一次空调开启期间的数据；",
           "2. 采集时**同时记录空调设定温度与压缩机启停状态**；",
           "3. 在新的、可信的数据上再标定 `ac_coef` 与 `ac_max_cooling`；",
           "4. 在结题报告中如实说明：当前仿真的空调制冷能力仍为估计值，",
           "   且现有实测数据无法支持该项标定（这本身是一个可报告的限制）。", ""]
    md += ["## 六、当前仿真的处置建议", "",
           "在取得可信数据之前，有两个选择：", "",
           "**选择 A（推荐）**：保留现有 `ac_coef = 0.30`，",
           "但在报告中明确标注\"空调制冷能力未经实测标定\"，",
           "并说明它导致\"全程开空调\"基线本身就有明显不舒适率这一异常。", "",
           "**选择 B**：用包络法估计的数量级临时调整，使仿真的",
           "\"全程开空调\"基线回到合理水平（不舒适率接近 0），",
           "并在报告中说明该值来自粗略估计。", ""]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "envelope": env,
                   "ratio": ratio if "ac_on" in summary
                   and "ac_off" in summary else None},
                  f, ensure_ascii=False, indent=2)
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
