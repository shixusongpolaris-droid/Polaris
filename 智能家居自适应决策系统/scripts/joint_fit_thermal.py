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
    ("2026-10-16 16:50:44", "2026-10-16 17:59:33", True),
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
            segs.append((s, e, str(r.get("state", "")).strip().startswith("在房间")))
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
    """按参数 p 前向积分整个序列，返回预测温度数组。

    参数 p = (ua, gain_base, gain_occ, solar, ac_c, ac_m)

        dT/dt = -ua·(T − T_out)          围护传热
                + gain_base              基础得热（楼板/邻户传热、电器待机、
                                         墙体蓄热释放），与是否有人无关
                + gain_occ·1{有人}        人员活动得热
                + solar·太阳因子          日照
                − min(ac_coef·max(T−T_set,0), ac_max)·1{空调开}

    为什么把 gain 拆成 base 与 occ
    ------------------------------
    初版只有一个 gain 且乘以"有人"指示。当时 occupancy 因字符串匹配缺陷
    （"在房间" in "不在房间" 为真）被误判为 100%，该 gain 实际充当了
    "常开热源"，恰好补偿了模型缺失的基础得热，因此拟合看似很好
    （RMSE 1.728，tau=72 分钟）。

    修正占用判定（真实在室率 68%）后，同一模型必须靠"极慢响应
    （tau=706 分钟）+ 极大增益（gain/ua=22.9 °C）"硬凑，
    RMSE 反而升到 1.896，且参数在物理上不可能。

    把基础得热显式建模后，两个热源各司其职，参数可解释性显著改善。
    """
    ua, g_base, g_occ, solar, ac_c, ac_m = p
    dt = DT_MIN / 60.0
    pred = [series[0]["T"]]
    for i in range(len(series) - 1):
        s = series[i]
        T = pred[-1]
        dT = -ua * (T - s["tout"]) + g_base \
            + (g_occ if s["occ"] else 0.0) + solar * s["sun"]
        if s["ac"]:
            dT -= min(ac_c * max(T - T_SET, 0.0), ac_m)
        # dT 单位为 °C/小时，dt 单位为小时
        pred.append(T + dT * dt)
    return pred


