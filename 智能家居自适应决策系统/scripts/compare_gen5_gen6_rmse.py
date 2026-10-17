# -*- coding: utf-8 -*-
"""同口径比较 gen5 与 gen6 参数的全时段 RMSE。

背景
----
`joint_fit_thermal.py` 原先用 config 的【每步】值直接喂进按【每小时】
工作的 simulate()，动力学慢 4 倍，得出"当前参数 RMSE 4.6138"这一
虚高基线，使拟合改善被夸大。

本脚本用修正后的口径（每步值 × 4 = 每小时），在同一段实测序列上
分别计算 gen5 与 gen6 参数的全时段 RMSE，并给出分状态误差。
"""

import io
import math
import os
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "scripts"))

DT_MIN = 15
T_SET = 23.0

# 空调启停（6 段，权威）
from _timeline_util import ac_on_at, AC_ON_PERIODS  # noqa: E402

# gen5 与 gen6 的参数（每步量）
GEN5 = dict(ua=0.1383, gain_base=0.76, gain_occ=0.0,
            solar=0.23, ac_coef=2.0125, ac_max=2.1475)
GEN6 = dict(ua=0.15175, gain_base=0.8925, gain_occ=0.0,
            solar=0.215, ac_coef=1.83, ac_max=2.1175)


def build_series():
    import csv
    bf = {}
    p = os.path.join(PROJ, "data", "processed", "outdoor_temp_backfill.csv")
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    bf[r["hour"].replace("T", " ")[:13]] = \
                        float(r["outdoor_temp_c"])
                except (ValueError, KeyError):
                    continue
    occ_segs = []
    p = os.path.join(PROJ, "data", "processed", "timeline_final.csv")
    with io.open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                s = datetime.strptime(r["start"][:19], "%Y-%m-%d %H:%M:%S")
                e = datetime.strptime(r["end"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            occ_segs.append((s, e,
                             str(r.get("state", "")).strip()
                             .startswith("在房间")))
    raw = os.path.join(PROJ, "data", "raw")
    seen = {}
    for f in ["start_2026-10-14.csv", "start_2026-10-15.csv",
              "daily_2026-10-15.csv", "daily_2026-10-16.csv"]:
        fp = os.path.join(raw, f)
        if os.path.exists(fp):
            with io.open(fp, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("pc_time"):
                        seen[r["pc_time"]] = r
    pts = []
    for t in sorted(seen):
        r = seen[t]
        try:
            T = float(r["temp_c"])
        except (ValueError, KeyError, TypeError):
            continue
        v = r.get("outdoor_temp_c")
        tout = float(v) if v not in (None, "") else bf.get(t[:13])
        pts.append((datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"), T, tout))
    t0 = pts[0][0]
    grid = {}
    for (t, T, tout) in pts:
        k = int((t - t0).total_seconds() // (DT_MIN * 60))
        g = grid.setdefault(k, {"T": [], "tout": []})
        g["T"].append(T)
        if tout is not None:
            g["tout"].append(tout)
    series = []
    for k in sorted(grid):
        g = grid[k]
        tk = t0 + timedelta(minutes=k * DT_MIN)
        series.append({
            "t": tk, "T": sum(g["T"]) / len(g["T"]),
            "tout": (sum(g["tout"]) / len(g["tout"])) if g["tout"] else None,
            "occ": any(a <= tk < b and o for a, b, o in occ_segs),
            "ac": ac_on_at(tk),
        })
    last = None
    for s in series:
        if s["tout"] is None:
            s["tout"] = last if last is not None else 22.0
        last = s["tout"]
    for s in series:
        hf = s["t"].hour + s["t"].minute / 60.0
        s["sun"] = (math.sin(math.pi * (hf - 6.0) / 12.0)
                    if 6.0 <= hf <= 18.0 else 0.0)
    return series


def simulate(series, p):
    """每小时口径的前向积分，最后乘 dt=0.25 小时。"""
    dt = DT_MIN / 60.0
    pred = [series[0]["T"]]
    for i in range(len(series) - 1):
        s = series[i]
        T = pred[-1]
        dT = (-p["ua"] * (T - s["tout"]) + p["gain_base"]
              + (p["gain_occ"] if s["occ"] else 0.0)
              + p["solar"] * s["sun"])
        if s["ac"]:
            dT -= min(p["ac_coef"] * max(T - T_SET, 0.0), p["ac_max"])
        pred.append(T + dT * dt)
    return pred


def rmse_of(series, p):
    # p 是每步量，换算到每小时
    ph = {k: v * 4.0 for k, v in p.items()}
    pred = simulate(series, ph)
    se = sum((a["T"] - b) ** 2 for a, b in zip(series, pred))
    return math.sqrt(se / len(series)), pred


def main():
    series = build_series()
    print("=" * 84)
    print(" 同口径比较 gen5 与 gen6 的全时段 RMSE")
    print("=" * 84)
    print(f"  实测序列 {len(series)} 步（{len(series)*DT_MIN/60:.1f} 小时）")
    print(f"  空调开启 {sum(1 for s in series if s['ac'])} 步；"
          f"有人 {sum(1 for s in series if s['occ'])} 步")
    print()
    print(f"  {'参数组':<8}{'全时段':>9}{'空调开':>9}{'空调关':>9}"
          f"{'有人':>9}{'无人':>9}")
    print("  " + "-" * 54)
    for name, p in [("gen5", GEN5), ("gen6", GEN6)]:
        r, pred = rmse_of(series, p)

        def sub(sel):
            idx = [i for i, s in enumerate(series) if sel(s)]
            if not idx:
                return float("nan")
            return math.sqrt(sum((series[i]["T"] - pred[i]) ** 2
                                 for i in idx) / len(idx))
        print(f"  {name:<8}{r:>9.4f}{sub(lambda s: s['ac']):>9.4f}"
              f"{sub(lambda s: not s['ac']):>9.4f}"
              f"{sub(lambda s: s['occ']):>9.4f}"
              f"{sub(lambda s: not s['occ']):>9.4f}")
    r5, _ = rmse_of(series, GEN5)
    r6, _ = rmse_of(series, GEN6)
    print()
    print(f"  gen6 相对 gen5：RMSE {r5:.4f} -> {r6:.4f}"
          f"（{(r6-r5)/r5*100:+.2f}%）")
    print()
    print("  ⚠️ 说明：两者差距极小，说明这 6 个参数在该数据上**可辨识性差**——")
    print("     参数变动 1.4~17.4% 只换来 0.2% 的拟合改善。因此 gen6 的意义")
    print("     不在于「拟合更好」，而在于 **用了更完整的空调启停数据**；")
    print("     两代结论一致，本身就是一次参数不确定性下的稳健性检验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
