# -*- coding: utf-8 -*-
"""房间热参数辨识（空调关闭后的初始速率法）。

为什么只看"空调刚关闭后的一小段"
--------------------------------
空调在运行时会周期性启停，整段数据是被驱动的，不能用单一指数拟合。
而**空调关闭后的最初几十分钟**是接近纯自由响应的时间窗：

    dT/dt |_{t=0+} = (T_env - T_0) / tau + G

只要窗内 T 变化不大，右端近似常数，因此初始斜率 dT/dt 与
(T_env - T_0) 成正比，比例系数即 1/tau。

做法
----
1. 从事件表取"空调关闭"的时刻
2. 取关闭后的一段窗口，计算线性拟合斜率（摄氏度/分钟）
3. 用窗口内实测室外温度作 T_env、窗口起始温度作 T_0
4. 由 tau = (T_env - T_0) / (dT/dt - G) 反推，G 用"无日照无人"窗口先标定

由于 G 未知，先用**同一房间、同一时段**的两个不同 T_0 的关空调事件
联立求解，可消去 G。
"""

import csv
import io
import math
import os
import statistics
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
RAW = os.path.join(PROJ, "data", "raw")
OUT = os.path.join(PROJ, "docs", "热参数辨识结果.md")

FILES = ["start_2026-10-14.csv", "start_2026-10-15.csv",
         "daily_2026-10-15.csv", "daily_2026-10-16.csv"]

DT_SIM = 15.0


def load():
    rows = []
    for fn in FILES:
        p = os.path.join(RAW, fn)
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(io.open(p, encoding="utf-8")):
            ts = r.get("pc_time", "")
            if len(ts) < 19:
                continue
            try:
                r["_t"] = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            try:
                r["_T"] = float(r["temp_c"])
            except (TypeError, ValueError):
                continue
            try:
                r["_To"] = float(r.get("outdoor_temp_c") or "")
            except (TypeError, ValueError):
                r["_To"] = None
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    return rows


def window_stats(rows, t0, t1):
    """返回窗口的 T0、Tend、时长、线性斜率(度/分)、室外均温、光照均温。"""
    seg = [r for r in rows if t0 <= r["_t"].strftime("%Y-%m-%d %H:%M") < t1]
    if len(seg) < 30:
        return None
    t_base = seg[0]["_t"]
    xs = [(r["_t"] - t_base).total_seconds() / 60.0 for r in seg]
    ys = [r["_T"] for r in seg]
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        return None
    slope = (n * sxy - sx * sy) / den
    Outs = [r["_To"] for r in seg if r["_To"] is not None]
    Ls = []
    for r in seg:
        try:
            Ls.append(float(r["light_lux"]))
        except (TypeError, ValueError):
            pass
    # 相关系数
    mx, my = sx / n, sy / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxv = math.sqrt(sum((x - mx) ** 2 for x in xs))
    syv = math.sqrt(sum((y - my) ** 2 for y in ys))
    r = cov / (sxv * syv) if sxv > 0 and syv > 0 else 0.0
    return {
        "t0": t0, "t1": t1, "n": n,
        "T0": ys[0], "Tend": ys[-1], "Tmean": my,
        "dur": xs[-1], "slope": slope, "r": r,
        "Tout": statistics.mean(Outs) if Outs else None,
        "light": statistics.mean(Ls) if Ls else None,
    }


