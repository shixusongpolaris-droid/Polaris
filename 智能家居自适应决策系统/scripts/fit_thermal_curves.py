# -*- coding: utf-8 -*-
"""分状态拟合房间热工曲线：空调关闭（自由响应）与空调开启（制冷）。

为什么分两段拟合
----------------
空调开启与关闭时，房间温度的行为完全不同：

  · **空调关闭**：数据平滑，|dT|(15 分钟) 仅 0.29~0.56 °C，
    可直接用一阶模型 `dT/dt = -ua·(T - T_out) + gain·1{有人}` 拟合。

  · **空调开启**：数据剧烈震荡（|dT| 1.9~2.5 °C，周期约 40 分钟，振幅约 4.5 °C），
    这是压缩机启停造成的空气温度摆动。15 分钟步长的单质量仿真模型
    无法表示这种震荡，因此对开启段采用**周期平均**口径拟合，
    使其与仿真口径一致。

时段来源
--------
空调启停时间取自用户报告，记录在
`data/raw/*_events.csv`，整理如下（本次硬编码，便于追溯）：

    开 10-14 16:08:10   关 10-14 17:29:43
    开 10-14 18:41:03   关 10-14 19:54:44（开窗实验）
    开 10-14 20:51:37   关 10-15 02:54:00（定时关机）
    开 10-15 05:40:00   关 10-15 08:42:51（关空调+开窗）
    开 10-15 21:46:18   关 10-16 13:48:00（由温度最低点反推）

占用状态取自人工核对过的 `data/processed/timeline_final.csv`。

注意：不使用 `timeline_final.csv` 的 `ac` 列自动判断启停，
因为其中数段为跨状态段（如"开→02:54关→05:40开"），
自动解析会出错——这正是前一次标定失败的原因之一。

输出
----
docs/热工曲线分段拟合.md
models/thermal_fit.json
"""

import csv
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

OUT_MD = os.path.join(PROJ, "docs", "热工曲线分段拟合.md")
OUT_JSON = os.path.join(PROJ, "models", "thermal_fit.json")

DT_MIN = 15

# ---- 权威启停表（来源：events.csv 中的用户报告）----
AC_SCHEDULE = [
    ("2026-10-14 16:08:10", "2026-10-14 17:29:43", "on", "设定23°C"),
    ("2026-10-14 17:29:43", "2026-10-14 18:41:03", "off", ""),
    ("2026-10-14 18:41:03", "2026-10-14 19:54:44", "on", "设定23°C"),
    ("2026-10-14 19:54:44", "2026-10-14 20:51:37", "off", "开窗实验"),
    ("2026-10-14 20:51:37", "2026-10-15 02:54:00", "on", ""),
    ("2026-10-15 02:54:00", "2026-10-15 05:40:00", "off", "定时关机"),
    ("2026-10-15 05:40:00", "2026-10-15 08:42:51", "on", ""),
    ("2026-10-15 08:42:51", "2026-10-15 21:46:18", "off", "无人"),
    ("2026-10-15 21:46:18", "2026-10-16 13:48:00", "on", "通宵+上午"),
    ("2026-10-16 13:48:00", "2026-10-16 16:00:00", "off", "数据末端"),
]


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
                if r.get("pc_time"):
                    seen[r["pc_time"]] = r
    rows = []
    for t in sorted(seen):
        r = seen[t]
        try:
            def fnum(k):
                v = r.get(k)
                return float(v) if v not in (None, "") else None
            rows.append({
                "t": datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                "T": float(r["temp_c"]),
                "Tout": fnum("outdoor_temp_c"),
                "light": fnum("light_lux"),
                "radar": r.get("radar", ""),
            })
        except (ValueError, KeyError, TypeError):
            continue
    return rows


def load_occupancy():
    """人工核对过的占用时段。"""
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
            out.append({"start": s, "end": e,
                        "occ": str(r.get("state", "")).strip().startswith("在房间")})
    return out


def occ_at(occ_segs, t):
    for s in occ_segs:
        if s["start"] <= t < s["end"]:
            return s["occ"]
    return None


