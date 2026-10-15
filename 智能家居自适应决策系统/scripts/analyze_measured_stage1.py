# -*- coding: utf-8 -*-
"""初步实测数据分析（阶段一）。

目标：用已采集的实测数据做第一大轮分析，回答三个问题：
  1. 数据资产盘点：有哪些可用数据、质量如何
  2. 热模型标定：用"空调关闭 + 无日照干扰"的干净窗口拟合房间热响应
  3. 与仿真默认参数对比：实测 τ 与仿真假设的差距

只读分析，不修改原始数据。
输出：控制台报告 + docs/初步实测分析_阶段一.md
"""

import csv
import io
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = Path(r"D:\Dev\Projects\Polaris\智能家居自适应决策系统")
RAW = PROJECT / "data" / "raw"
OUT_MD = PROJECT / "docs" / "初步实测分析_阶段一.md"

# 需要分析的数据文件（按时间顺序）
FILES = [
    "start_2026-10-14.csv",
    "start_2026-10-15.csv",
    "daily_2026-10-15.csv",
]


def load(fname):
    p = RAW / fname
    if not p.exists():
        return []
    rows = list(csv.DictReader(io.open(p, encoding="utf-8")))
    for r in rows:
        try:
            r["_t"] = datetime.strptime(r["pc_time"][:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            r["_t"] = None
    return [r for r in rows if r["_t"] is not None]


def fnum(r, k):
    try:
        return float(r[k])
    except (TypeError, ValueError, KeyError):
        return None


def segment(rows, t0, t1):
    """按 'HH:MM:SS' 取时段（跨文件时按日期时间排序后用时刻匹配）。"""
    out = []
    for r in rows:
        hm = r["_t"].strftime("%H:%M:%S")
        if t0 <= hm <= t1:
            out.append(r)
    return out


def fit_first_order(pairs, tinf_grid=None):
    """一阶模型 T(t)=Tinf-(Tinf-T0)exp(-t/tau) 拟合。

    pairs: [(分钟, 温度), ...]
    返回 dict（含 valid 标记）或 None。

    可靠拟合的两个必要条件（否则指数模型欠约束，会给出
    物理上荒谬的 T_inf，例如把 20 分钟近似直线的升温外推成 45 C）：
      · 时长 >= MIN_MINUTES（曲线要有明显弯曲）
      · 温度变化 >= MIN_SPAN（信噪比要够）
    """
    MIN_MINUTES = 60.0      # 最短拟合时长（分钟）
    MIN_SPAN = 3.0          # 最小温度变化幅度（摄氏度）

    if len(pairs) < 30:
        return None
    Ts = [p[1] for p in pairs]
    span = max(Ts) - min(Ts)
    duration = pairs[-1][0] - pairs[0][0]
    if span < 0.5:
        return None          # 变化太小，拟合无意义

    # 在"观测最高温"之上搜索平衡温度
    if tinf_grid is None:
        tinf_grid = [max(Ts) + 0.1 * i for i in range(1, 200)]

    best = None
    for tinf in tinf_grid:
        pts = [(x, tinf - T) for x, T in pairs if tinf - T > 0.05]
        if len(pts) < 25:
            continue
        n = len(pts)
        sx = sum(p[0] for p in pts)
        sy = sum(math.log(p[1]) for p in pts)
        sxx = sum(p[0] ** 2 for p in pts)
        sxy = sum(p[0] * math.log(p[1]) for p in pts)
        den = n * sxx - sx * sx
        if den == 0:
            continue
        slope = (n * sxy - sx * sy) / den
        if slope >= 0:
            continue
        inter = (sy - slope * sx) / n
        tau = -1.0 / slope
        A = math.exp(inter)
        se = sum((T - (tinf - A * math.exp(-x / tau))) ** 2 for x, T in pairs)
        rmse = math.sqrt(se / len(pairs))
        if best is None or rmse < best["rmse"]:
            best = {"tinf": tinf, "tau": tau, "A": A, "rmse": rmse,
                    "n": len(pts), "span": span, "duration": duration,
                    "valid": True, "reason": ""}

    if best is None:
        return None

    # 有效性判据
    reasons = []
    if best["duration"] < MIN_MINUTES:
        reasons.append(f"时长不足({best['duration']:.0f}<{MIN_MINUTES:.0f}分钟)")
    if best["span"] < MIN_SPAN:
        reasons.append(f"温差不足({best['span']:.1f}<{MIN_SPAN:.1f}C)")
    if reasons:
        best["valid"] = False
        best["reason"] = "、".join(reasons)
    return best


def main():
    all_rows = []
    for f in FILES:
        r = load(f)
        all_rows += r
    all_rows.sort(key=lambda x: x["_t"])
    if not all_rows:
        print("没有数据")
        return 1

    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("=" * 74)
    P(" 初步实测数据分析（阶段一）")
    P("=" * 74)
    P(f" 分析时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    P(f" 数据总量: {len(all_rows)} 行")
    P(f" 时间跨度: {all_rows[0]['_t']:%Y-%m-%d %H:%M} ~ {all_rows[-1]['_t']:%Y-%m-%d %H:%M}")
    P()

    # ---------------- 1. 数据资产盘点 ----------------
    P("─" * 74)
    P("1. 数据资产盘点")
    P("─" * 74)
    P(f"  {'文件':<30}{'行数':>7}{'起始':>10}{'结束':>10}{'缺失温度':>9}")
    for f in FILES:
        rows = load(f)
        if not rows:
            P(f"  {f:<30}{'不存在':>7}")
            continue
        miss = sum(1 for r in rows if fnum(r, "temp_c") is None)
        P(f"  {f:<30}{len(rows):>7}{rows[0]['_t']:%H:%M:%S>10}"
          f"{rows[-1]['_t']:%H:%M:%S>10}{miss:>9}")
    P()

    # 采样间隔
    ts = [r["_t"] for r in all_rows]
    gaps = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
    gaps = [g for g in gaps if 0 < g < 7200]
    P(f"  采样间隔: 最小 {min(gaps):.0f}s  最大 {max(gaps):.0f}s  "
      f"中位 {statistics.median(gaps):.0f}s")
    P(f"  正常(<=11s)占比: {100*sum(1 for g in gaps if g <= 11)/len(gaps):.1f}%")
    P()

    # ---------------- 2. 温区/湿区/光区统计 ----------------
    P("─" * 74)
    P("2. 环境参数范围")
    P("─" * 74)
    for key, name, unit in [("temp_c", "室内温度", "C"),
                            ("humidity_pct", "室内湿度", "%"),
                            ("light_lux", "光照", "lx"),
                            ("outdoor_temp_c", "室外温度", "C")]:
        v = [fnum(r, key) for r in all_rows]
        v = [x for x in v if x is not None]
        if v:
            P(f"  {name:<10}{min(v):>8.1f} ~ {max(v):<8.1f}{unit}"
              f"   (有效 {len(v)}/{len(all_rows)})")
    P()

    # ---------------- 3. 干净窗口识别与标定 ----------------
    P("─" * 74)
    P("3. 热模型标定（用空调关闭 + 环境稳定的窗口）")
    P("─" * 74)
    P()

    # 窗口定义：(名称, 日期, 起, 止, 备注)
    WINDOWS = [
        ("夜间被动升温（睡眠）", "2026-10-15", "02:54:00", "05:40:00",
         "空调定时关机，无人活动，无日照"),
        ("上午被动升温（有人）", "2026-10-15", "08:20:00", "08:37:00",
         "空调压缩机停机，窗帘未开，短暂窗口"),
        ("白天被动升温（无人）", "2026-10-15", "09:30:00", "12:00:00",
         "用户外出，密闭房间，有日照"),
        ("门开趋于客厅温度", "2026-10-15", "21:24:00", "21:45:00",
         "房门打开，房间向客厅温度平衡"),
    ]

    results = []
    for name, day, t0, t1, note in WINDOWS:
        seg = [r for r in all_rows
               if r["_t"].strftime("%Y-%m-%d") == day and t0 <= r["_t"].strftime("%H:%M:%S") <= t1]
        if len(seg) < 30:
            P(f"  [{name}] 样本不足（{len(seg)}），跳过")
            P()
            continue
        base = seg[0]["_t"]
        pairs = []
        for r in seg:
            T = fnum(r, "temp_c")
            if T is not None:
                pairs.append(((r["_t"] - base).total_seconds() / 60.0, T))
        fit = fit_first_order(pairs)
        Ts = [p[1] for p in pairs]
        P(f"  【{name}】  {note}")
        P(f"    时段 {t0}~{t1}   样本 {len(pairs)}")
        P(f"    温度 {Ts[0]:.2f} -> {Ts[-1]:.2f} C  "
          f"(变化 {Ts[-1]-Ts[0]:+.2f})  范围 {min(Ts):.2f}~{max(Ts):.2f}")
        if fit:
            tag = "✅ 可靠" if fit.get("valid") else f"⚠️ 不可靠（{fit.get('reason')}）"
            P(f"    拟合: T_inf={fit['tinf']:.2f} C   tau={fit['tau']:.1f} 分钟  "
              f"RMSE={fit['rmse']:.3f} C   {tag}")
            if fit.get("valid"):
                results.append((name, fit, Ts))
            else:
                P(f"    -> 该结果不参与标定（实测温度 {min(Ts):.1f}~{max(Ts):.1f} C，"
                  f"历时 {fit['duration']:.0f} 分钟）")
        else:
            P("    拟合失败（变化过小或样本不足）")
        P()

    # ---------------- 4. 与仿真默认参数对比 ----------------
    P("─" * 74)
    P("4. 与仿真默认参数对比")
    P("─" * 74)
    try:
        sys.path.insert(0, str(PROJECT / "src"))
        from env.config import SimConfig
        cfg = SimConfig()
        sim = {k: getattr(cfg, k) for k in dir(cfg)
               if not k.startswith("_") and isinstance(getattr(cfg, k), (int, float))}
        P("  仿真配置中可能相关的参数：")
        for k, v in sorted(sim.items()):
            if any(s in k.lower() for s in
                   ("temp", "ua", "capac", "tau", "thermal", "heat", "ac_", "cool")):
                P(f"    {k} = {v}")
    except Exception as e:
        P(f"  （无法读取仿真配置: {type(e).__name__}: {e}）")
    P()

    if results:
        taus = [f["tau"] for _, f, _ in results]
        tinf = [f["tinf"] for _, f, _ in results]
        P("  实测时间常数汇总：")
        for (n, f, _) in results:
            P(f"    {n:<24} tau = {f['tau']:>7.1f} 分钟   T_inf = {f['tinf']:>6.2f} C")
        P(f"    平均 tau = {statistics.mean(taus):.1f} 分钟")
    P()

    P("=" * 74)
    P("（阶段一分析结束）")
    P("=" * 74)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("```\n" + "\n".join(lines) + "\n```\n")
    print()
    print(f"报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
