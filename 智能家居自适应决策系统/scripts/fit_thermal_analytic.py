# -*- coding: utf-8 -*-
"""用解析法拟合房间热工曲线（稳健版）。

为什么不用网格/数值优化
--------------------
前一次尝试用网格 + Nelder-Mead 拟合 `dT/dt = -ua·(T-T_out) + gain·1{有人}`，
结果参数严重发散：τ 从 6.3 到 26 分钟，且各窗口差异达数倍，
与直接观测的 72~130 分钟矛盾。原因是 ua 与 gain 通过
T_∞ = T_out + gain/ua 强耦合，多组 (ua, gain) 对同一条缓变曲线
给出相近残差，优化器会滑向 τ→0、gain→∞ 的退化方向。

改用解析法
----------
一阶模型在"T_out 与占用状态近似不变"的窗口内有解析解：

    T(t) = T_∞ + (T₀ − T_∞)·exp(−(t−t₀)/τ)

    · 无人在室： gain = 0      ->  T_∞ 应等于 T_out
    · 有人在室： gain > 0      ->  T_∞ = T_out + gain/ua = T_out + gain·τ/60

因此对每个窗口，用最小二乘拟合 (T_∞, τ) 两个参数即可，
τ 直接可读、无耦合退化问题。ua_coef = 60/τ（单位 1/小时）。
"""

import csv
import io
import json
import math
import os
import sys
from datetime import datetime
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)
OUT_JSON = os.path.join(PROJ, "models", "thermal_fit_analytic.json")


def load_backfill():
    """加载回填的室外温度（逐小时，用于补实测缺失的时段）。

    来源与校验见 data/processed/outdoor_temp_backfill_说明.md：
    平均绝对偏差约 2 °C，故用回填值的窗口必须在报告中标注。
    """
    p = os.path.join(PROJ, "data", "processed",
                     "outdoor_temp_backfill.csv")
    out = {}
    if not os.path.exists(p):
        return out
    with io.open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[r["hour"].replace("T", " ")[:13]] = \
                    float(r["outdoor_temp_c"])
            except (ValueError, KeyError):
                continue
    return out


BACKFILL = {}


