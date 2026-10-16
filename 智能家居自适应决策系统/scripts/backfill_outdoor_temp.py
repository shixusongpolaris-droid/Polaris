# -*- coding: utf-8 -*-
"""回填缺失的室外温度数据（用采集脚本同一个天气源）。

问题
----
实测数据的室外温度覆盖率：

    2026-10-14    0 / 2831   （0%，整天缺失）
    2026-10-15 6099 / 8620   （71%，07:03 之后才有）
    2026-10-16 6466 / 6467   （100%）

而室外温度是热模型标定的**边界条件**，缺失会导致空调关闭段无法拟合
（含 10-14 全部窗口与 10-15 凌晨窗口）。

做法
----
用与 `scripts/fetch_weather.py` **完全相同的天气源**（Open-Meteo，
坐标上海闵行 31.11/121.38）取历史逐小时温度，按小时回填。

**先做可靠性校验**：用 10-16 已有实测的室外温度与 API 同时刻值对比，
若偏差可接受，才用 API 数据回填 10-14 与 10-15 凌晨。

**不修改原始 CSV**（它们已按字节入库），而是另存
`data/processed/outdoor_temp_backfill.csv`，分析脚本按需合并。

输出
----
data/processed/outdoor_temp_backfill.csv
data/processed/outdoor_temp_backfill_说明.md
models/outdoor_backfill_check.json
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
LAT, LON = 31.11, 121.38
TZ = 8
OUT_CSV = os.path.join(PROJ, "data", "processed",
                       "outdoor_temp_backfill.csv")
OUT_MD = os.path.join(PROJ, "data", "processed",
                      "outdoor_temp_backfill_说明.md")
OUT_JSON = os.path.join(PROJ, "models", "outdoor_backfill_check.json")

URL = ("https://api.open-meteo.com/v1/forecast"
       f"?latitude={LAT}&longitude={LON}"
       "&hourly=temperature_2m,relative_humidity_2m"
       "&past_days=7&forecast_days=1")


def fetch_hourly():
    with urllib.request.urlopen(URL, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8"))
    h = d.get("hourly", {})
    return list(zip(h.get("time", []),
                    h.get("temperature_2m", []),
                    h.get("relative_humidity_2m", [])))


def load_measured():
    """读取实测室外温度（仅已有的非空值）。"""
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
                v = r.get("outdoor_temp_c")
                if t and v not in (None, ""):
                    seen[t[:19]] = float(v)
    return seen


def main():
    print("=" * 78)
    print(" 室外温度回填（Open-Meteo 历史数据）")
    print("=" * 78)
    try:
        hourly = fetch_hourly()
    except Exception as e:
        print(f"  抓取失败：{type(e).__name__}: {e}")
        return 1
    print(f"  抓取到 {len(hourly)} 个逐小时点")
    if hourly:
        print(f"  范围：{hourly[0][0]} ~ {hourly[-1][0]}")
    api = {}
    for t, temp, hum in hourly:
        # 统一键格式为 "YYYY-MM-DD HH"，避免实测（空格分隔）与
        # API（ISO 的 T 分隔）不一致导致无法配对
        api[t.replace("T", " ")[:13]] = (temp, hum)
    print()

    # ---------- 可靠性校验：用 10-16 实测对比 ----------
    meas = load_measured()
    print("=== 可靠性校验（实测 vs API，逐小时平均）===")
    buckets = {}
    for t, v in meas.items():
        key = t.replace("T", " ")[:13]        # YYYY-MM-DD HH
        buckets.setdefault(key, []).append(v)
    diffs = []
    print(f"  {'小时':<18}{'实测均值':>10}{'API':>9}{'偏差':>9}{'样本':>7}")
    print("  " + "-" * 54)
    shown = 0
    for key in sorted(buckets):
        if key not in api:
            continue
        m = mean(buckets[key])
        a = api[key][0]
        if a is None:
            continue
        diffs.append(a - m)
        if shown < 14:
            print(f"  {key:<18}{m:>10.2f}{a:>9.2f}{a-m:>+9.2f}"
                  f"{len(buckets[key]):>7}")
            shown += 1
    print()
    if diffs:
        md_ = mean(diffs)
        mae = mean(abs(x) for x in diffs)
        mx = max(abs(x) for x in diffs)
        print(f"  对比小时数 {len(diffs)}")
        print(f"  平均偏差 {md_:+.3f} °C   平均绝对偏差 {mae:.3f} °C   "
              f"最大绝对偏差 {mx:.3f} °C")
        ok = mae <= 1.5
        print(f"  -> {'✅ 可靠，可用于回填' if ok else '⚠️ 偏差偏大，谨慎使用'}")
    else:
        md_ = mae = mx = None
        ok = False
        print("  ⚠️ 无可对比数据")
    print()

    # ---------- 生成回填文件 ----------
    rows = []
    for t, temp, hum in hourly:
        if temp is None:
            continue
        dt = datetime.fromisoformat(t)
        if not (datetime(2026, 10, 14) <= dt < datetime(2026, 10, 17)):
            continue
        rows.append({"hour": t, "outdoor_temp_c": temp,
                     "outdoor_humidity_pct": hum})
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with io.open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["hour", "outdoor_temp_c",
                                          "outdoor_humidity_pct"])
        w.writeheader()
        w.writerows(rows)
    print(f"  已写出 {len(rows)} 行 -> {OUT_CSV}")
    print()

    # 各日覆盖
    from collections import Counter
    c = Counter(r["hour"][:10] for r in rows)
    for k in sorted(c):
        print(f"    {k}: {c[k]} 小时")

    md = ["# 室外温度回填说明", "",
          "## 一、为什么需要回填", "",
          "实测数据的室外温度覆盖率：", "",
          "| 日期 | 有室外温度 | 覆盖率 |",
          "|---|---|---|",
          "| 2026-10-14 | 0 / 2831 | 0% |",
          "| 2026-10-15 | 6099 / 8620 | 71%（07:03 之后） |",
          "| 2026-10-16 | 6466 / 6467 | 100% |", "",
          "室外温度是热模型的**边界条件**，缺失会使空调关闭段无法拟合。", "",
          "## 二、数据来源", "",
          f"- 天气源：**Open-Meteo**（与 `scripts/fetch_weather.py` 同一来源）",
          f"- 坐标：上海闵行（{LAT}, {LON}），与采集脚本一致",
          f"- 接口：`past_days=7` 的逐小时温度",
          f"- 抓取时间：{datetime.now():%Y-%m-%d %H:%M:%S}", "",
          "## 三、可靠性校验", "",
          "用 10-16 已有实测室外温度与 API 同时刻值逐小时对比：", ""]
    if diffs:
        md += [f"- 对比小时数：{len(diffs)}",
               f"- 平均偏差：{md_:+.3f} °C",
               f"- 平均绝对偏差：**{mae:.3f} °C**",
               f"- 最大绝对偏差：{mx:.3f} °C",
               "",
               ("**结论：偏差可接受，API 数据可用于回填。**" if ok else
                "**结论：偏差偏大，回填数据仅作参考，需谨慎使用。**")]
    md += ["", "## 四、使用方式", "",
           "**原始 CSV 未被修改**（已按字节入库，保持可追溯）。",
           "分析脚本按需合并本文件：对缺少室外温度的样本，",
           "用所在小时的 API 值填充。", "",
           "| 日期 | 回填小时数 |", "|---|---|"]
    for k in sorted(c):
        md.append(f"| {k} | {c[k]} |")
    md += ["", "## 五、注意事项", "",
           "1. API 给的是**逐小时**值，同一小时内所有样本共用该值，",
           "   因此回填数据的**时间分辨率低于实测**（实测为 10 秒或 15 分钟）。",
           "2. 回填值来自**区域气象数据**，与房间实际所处的微环境",
           "   （楼栋遮挡、朝向、楼层）存在差异。",
           "3. 使用回填数据的拟合结果应在报告中**明确标注**，",
           "   与使用实测室外温度的拟合结果区分。", ""]
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"n_hours": len(rows),
                   "check": {"n_pairs": len(diffs) if diffs else 0,
                             "bias": md_, "mae": mae, "max_abs": mx},
                   "coverage": dict(c)}, f, ensure_ascii=False, indent=2)
    print()
    print(f"  说明已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