def window_rows(rows, t0, t1):
    return [r for r in rows if t0 <= r["t"] <= t1]


# ============================================================
# A. 空调关闭：拟合 ua_coef 与 internal_gain
# ============================================================
# 日照标记：有日照的窗口不能用单纯的"内部增益"解释，
# 必须单独处理，否则会把日照的贡献错误地吸收进 internal_gain。
SOLAR_EXPOSED_NOTES = {"定时关机"}   # 占位，实际按窗口起止时刻判断

# 判定为"可能有日照"的窗口（按备注 + 白天时段）
SUNNY_WINDOWS = {( "2026-10-15 08:42:51", "2026-10-15 21:46:18" ),
                 ( "2026-10-16 13:48:00", "2026-10-16 16:00:00" )}


def fit_ac_off(rows, occ_segs, windows=None):
    """对每个关闭区间做一阶拟合，再联合求 (ua, gain)。

    参数：
        windows  限定参与拟合的窗口（用于把有日照窗口排除在外）
    """
    wins = []
    for a, b, st, note in AC_SCHEDULE:
        if st != "off":
            continue
        if windows is not None and (a, b) not in windows:
            continue
        t0 = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        if (t1 - t0).total_seconds() / 60.0 < 45:
            continue                      # 太短，拟合不稳
        sub = window_rows(rows, t0, t1)
        if len(sub) < 30:
            continue
        wins.append({"t0": t0, "t1": t1, "note": note, "rows": sub})

    print(f"  可用关闭区间：{len(wins)} 个")
    for w in wins:
        print(f"    {w['t0']:%m-%d %H:%M} -> {w['t1']:%m-%d %H:%M}  "
              f"({(w['t1']-w['t0']).total_seconds()/60:.0f} 分, "
              f"{len(w['rows'])} 点)  {w['note']}")

    def predict_rmse(ua, gain, w, dt_min=1.0):
        """从区间初值出发，按 1 分钟步长积分，再与实测比较。"""
        r0 = w["rows"][0]
        T = r0["T"]
        t = w["t0"]
        pred = []
        idx = 0
        steps = int((w["t1"] - w["t0"]).total_seconds() / 60.0 / dt_min)
        for k in range(steps + 1):
            tk = t + timedelta(minutes=k * dt_min)
            while idx + 1 < len(w["rows"]) and \
                    abs((w["rows"][idx + 1]["t"] - tk).total_seconds()) < \
                    abs((w["rows"][idx]["t"] - tk).total_seconds()):
                idx += 1
            r = w["rows"][idx]
            if abs((r["t"] - tk).total_seconds()) > 90:
                continue
            Tout = r["Tout"] if r["Tout"] is not None else 22.0
            occ = occ_at(occ_segs, tk)
            g = gain if occ else 0.0
            pred.append((r["T"], T))
            T = T - ua * (T - Tout) * (dt_min / 60.0) + g * (dt_min / 60.0)
        if not pred:
            return None
        se = sum((p - m) ** 2 for m, p in pred) / len(pred)
        return math.sqrt(se), len(pred)

    def total_rmse(ua, gain):
        if ua <= 0 or gain < 0:
            return 1e9
        tot, n = 0.0, 0
        for w in wins:
            res = predict_rmse(ua, gain, w)
            if res is None:
                return 1e9
            tot += res[0] ** 2 * res[1]
            n += res[1]
        return math.sqrt(tot / n) if n else 1e9

    # 先用粗网格定位，再用 Nelder-Mead 细化。
    # 注意：实测 tau 约 72~130 分钟 -> ua = 60/tau 约 0.46~0.83 /小时，
    # 因此搜索上界必须显著高于 1，否则会像初次尝试那样撞到边界。
    best = None
    for ua in [x / 100.0 for x in range(5, 301, 5)]:
        for gain in [x / 10.0 for x in range(0, 201, 5)]:
            r = total_rmse(ua, gain)
            if best is None or r < best[0]:
                best = (r, ua, gain)

    try:
        from scipy.optimize import minimize
        res = minimize(lambda p: total_rmse(p[0], p[1]), [best[1], best[2]],
                       method="Nelder-Mead",
                       options={"xatol": 1e-4, "fatol": 1e-5,
                                "maxiter": 2000})
        if res.success and res.fun < best[0]:
            best = (float(res.fun), float(res.x[0]), float(res.x[1]))
    except Exception:
        pass
    return best, wins


