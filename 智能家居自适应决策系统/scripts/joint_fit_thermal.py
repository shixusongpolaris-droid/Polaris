# -*- coding: utf-8 -*-
"""全时段联合拟合：一次标定 5 个热工/设备参数。

为什么必须联合拟合
------------------
单窗口解析拟合无法确定时间常数 τ：

    窗口        τ(分)
    31 分钟      600
    60 分钟       23
    78 分钟        8
    91 分钟       80 ~ 170
    132 分钟      25

原因是 `(T∞, τ)` 退化——短窗口内温度尚未接近平衡，
数据只提供"初始斜率"这一个信息，无法拆出两个参数。

联合拟合利用的是**整段数据中的多次转折**：
空调 5 次启停、日出日落、人员进出，每一次转折都提供独立约束，
合起来才能把 τ 与 gain 分开。

模型（每 15 分钟一步，显式欧拉）
--------------------------------
    T' = T + Δt·[ −ua·(T − T_out)        # 围护传热
                  + gain·1{有人}          # 内部热源（人、电器）
                  + solar·(光照/1000) ]   # 日照（本次新增）
             − min(ac_coef·max(T − T_set, 0), ac_max)·1{空调开}

待标定参数：ua, gain, solar, ac_coef, ac_max
已知输入：T_out(t)（实测 + 回填）、占用(t)、空调开关(t)、光照(t)、T_set=23

输出
----
docs/全时段联合标定.md
models/joint_fit.json
"""

import csv
import io
import json
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
OUT_MD = os.path.join(PROJ, "docs", "全时段联合标定.md")
OUT_JSON = os.path.join(PROJ, "models", "joint_fit.json")

DT_MIN = 15
T_SET = 23.0

AC_SCHEDULE = [
    ("2026-10-14 16:08:10", "2026-10-14 17:29:43", True),
    ("2026-10-14 18:41:03", "2026-10-14 19:54:44", True),
    ("2026-10-14 20:51:37", "2026-10-15 02:54:00", True),
    ("2026-10-15 05:40:00", "2026-10-15 08:42:51", True),
    ("2026-10-15 21:46:18", "2026-10-16 13:48:00", True),
]


def load_backfill():
    p = os.path.join(PROJ, "data", "processed",
                     "outdoor_temp_backfill.csv")
    out = {}
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    out[r["hour"].replace("T", " ")[:13]] = \
                        float(r["outdoor_temp_c"])
                except (ValueError, KeyError):
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