def rmse(series, p):
    if p[0] <= 0 or any(x < 0 for x in p[1:]) or p[5] <= 0:
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
    print(f"  当前仿真参数：ua={cur.ua_coef}, gain_base={cur.gain_base}, "
          f"gain_occ={cur.internal_gain}, solar={cur.solar_coef}, "
          f"ac_coef={cur.ac_coef}, ac_max={cur.ac_max_cooling}"
          f"（均为每步 15 分钟量）")
    # ⚠️ 单位换算（2026-10-17 修正，由团队复核成员 fix-reviewer 指出）：
    # SimConfig 里的热参数是【每步(15 分钟)】量（simulator 中 dT 直接加到
    # 温度上、不乘 dt）；而本脚本 simulate() 的模型按【每小时】速率工作，
    # 最后乘 dt=0.25 小时。因此把 config 的值直接喂进 rmse() 会让动力学
    # 慢 4 倍，得到的"当前参数 RMSE"严重偏大，使拟合改善被夸大成
    # "4.6138 -> 1.7790（-61%）"，而真实的同口径比较只差 0.17%。
    # 修正：统一乘 4 换算到每小时，并补齐 gain_base 与 solar_coef 两项
    # （旧代码把这两项置 0，本身也与 gen4 之后的模型不符）。
    r_cur = rmse(series, [cur.ua_coef * 4.0,
                          cur.gain_base * 4.0,
                          cur.internal_gain * 4.0,
                          cur.solar_coef * 4.0,
                          cur.ac_coef * 4.0,
                          cur.ac_max_cooling * 4.0])
    print(f"  当前参数的全时段 RMSE = {r_cur:.4f} °C"
          f"（已换算到每小时口径，与拟合值可比）")
    print()

    # 粗搜 + Nelder-Mead
    # 参数 = (ua, gain_base, gain_occ, solar, ac_c, ac_m)
    best = None
    for ua in [0.1, 0.3, 0.6, 1.0, 1.6, 2.4, 3.5]:
        for g_base in [0.0, 1.0, 3.0, 6.0, 10.0, 16.0, 24.0]:
            for g_occ in [0.0, 1.0, 3.0, 6.0]:
                for solar in [0.0, 1.0, 3.0]:
                    for ac_c in [1.0, 3.0, 8.0]:
                        for ac_m in [2.0, 8.0]:
                            r = rmse(series, [ua, g_base, g_occ, solar,
                                              ac_c, ac_m])
                            if best is None or r < best[0]:
                                best = (r, ua, g_base, g_occ, solar,
                                        ac_c, ac_m)
    print(f"  粗搜最优 RMSE = {best[0]:.4f}")
    print(f"    ua={best[1]:.3f} gain_base={best[2]:.2f} "
          f"gain_occ={best[3]:.2f} solar={best[4]:.2f} "
          f"ac_coef={best[5]:.2f} ac_max={best[6]:.2f}")

    try:
        from scipy.optimize import minimize
        res = minimize(lambda p: rmse(series, p), list(best[1:]),
                       method="Nelder-Mead",
                       options={"xatol": 1e-4, "fatol": 1e-6,
                                "maxiter": 12000, "maxfev": 30000})
        if res.fun < best[0]:
            best = (float(res.fun), *[float(x) for x in res.x])
    except Exception as e:
        print(f"  细化失败：{e}")

    r, ua, g_base, g_occ, solar, ac_c, ac_m = best
    print()
    print("=" * 80)
    print(" 联合标定结果")
    print("=" * 80)
    print(f"  全时段 RMSE = {r:.4f} °C   （当前参数 {r_cur:.4f}）")
    print(f"    ua_coef        {cur.ua_coef:>8.3f} -> {ua:>8.3f}"
          f"   (tau = {60.0/ua:.0f} 分钟)")
    print(f"    gain_base      {'(无此项)':>8} -> {g_base:>8.2f} °C/小时")
    print(f"    gain_occ       {cur.internal_gain:>8.2f} -> {g_occ:>8.2f} °C/小时")
    print(f"    solar_coef     {'(无此项)':>8} -> {solar:>8.2f}")
    print(f"    ac_coef        {cur.ac_coef:>8.2f} -> {ac_c:>8.2f}")
    print(f"    ac_max_cooling {cur.ac_max_cooling:>8.2f} -> {ac_m:>8.2f}")
    print()
    print(f"    (gain_base + gain_occ)/ua = {(g_base+g_occ)/ua:.2f} °C "
          f"  <- 有人时的稳态室内外温差")
    print(f"    gain_base/ua              = {g_base/ua:.2f} °C "
          f"  <- 无人时的稳态室内外温差")
    # 单位说明（2026-10-17 修正，由团队复核成员 fix-reviewer 指出）：
    # ac_m 是拟合出的【每小时】速率（simulate 内部按每小时工作），
    # 原代码把它标成"°C/步"再乘 4 得"°C/小时"，单位恰好反了。
    print(f"    最大制冷 {ac_m/4:.4f} °C/步 = {ac_m:.2f} °C/小时")

    # 分状态误差
    pred = simulate(series, [ua, g_base, g_occ, solar, ac_c, ac_m])
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
                       "gain_base": 0.0,
                       "gain_occ": cur.internal_gain,
                       "solar_coef": 0.0,
                       "ac_coef": cur.ac_coef,
                       "ac_max_cooling": cur.ac_max_cooling,
                       "rmse": r_cur},
           "fitted": {"ua_coef": ua, "gain_base": g_base,
                      "gain_occ": g_occ,
                      "solar_coef": solar, "ac_coef": ac_c,
                      "ac_max_cooling": ac_m, "rmse": r,
                      "tau_min": 60.0 / ua,
                      "offset_occupied": (g_base + g_occ) / ua,
                      "offset_empty": g_base / ua}}
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
          "dT/dt = −ua·(T − T_out(t))            围护传热",
          "        + gain_base                    基础得热（楼板/邻户传热、",
          "                                       电器待机、墙体蓄热释放）",
          "        + gain_occ·1{有人}(t)          人员活动得热",
          "        + solar·太阳因子(t)             日照",
          "        − min(ac_coef·max(T−T_set,0), ac_max)·1{空调开}",
          "```", "",
          f"空调设定温度取实测报告的 **{T_SET} °C**；Δt = {DT_MIN} 分钟。", "",
          "**为什么把 gain 拆成两部分**：初版只有一个 gain 且乘以"
          "\"有人\"指示。",
          "当 occupancy 因字符串匹配缺陷（`\"在房间\" in \"不在房间\"` 为真）",
          "被误判为 100% 时，该 gain 实际充当\"常开热源\"，恰好补偿了模型",
          "缺失的基础得热，因此拟合看似很好（RMSE 1.728、τ=72 分钟）。",
          "修正占用判定（真实在室率 68%）后，同一模型必须靠\"极慢响应",
          "（τ=706 分钟）+ 极大增益（gain/ua=22.9 °C）\"硬凑，",
          "RMSE 反而升到 1.896，且参数在物理上不可能。",
          "把基础得热显式建模后两个热源各司其职。", "",
          "## 三、结果", "",
          "| 参数 | 折当前仿真 | **联合标定** |",
          "|---|---|---|",
          f"| `ua_coef` | {cur.ua_coef} | **{ua:.4f}**（τ = {60.0/ua:.0f} 分钟） |",
          f"| `gain_base` | 无此项 | **{g_base:.2f}** °C/小时 |",
          f"| `gain_occ` | {cur.internal_gain}（全按有人计） | **{g_occ:.2f}** °C/小时 |",
          f"| `solar_coef` | 无此项 | **{solar:.2f}** |",
          f"| `ac_coef` | {cur.ac_coef} | **{ac_c:.2f}** |",
          f"| `ac_max_cooling` | {cur.ac_max_cooling} | **{ac_m:.2f}** |",
          "",
          f"全时段 RMSE：**{r:.4f} °C**（当前参数为 {r_cur:.4f} °C）", "",
          f"有人时稳态室内外温差 = (gain_base+gain_occ)/ua = "
          f"**{(g_base+g_occ)/ua:.2f} °C**；",
          f"无人时 = gain_base/ua = **{g_base/ua:.2f} °C**；",
          f"最大制冷 {ac_m/4:.4f} °C/步 = {ac_m:.2f} °C/小时。", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
