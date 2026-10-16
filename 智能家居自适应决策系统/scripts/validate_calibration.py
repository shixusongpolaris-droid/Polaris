# -*- coding: utf-8 -*-
"""标定后验证：用修正参数驱动仿真，与实测曲线对比。

验证目标
--------
在"空调关闭、无人、无日照"的实测窗口上，用纯热模型（不含策略）
从实测初始温度出发积分，检查仿真曲线与实测曲线是否吻合。

对比窗口
--------
· 夜间 10-15 02:54 ~ 05:40（空调关、睡眠、无日照）
· 凌晨 10-16 02:20 ~ 05:05（空调关、睡眠、无日照）
· 下午 10-16 14:10 ~ 15:59（空调关、无人、有日照）

评价指标：RMSE、偏差、以及关键统计量（起始/终止/极值）。
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
OUT = os.path.join(PROJ, "docs", "标定验证结果.md")

sys.path.insert(0, os.path.join(PROJ, "src"))

FILES = ["start_2026-10-14.csv", "start_2026-10-15.csv",
         "daily_2026-10-15.csv", "daily_2026-10-16.csv"]


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
                r["_T"] = float(r["temp_c"])
            except (ValueError, TypeError):
                continue
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    return rows


def simulate(cfg, T0, tout_series, n_steps, gain_per_step):
    """纯热模型积分（不含空调）。

    T[k+1] = T[k] - ua_coef*(T[k] - T_out[k]) + gain
    """
    T = T0
    out = [T]
    for k in range(n_steps):
        To = tout_series[min(k, len(tout_series) - 1)]
        T = T - cfg.ua_coef * (T - To) + gain_per_step
        out.append(T)
    return out


def main():
    from env.config import SimConfig
    cfg = SimConfig()
    rows = load()
    print(f"载入 {len(rows)} 行")
    print(f"参数: ua_coef={cfg.ua_coef}  internal_gain={cfg.internal_gain}  "
          f"temp_outdoor_mean={cfg.temp_outdoor_mean}")
    print()

    # 各窗口：起始、结束、是否有日照
    WINDOWS = [
        ("夜间 10-15 02:54~05:40", "2026-10-15 02:54", "2026-10-15 05:40", False),
        ("凌晨 10-16 02:20~05:05", "2026-10-16 02:20", "2026-10-16 05:05", False),
        ("下午 10-16 14:10~15:59", "2026-10-16 14:10", "2026-10-16 15:59", True),
    ]

    print("=" * 88)
    print("标定后仿真 vs 实测")
    print("=" * 88)
    print(f"  {'窗口':<26}{'起始':>8}{'实测末':>8}{'仿真末':>8}{'RMSE':>8}{'偏差':>8}")
    print("  " + "-" * 82)

    res = []
    for label, t0, t1, solar in WINDOWS:
        seg = [r for r in rows if t0 <= r["_t"].strftime("%Y-%m-%d %H:%M") < t1]
        if len(seg) < 60:
            print(f"  {label:<26}  数据不足")
            continue
        # 用 15 分钟步长抽样
        step = 90        # 90 个 10 秒样本 = 15 分钟
        sample = seg[::step]
        meas = [r["_T"] for r in sample]
        # 室外温度：有就用实测，没有用配置均值
        tout = []
        for r in sample:
            try:
                tout.append(float(r.get("outdoor_temp_c") or ""))
            except (TypeError, ValueError):
                tout.append(cfg.temp_outdoor_mean)
        # 日照窗口额外加太阳得热（实测平衡温度比夜间高约 2.5 C）
        gain = cfg.internal_gain
        if solar:
            gain = cfg.internal_gain + 0.33 * cfg.ua_coef   # +0.33C 偏移的等效增益
        sim = simulate(cfg, meas[0], tout, len(meas) - 1, gain)

        errs = [s - m for s, m in zip(sim, meas)]
        rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
        bias = statistics.mean(errs)
        res.append((label, meas, sim, rmse, bias))
        print(f"  {label:<26}{meas[0]:>8.2f}{meas[-1]:>8.2f}{sim[-1]:>8.2f}"
              f"{rmse:>8.3f}{bias:>+8.2f}")

    print()
    if res:
        rmses = [r[3] for r in res]
        print(f"  平均 RMSE = {statistics.mean(rmses):.3f} C")
        print(f"  最大 RMSE = {max(rmses):.3f} C")

    # 打印夜间窗口的曲线对比
    print()
    print("=" * 88)
    print("夜间窗口逐点对比（10-15 02:54~05:40）")
    print("=" * 88)
    label, meas, sim, rmse, bias = res[0]
    print(f"  {'步':>4}{'实测':>9}{'仿真':>9}{'偏差':>9}")
    for i in range(0, len(meas), max(1, len(meas) // 12)):
        print(f"  {i:>4}{meas[i]:>9.2f}{sim[i]:>9.2f}{sim[i]-meas[i]:>+9.2f}")
    print(f"  {len(meas)-1:>4}{meas[-1]:>9.2f}{sim[-1]:>9.2f}{sim[-1]-meas[-1]:>+9.2f}")

    md = ["# 标定验证结果", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 参数：`ua_coef={cfg.ua_coef}`、`internal_gain={cfg.internal_gain}`、"
          f"`temp_outdoor_mean={cfg.temp_outdoor_mean}`、"
          f"`temp_outdoor_swing={cfg.temp_outdoor_swing}`",
          "",
          "## 方法",
          "",
          "在「空调关闭」的实测窗口上，用纯热模型从实测初始温度积分：",
          "",
          "```",
          "T[k+1] = T[k] - ua_coef*(T[k] - T_out[k]) + gain",
          "```",
          "",
          "与实测曲线逐点比较，评价 RMSE 与偏差。",
          "",
          "## 结果",
          "",
          "| 窗口 | 起始温度 | 实测终止 | 仿真终止 | RMSE(C) | 偏差(C) |",
          "|---|---|---|---|---|---|"]
    for label, meas, sim, rmse, bias in res:
        md.append(f"| {label} | {meas[0]:.2f} | {meas[-1]:.2f} | {sim[-1]:.2f} | "
                  f"{rmse:.3f} | {bias:+.2f} |")
    md.append("")
    if res:
        md.append(f"平均 RMSE **{statistics.mean([r[3] for r in res]):.3f} °C**。")
    md.append("")
    io.open(OUT, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print()
    print(f"报告已保存: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