def build_series():
    """构造 15 分钟等间隔时间序列。"""
    bf = load_backfill()
    occ = load_occ()
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
    pts = []
    for t in sorted(seen):
        r = seen[t]
        try:
            T = float(r["temp_c"])
        except (ValueError, KeyError, TypeError):
            continue
        v = r.get("outdoor_temp_c")
        tout = float(v) if v not in (None, "") else bf.get(t[:13])
        try:
            light = float(r.get("light_lux") or 0.0)
        except (ValueError, TypeError):
            light = 0.0
        pts.append((datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                    T, tout, light))
    if not pts:
        return []
    # 归并到 15 分钟格点（取格内均值）
    grid = {}
    t0 = pts[0][0]
    for (t, T, tout, light) in pts:
        k = int((t - t0).total_seconds() // (DT_MIN * 60))
        g = grid.setdefault(k, {"T": [], "tout": [], "light": [], "t": t})
        g["T"].append(T)
        if tout is not None:
            g["tout"].append(tout)
        g["light"].append(light)
    series = []
    for k in sorted(grid):
        g = grid[k]
        series.append({
            "t": t0 + timedelta(minutes=k * DT_MIN),
            "T": sum(g["T"]) / len(g["T"]),
            "tout": sum(g["tout"]) / len(g["tout"]) if g["tout"] else None,
            "light": sum(g["light"]) / len(g["light"]),
        })
    # 填补缺失的 tout
    last = None
    for s in series:
        if s["tout"] is None:
            s["tout"] = last if last is not None else 22.0
        last = s["tout"]
    # 标注占用与空调，并计算"太阳因子"
    # 太阳因子与仿真 _update_outdoor 完全一致（6:00-18:00 正弦），
    # 这样 solar_coef 在拟合与仿真中口径一致、可直接迁移。
    import math as _m
    for s in series:
        t = s["t"]
        s["occ"] = any(a <= t < b and o for a, b, o in occ)
        s["ac"] = any(datetime.strptime(a, "%Y-%m-%d %H:%M:%S") <= t
                      < datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
                      for a, b, _ in AC_SCHEDULE)
        hf = t.hour + t.minute / 60.0
        s["sun"] = (_m.sin(_m.pi * (hf - 6.0) / 12.0)
                    if 6.0 <= hf <= 18.0 else 0.0)
    return series


def simulate(series, p):
    """按参数 p 前向积分整个序列，返回预测温度数组。"""
    ua, gain, solar, ac_c, ac_m = p
    dt = DT_MIN / 60.0
    pred = [series[0]["T"]]
    for i in range(len(series) - 1):
        s = series[i]
        T = pred[-1]
        dT = -ua * (T - s["tout"]) + (gain if s["occ"] else 0.0) \
            + solar * s["sun"]
        if s["ac"]:
            dT -= min(ac_c * max(T - T_SET, 0.0), ac_m)
        # dT 的单位是 °C/小时，dt 单位为小时，故一步增量为 dT·dt
        pred.append(T + dT * dt)
    return pred


def rmse(series, p):
    if p[0] <= 0 or p[1] < 0 or p[2] < 0 or p[3] < 0 or p[4] <= 0:
        return 1e9
    pred = simulate(series, p)
    se = sum((a["T"] - b) ** 2 for a, b in zip(series, pred))
    return math.sqrt(se / len(series))


def main():
    series = build_series()
    print("=" * 80)
    print(" 全时段联合标定")
    print("=" * 80)
    print(f"  15 分钟步数：{len(series)}"
          f"（约 {len(series)*DT_MIN/60:.1f} 小时）")
    print(f"  覆盖：{series[0]['t']} ~ {series[-1]['t']}")
    print(f"  空调开启步数：{sum(1 for s in series if s['ac'])}")
    print(f"  有人步数：{sum(1 for s in series if s['occ'])}")
    print()

    from src.env.config import SimConfig
    cur = SimConfig()
    print(f"  当前仿真参数：ua={cur.ua_coef}, gain={cur.internal_gain}, "
          f"ac_coef={cur.ac_coef}, ac_max={cur.ac_max_cooling}")
    r_cur = rmse(series, [cur.ua_coef, cur.internal_gain, 0.0,
                          cur.ac_coef, cur.ac_max_cooling])
    print(f"  当前参数的全时段 RMSE = {r_cur:.4f} °C")
    print()

    # 粗搜 + Nelder-Mead
    best = None
    for ua in [0.1, 0.3, 0.6, 1.0, 1.6, 2.4, 3.5, 5.0]:
        for gain in [0.5, 2.0, 5.0, 10.0, 18.0, 30.0]:
            for solar in [0.0, 1.0, 3.0]:
                for ac_c in [1.0, 3.0, 8.0]:
                    for ac_m in [2.0, 6.0]:
                        r = rmse(series, [ua, gain, solar, ac_c, ac_m])
                        if best is None or r < best[0]:
                            best = (r, ua, gain, solar, ac_c, ac_m)
    print(f"  粗搜最优 RMSE = {best[0]:.4f}")
    print(f"    ua={best[1]:.3f} gain={best[2]:.2f} solar={best[3]:.2f} "
          f"ac_coef={best[4]:.2f} ac_max={best[5]:.2f}")

    try:
        from scipy.optimize import minimize
        res = minimize(lambda p: rmse(series, p), list(best[1:]),
                       method="Nelder-Mead",
                       options={"xatol": 1e-4, "fatol": 1e-6,
                                "maxiter": 8000, "maxfev": 20000})
        if res.fun < best[0]:
            best = (float(res.fun), *[float(x) for x in res.x])
    except Exception as e:
        print(f"  细化失败：{e}")

    r, ua, gain, solar, ac_c, ac_m = best
    print()
    print("=" * 80)
    print(" 联合标定结果")
    print("=" * 80)
    print(f"  全时段 RMSE = {r:.4f} °C   （当前参数 {r_cur:.4f}）")
    print(f"    ua_coef        {cur.ua_coef:>8.3f} -> {ua:>8.3f}")
    print(f"    internal_gain  {cur.internal_gain:>8.2f} -> {gain:>8.2f}")
    print(f"    solar_coef     {'(无此项)':>8} -> {solar:>8.2f}")
    print(f"    ac_coef        {cur.ac_coef:>8.2f} -> {ac_c:>8.2f}")
    print(f"    ac_max_cooling {cur.ac_max_cooling:>8.2f} -> {ac_m:>8.2f}")
    print()
    print(f"    tau = {60.0/ua:.1f} 分钟    gain/ua = {gain/ua:.2f} °C")
    print(f"    最大制冷 {ac_m:.2f} °C/步 = {ac_m*4:.1f} °C/小时")

    # 分状态误差
    pred = simulate(series, [ua, gain, solar, ac_c, ac_m])
    for label, sel in [("空调开启段", lambda s: s["ac"]),
                       ("空调关闭段", lambda s: not s["ac"]),
                       ("有人段", lambda s: s["occ"]),
                       ("无人段", lambda s: not s["occ"])]:
        idx = [i for i, s in enumerate(series) if sel(s)]
        if idx:
            e = math.sqrt(sum((series[i]["T"] - pred[i]) ** 2
                              for i in idx) / len(idx))
            print(f"    {label} RMSE = {e:.4f} °C  （{len(idx)} 步）")

    out = {"n_steps": len(series),
           "current": {"ua_coef": cur.ua_coef,
                       "internal_gain": cur.internal_gain,
                       "solar_coef": 0.0,
                       "ac_coef": cur.ac_coef,
                       "ac_max_cooling": cur.ac_max_cooling,
                       "rmse": r_cur},
           "fitted": {"ua_coef": ua, "internal_gain": gain,
                      "solar_coef": solar, "ac_coef": ac_c,
                      "ac_max_cooling": ac_m, "rmse": r,
                      "tau_min": 60.0 / ua, "gain_over_ua": gain / ua}}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 全时段联合标定", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 数据：{len(series)} 个 15 分钟步"
          f"（约 {len(series)*DT_MIN/60:.1f} 小时）", "",
          "## 一、为什么必须联合拟合", "",
          "单窗口解析拟合无法确定 τ，同一段数据换个窗口起止即可得：", "",
          "| 窗口长度 | τ(分钟) |", "|---|---|",
          "| 31 分钟 | 600 |", "| 60 分钟 | 23 |", "| 78 分钟 | 8 |",
          "| 91 分钟 | 80 ~ 170 |", "| 132 分钟 | 25 |", "",
          "原因：`(T∞, τ)` 退化。短窗口内温度尚未接近平衡，",
          "数据只提供\"初始斜率\"= (T∞−T₀)/τ 一个信息，",
          "无法拆出两个参数。", "",
          "联合拟合利用整段数据中的**多次转折**（空调 5 次启停、",
          "日出日落、人员进出），每次转折提供独立约束，",
          "合起来才能把 τ 与 gain 分开。", "",
          "## 二、模型", "",
          "```",
          "T' = T + Δt·[ −ua·(T − T_out(t))",
          "              + gain·1{有人}(t)",
          "              + solar·(光照(t)/1000) ]",
          "         − min(ac_coef·max(T − T_set, 0), ac_max)·1{空调开}",
          "```", "",
          f"空调设定温度取实测报告的 **{T_SET} °C**；Δt = {DT_MIN} 分钟。", "",
          "## 三、结果", "",
          "| 参数 | 当前仿真 | **联合标定** |",
          "|---|---|---|",
          f"| `ua_coef` | {cur.ua_coef} | **{ua:.3f}** |",
          f"| `internal_gain` | {cur.internal_gain} | **{gain:.2f}** |",
          f"| `solar_coef` | 无此项 | **{solar:.2f}** |",
          f"| `ac_coef` | {cur.ac_coef} | **{ac_c:.2f}** |",
          f"| `ac_max_cooling` | {cur.ac_max_cooling} | **{ac_m:.2f}** |",
          "",
          f"全时段 RMSE：**{r:.4f} °C**（当前参数为 {r_cur:.4f} °C）", "",
          f"隐含 τ = **{60.0/ua:.1f} 分钟**；`gain/ua` = {gain/ua:.2f} °C；",
          f"最大制冷 {ac_m:.2f} °C/步 = {ac_m*4:.1f} °C/小时。", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