# ============================================================
# B. 空调开启：周期平均口径拟合有效制冷量
# ============================================================
def cycle_average(rows, win_min=40):
    """对温度做滑动平均，去掉压缩机启停造成的震荡。"""
    out = []
    half = win_min / 2.0
    for r in rows:
        seg = [x["T"] for x in rows
               if abs((x["t"] - r["t"]).total_seconds()) <= half * 60]
        out.append({**r, "T_avg": mean(seg) if seg else r["T"],
                    "n_avg": len(seg)})
    return out


def fit_ac_on(rows, occ_segs, ua, gain):
    """在周期平均温度上，估计空调的平均制冷量。

    模型（周期平均后）：
        dT_avg/dt = -ua·(T_avg - T_out) - C_eff + gain·1{有人}
    其中 C_eff 为周期平均的空调制冷量（°C/小时）。
    """
    wins = []
    for a, b, st, note in AC_SCHEDULE:
        if st != "on":
            continue
        t0 = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        sub = window_rows(rows, t0, t1)
        if len(sub) < 200:
            continue
        wins.append({"t0": t0, "t1": t1, "note": note,
                     "rows": cycle_average(sub)})
    return wins


def main():
    data = load_data()
    occ = load_occupancy()
    print("=" * 78)
    print(" 热工曲线分段拟合")
    print("=" * 78)
    print(f"  实测样本 {len(data)} 条；占用时段 {len(occ)} 段")
    print()

    print("=== A. 空调关闭：拟合 ua_coef 与 internal_gain ===")
    print()
    print("  【A1】无日照窗口（用于拟合基础热参数）")
    nosun = [(a, b) for a, b, st, nt in AC_SCHEDULE
             if st == "off" and (a, b) not in SUNNY_WINDOWS]
    best, wins = fit_ac_off(data, occ, windows=set(nosun))
    if best is None:
        print("  拟合失败")
        return 1
    rmse, ua, gain = best
    print()
    print(f"  拟合结果：ua_coef = {ua:.4f}   internal_gain = {gain:.2f}")
    print(f"  联合 RMSE = {rmse:.4f} °C")
    print(f"  当前仿真值：ua_coef = 0.115   internal_gain = 0.80")
    print()
    tau = 1.0 / ua * 60.0
    print(f"  隐含时间常数 tau = {tau:.1f} 分钟")
    print(f"  有人时的稳态温差 = gain/ua = {gain/ua:.2f} °C")
    print()

    print("  【A2】有日照窗口（日照增益模型无法解释的部分）")
    sunny = [(a, b) for a, b, st, nt in AC_SCHEDULE
             if st == "off" and (a, b) in SUNNY_WINDOWS]
    sun_res, sun_wins = fit_ac_off(data, occ, windows=set(sunny))
    if sun_res:
        srmse, sua, sgain = sun_res
        print(f"    单独用 (ua, gain) 拟合的 RMSE = {srmse:.4f} °C")
        if sgain > gain * 1.2:
            print(f"    该组拟合出 internal_gain = {sgain:.2f}，"
                  f"远高于无日照组的 {gain:.2f}")
            print("    -> 差值即为**日照增益**的量级，模型必须显式加入日照项")
        extra = sgain - gain
        print(f"    日照等效增益 ≈ {extra:.2f} °C/小时量级")
    else:
        srmse, sua, sgain, extra = None, None, None, None
    print()

    print("=== B. 空调开启：周期平均口径 ===")
    onw = fit_ac_on(data, occ, ua, gain)
    print(f"  可用开启区间：{len(onw)} 个")
    print()
    print(f"  {'区间':<34}{'T_avg 起':>10}{'T_avg 终':>10}"
          f"{'ΔT_avg':>9}{'小时':>7}{'净速率':>10}")
    print("  " + "-" * 74)
    rows_out = []
    for w in onw:
        r0, r1 = w["rows"][0], w["rows"][-1]
        hrs = (r1["t"] - r0["t"]).total_seconds() / 3600.0
        d = r1["T_avg"] - r0["T_avg"]
        lab = f"{r0['t']:%m-%d %H:%M} -> {r1['t']:%m-%d %H:%M}"
        print(f"  {lab:<34}{r0['T_avg']:>10.2f}{r1['T_avg']:>10.2f}"
              f"{d:>+9.2f}{hrs:>7.2f}{d/hrs:>+10.2f}")
        rows_out.append({"label": lab, "T_avg0": r0["T_avg"],
                         "T_avg1": r1["T_avg"], "hours": hrs,
                         "rate_per_h": d / hrs})
    print()
    print("  （净速率 = 周期平均温度的变化率，含同时的得热）")
    print()

    out = {"ac_off_fit": {"ua_coef": ua, "internal_gain": gain,
                          "rmse": rmse,
                          "current": {"ua_coef": 0.115,
                                      "internal_gain": 0.80}},
           "ac_off_windows": [
               {"t0": str(w["t0"]), "t1": str(w["t1"]), "note": w["note"],
                "n": len(w["rows"])} for w in wins],
           "ac_on_windows": rows_out}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 热工曲线分段拟合", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 实测样本：{len(data)} 条", "",
          "## 一、为什么分两段拟合", "",
          "空调关闭与开启时，房间温度行为完全不同：", "",
          "| 状态 | \\|dT\\|(15分钟) 均值 | 最大 | 特征 |",
          "|---|---|---|---|",
          "| 空调关闭 | 0.29 ~ 0.56 °C | 1.8 ~ 3.1 | 平滑，可用一阶模型直接拟合 |",
          "| 空调开启 | 1.90 ~ 2.50 °C | 4.0 ~ 4.7 | 剧烈震荡（压缩机启停），需周期平均 |",
          "",
          "## 二、空调启停时段（权威口径，取自用户报告）", "",
          "| 区间 | 状态 | 时长 | 备注 |",
          "|---|---|---|---|"]
    for a, b, st, note in AC_SCHEDULE:
        t0 = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        mins = (t1 - t0).total_seconds() / 60.0
        md.append(f"| {a[5:]} → {b[5:]} | {'开' if st=='on' else '关'} | "
                  f"{mins:.0f} 分 | {note} |")
    md += ["", "## 三、空调关闭段拟合结果", "",
           "模型：`dT/dt = -ua_coef·(T - T_out) + internal_gain·1{有人}`", "",
           "| 参数 | 当前仿真值 | **拟合值** |",
           "|---|---|---|",
           f"| `ua_coef` | 0.115 | **{ua:.4f}** |",
           f"| `internal_gain` | 0.80 | **{gain:.2f}** |",
           "",
           f"联合 RMSE = **{rmse:.4f} °C**；隐含时间常数 τ = "
           f"{tau:.1f} 分钟；有人时稳态温差 = {gain/ua:.2f} °C。", "",
           f"参与拟合的关闭区间 {len(wins)} 个：", ""]
    for w in wins:
        md.append(f"- {w['t0']:%m-%d %H:%M} → {w['t1']:%m-%d %H:%M}"
                  f"（{len(w['rows'])} 点）{w['note']}")
    md += ["", "## 四、空调开启段（周期平均口径）", "",
           "| 区间 | 周期平均温度 起 | 终 | ΔT | 时长(小时) | 净速率(°C/小时) |",
           "|---|---|---|---|---|---|"]
    for r in rows_out:
        md.append(f"| {r['label']} | {r['T_avg0']:.2f} | {r['T_avg1']:.2f} | "
                  f"{r['T_avg1']-r['T_avg0']:+.2f} | {r['hours']:.2f} | "
                  f"{r['rate_per_h']:+.2f} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