def load_data():
    global BACKFILL
    BACKFILL = load_backfill()
    raw = os.path.join(PROJ, "data", "raw")
    seen = {}
    for f in ["start_2026-10-14.csv", "start_2026-10-15.csv",
              "daily_2026-10-15.csv", "daily_2026-10-16.csv"]:
        p = os.path.join(raw, f)
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("pc_time"):
                        seen[r["pc_time"]] = r
    out = []
    for t in sorted(seen):
        r = seen[t]
        try:
            v = r.get("outdoor_temp_c")
            tout = float(v) if v not in (None, "") else None
            src = "实测"
            if tout is None and BACKFILL:
                tout = BACKFILL.get(t[:13])
                src = "回填" if tout is not None else "缺失"
            out.append({
                "t": datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                "T": float(r["temp_c"]),
                "Tout": tout,
                "Tout_src": src,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return out


def load_occ():
    p = os.path.join(PROJ, "data", "processed", "timeline_final.csv")
    segs = []
    with io.open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                s = datetime.strptime(r["start"][:19], "%Y-%m-%d %H:%M:%S")
                e = datetime.strptime(r["end"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            segs.append((s, e, "在房间" in str(r.get("state", ""))))
    return segs


def occ_frac(segs, t0, t1):
    """窗口内"有人"的时间占比。"""
    tot = (t1 - t0).total_seconds()
    if tot <= 0:
        return 0.0
    occ = 0.0
    for s, e, o in segs:
        a, b = max(s, t0), min(e, t1)
        if b > a and o:
            occ += (b - a).total_seconds()
    return occ / tot


# ---- 需要拟合的窗口：(起, 止, 空调状态, 备注) ----
WINDOWS = [
    ("2026-10-14 17:29:43", "2026-10-14 18:41:03", "off", "傍晚"),
    ("2026-10-14 19:54:44", "2026-10-14 20:51:37", "off", "开窗实验"),
    ("2026-10-15 02:54:00", "2026-10-15 05:40:00", "off", "夜间"),
    ("2026-10-15 08:42:51", "2026-10-15 21:46:18", "off", "白天有日照"),
    ("2026-10-16 13:48:00", "2026-10-16 16:00:00", "off", "下午有日照"),
    ("2026-10-14 16:08:10", "2026-10-14 17:29:43", "on", "设定23"),
    ("2026-10-14 18:41:03", "2026-10-14 19:54:44", "on", "设定23"),
    ("2026-10-14 20:51:37", "2026-10-15 02:54:00", "on", "夜间"),
    ("2026-10-15 05:40:00", "2026-10-15 08:42:51", "on", "清晨"),
    ("2026-10-15 21:46:18", "2026-10-16 13:48:00", "on", "通宵+上午"),
]


def fit_exp(pts, t0):
    """拟合 T(t) = Tinf + (T0 - Tinf)exp(-(t-t0)/tau)。

    pts: [(分钟偏移, T)]
    返回 (Tinf, tau分钟, rmse, 用于比较的常数模型 rmse)
    """
    if len(pts) < 20:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    def rmse_for(Tinf, tau):
        se = 0.0
        for x, y in zip(xs, ys):
            pred = Tinf + (ys[0] - Tinf) * math.exp(-x / tau)
            se += (pred - y) ** 2
        return math.sqrt(se / len(xs))

    best = None
    # Tinf 覆盖观测范围上下各 15 度；tau 覆盖 5~600 分钟
    for Tinf in [min(ys) - 15 + i * 0.5 for i in range(int((max(ys) - min(ys) + 30) / 0.5) + 1)]:
        for tau in [5 + i * 5 for i in range(120)]:
            r = rmse_for(Tinf, tau)
            if best is None or r < best[0]:
                best = (r, Tinf, tau)
    # 局部细化
    r0, Ti0, ta0 = best
    for Tinf in [Ti0 - 0.5 + i * 0.05 for i in range(21)]:
        for tau in [ta0 - 5 + i for i in range(11)]:
            if tau <= 0:
                continue
            r = rmse_for(Tinf, tau)
            if r < best[0]:
                best = (r, Tinf, tau)
    # 常数模型（只有 Tinf）作为对照
    const = math.sqrt(sum((y - mean(ys)) ** 2 for y in ys) / len(ys))
    return best[1], best[2], best[0], const


def main():
    data = load_data()
    occ = load_occ()
    by_t = {r["t"]: r for r in data}

    print("=" * 90)
    print(" 房间热工曲线 —— 解析法拟合")
    print("=" * 90)
    print(f"  实测样本 {len(data)} 条")
    print()

    rows = []
    for a, b, st, note in WINDOWS:
        t0 = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        sub = [r for r in data if t0 <= r["t"] <= t1 and r["T"] is not None]
        if len(sub) < 25:
            continue
        pts = [((r["t"] - t0).total_seconds() / 60.0, r["T"]) for r in sub]
        res = fit_exp(pts, t0)
        if res is None:
            continue
        Tinf, tau, rmse, const = res
        Touts = [r["Tout"] for r in sub if r["Tout"] is not None]
        Tout_m = mean(Touts) if Touts else None
        of = occ_frac(occ, t0, t1)
        ua = 60.0 / tau
        gain = (Tinf - Tout_m) * ua if Tout_m is not None and of > 0.5 else None
        Tobs0, Tobs1 = sub[0]["T"], sub[-1]["T"]
        rows.append({"label": f"{a[5:]} -> {b[5:]}", "ac": st, "note": note,
                     "minutes": (t1 - t0).total_seconds() / 60.0,
                     "T0": Tobs0, "T1": Tobs1, "Tinf": Tinf, "tau": tau,
                     "rmse": rmse, "rmse_const": const,
                     "Tout": Tout_m, "occ_frac": of,
                     "ua": ua, "gain": gain})

    print(f"  {'窗口':<30}{'空调':<5}{'分':>5}{'T始':>7}{'T终':>7}"
          f"{'T∞':>8}{'τ(分)':>9}{'ua':>8}{'RMSE':>8}{'常数RMSE':>10}")
    print("  " + "-" * 86)
    for r in rows:
        acs = "开" if r["ac"] == "on" else "关"
        print(f"  {r['label']:<30}{acs:<5}{r['minutes']:>5.0f}{r['T0']:>7.2f}"
              f"{r['T1']:>7.2f}{r['Tinf']:>8.2f}{r['tau']:>9.1f}{r['ua']:>8.3f}"
              f"{r['rmse']:>8.3f}{r['rmse_const']:>10.3f}")

    # 汇总：空调关闭且无日照的窗口
    clean = [r for r in rows if r["ac"] == "off" and "日照" not in r["note"]
             and "开窗" not in r["note"]]
    print()
    print("=" * 90)
    print(" 汇总：空调关闭且无日照的窗口（用于基础热参数）")
    print("=" * 90)
    if clean:
        for r in clean:
            gtxt = f"{r['gain']:.2f}" if r["gain"] is not None else "-"
            ttxt = f"{r['Tout']:.2f}" if r["Tout"] is not None else "-"
            octxt = "有人" if r["occ_frac"] > 0.5 else "无人"
            print(f"  {r['label']:<30}  τ = {r['tau']:>6.1f} 分   "
                  f"ua = {r['ua']:.3f}/小时   T∞ = {r['Tinf']:.2f} °C   "
                  f"T_out = {ttxt}   {octxt}({r['occ_frac']:.0%})"
                  f"   gain = {gtxt}")
        taus = [r["tau"] for r in clean]
        uas = [r["ua"] for r in clean]
        print()
        print(f"  τ 范围：{min(taus):.1f} ~ {max(taus):.1f} 分钟"
              f"（中位 {sorted(taus)[len(taus)//2]:.1f}）")
        print(f"  ua 范围：{min(uas):.3f} ~ {max(uas):.3f} /小时"
              f"（中位 {sorted(uas)[len(uas)//2]:.3f}）")
        gains = [r["gain"] for r in clean if r["gain"] is not None]
        if gains:
            print(f"  gain 范围：{min(gains):.2f} ~ {max(gains):.2f} °C/小时")
    print()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