def main():
    rows = load()
    print(f"载入 {len(rows)} 行")
    print()

    # ---- 空调关闭事件（来自事件记录）----
    OFF_EVENTS = [
        ("10-14 17:29 关空调（无人，门关）", "2026-10-14 17:30", "2026-10-14 17:47"),
        ("10-14 19:54 关空调（无人，开窗）", "2026-10-14 19:57", "2026-10-14 20:20"),
        ("10-15 02:54 关空调（睡眠，无日照）", "2026-10-15 02:56", "2026-10-15 03:30"),
        ("10-15 08:42 关空调（在房，开窗）", "2026-10-15 08:45", "2026-10-15 09:10"),
        ("10-16 13:48 关空调（在房，日照）", "2026-10-16 13:50", "2026-10-16 14:04"),
    ]

    print("=" * 90)
    print("空调关闭后的初始升温段（自由响应窗口）")
    print("=" * 90)
    print(f"  {'窗口':<38}{'时长':>6}{'T0':>7}{'Tend':>7}{'斜率(C/h)':>11}{'r':>7}{'Tout':>7}")
    print("  " + "-" * 84)
    stats = []
    for label, t0, t1 in OFF_EVENTS:
        s = window_stats(rows, t0, t1)
        if not s:
            print(f"  {label:<38}  数据不足")
            continue
        stats.append((label, s))
        to = f"{s['Tout']:.1f}" if s["Tout"] is not None else "-"
        print(f"  {label:<38}{s['dur']:>5.0f}分{s['T0']:>7.2f}{s['Tend']:>7.2f}"
              f"{s['slope']*60:>11.2f}{s['r']:>7.3f}{to:>7}")
    print()

    # ---- 用"同条件、不同起始温度"的组合联立求解 tau 与 G ----
    # 线性模型： dT/dt = (T_out - T)/tau + G
    #   => rate = T_out/tau - T/tau + G = C1 - T/tau    其中 C1 = T_out/tau + G
    # 对多个点做 (T, rate) 回归，斜率即 -1/tau
    pts = []
    for label, s in stats:
        if s["Tout"] is None or abs(s["r"]) < 0.85:
            continue
        pts.append((s["T0"], s["slope"], s["Tout"], label))
    print("=" * 90)
    print("联立求解（仅用线性度高的窗口，|r|>=0.85）")
    print("=" * 90)
    if len(pts) >= 2:
        X = [p[0] for p in pts]
        Y = [p[1] for p in pts]
        m = len(X)
        sx = sum(X); sy = sum(Y)
        sxx = sum(x * x for x in X); sxy = sum(x * y for x, y in zip(X, Y))
        den = m * sxx - sx * sx
        a = (m * sxy - sx * sy) / den
        b = (sy - a * sx) / m
        tau = -1.0 / a if a < 0 else None
        print(f"  参与点 {m} 个：")
        for T0, rate, To, lbl in pts:
            print(f"    {lbl:<38} T0={T0:>6.2f}  rate={rate*60:>+7.2f} C/h  Tout={To:.1f}")
        print()
        print(f"  回归: rate = {a:.6f}*T + {b:.6f}   （rate 单位 C/分）")
        if tau:
            print(f"  >>> tau = {tau:.1f} 分钟")
        else:
            print("  >>> 斜率非负，无法给出 tau")
    else:
        tau = None
        print(f"  线性度高的窗口不足（只有 {len(pts)} 个）")
    print()

    # ---- 制冷能力 ----
    print("=" * 90)
    print("空调制冷能力（实测降温速率）")
    print("=" * 90)
    COOL = [
        ("10-14 16:26~16:42", "2026-10-14 16:26", "2026-10-14 16:42"),
        ("10-14 19:05~19:20", "2026-10-14 19:05", "2026-10-14 19:20"),
        ("10-16 16:52~17:30", "2026-10-16 16:52", "2026-10-16 17:30"),
    ]
    cool = []
    for label, t0, t1 in COOL:
        s = window_stats(rows, t0, t1)
        if not s:
            continue
        rate_h = s["slope"] * 60
        cool.append(rate_h)
        print(f"  {label}  {s['T0']:.2f} -> {s['Tend']:.2f} C  "
              f"{s['dur']:.0f} 分   {rate_h:+.2f} C/小时   r={s['r']:+.3f}")
    if cool:
        print()
        print(f"  最快降温 {min(cool):+.2f} C/小时")
        print(f"  中位      {statistics.median(cool):+.2f} C/小时")
    print()

    md = ["# 热参数辨识结果", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 数据：{len(rows)} 行（10 秒采样，两天实测）", "",
          "## 方法", "",
          "空调运行时周期性启停，整段数据是被驱动的，不能用单一指数拟合；",
          "扫描平衡温度做指数拟合还会落入退化解（平衡温度取很远时指数近似",
          "直线，RMSE 反而更低）。",
          "",
          "因此只取**空调关闭后的最初一段**（接近纯自由响应）计算升温斜率：", "",
          "```",
          "dT/dt = (T_out - T)/tau + G",
          "```",
          "",
          "该式的斜率对 T 的依赖关系为 -1/tau，用多个「同条件、不同起始温度」",
          "的关空调事件联立回归，即可消去未知的热增益 G。", "",
          "## 空调关闭后的初始升温段", "",
          "| 窗口 | 时长 | T0 | Tend | 斜率(C/h) | 相关系数 | 室外温度 |",
          "|---|---|---|---|---|---|---|"]
    for label, s in stats:
        to = f"{s['Tout']:.1f}" if s["Tout"] is not None else "-"
        md.append(f"| {label} | {s['dur']:.0f}分 | {s['T0']:.2f} | {s['Tend']:.2f} | "
                  f"{s['slope']*60:+.2f} | {s['r']:.3f} | {to} |")
    md.append("")
    if tau:
        md.append(f"**联立求解 tau = {tau:.1f} 分钟**")
        md.append("")
        md.append("| 参数 | 旧值 | 新值 |")
        md.append("|---|---|---|")
        md.append(f"| `ua_coef` | 0.04 | **{DT_SIM/tau:.4f}** |")
        md.append(f"| 隐含 tau | {DT_SIM/0.04:.0f} 分 | **{tau:.1f} 分** |")
    md.append("")
    md.append("## 空调制冷能力")
    md.append("")
    for label, t0, t1 in COOL:
        s = window_stats(rows, t0, t1)
        if not s:
            continue
        md.append(f"- {label}: {s['T0']:.2f} -> {s['Tend']:.2f} C，"
                  f"{s['dur']:.0f} 分钟，{s['slope']*60:+.2f} C/小时")
    if cool:
        md.append("")
        md.append(f"最快降温 **{min(cool):+.2f} C/小时**，"
                  f"中位 {statistics.median(cool):+.2f} C/小时")
    md.append("")

    io.open(OUT, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"报告已保存: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
