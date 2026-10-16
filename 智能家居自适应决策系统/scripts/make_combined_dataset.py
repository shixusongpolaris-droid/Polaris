# -*- coding: utf-8 -*-
"""生成"全天在家"合成数据集（含睡眠占用修正）。

背景
----
雷达（LD2410C）对"长时间静坐"和"睡眠"检测不可靠，实测：
  · 清醒活动/走动          接近 100%
  · 入睡 1 小时内           70~80%
  · 入睡 1 小时以后          1.7%~55%（严重漏检）
  · 确实外出               接近 100%

用户确认的行为规则：不梦游，睡眠期间必然在房间内。
因此对睡眠时段做占用修正：radar 一律置为 1。

处理原则
--------
1. 原始数据完全不动，只读
2. 修正值写入新列，原值保留在 radar_raw
3. 输出到 data/processed/，并标注每行来源与时段

输出
----
data/processed/合成_全天在家样本.csv
data/processed/合成_全天在家样本_说明.md
"""

import csv
import io
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
RAW = os.path.join(PROJECT, "data", "raw")
OUT_DIR = os.path.join(PROJECT, "data", "processed")
OUT_CSV = os.path.join(OUT_DIR, "合成_全天在家样本.csv")
OUT_MD = os.path.join(OUT_DIR, "合成_全天在家样本_说明.md")

# ---- 睡眠时段（用户报告：2026-10-16 凌晨 5:00 睡到 13:30）----
SLEEP_START = "2026-10-16 05:00:00"
SLEEP_END = "2026-10-16 13:34:00"

# ---- 拼接方案：用 10/16 的凌晨+上午+下午，配 10/14 的下午+晚间 ----
SEGMENTS = [
    # (源文件, 日期过滤, 起始时刻, 结束时刻, 时段标签)
    ("daily_2026-10-16.csv",  "2026-10-16", "00:00:00", "18:00:00", "凌晨-上午-下午"),
    ("start_2026-10-14.csv",  "2026-10-14", "16:08:00", "23:59:59", "下午-傍晚-睡前"),
]

OUT_FIELDS = [
    "pc_time", "source_date", "source_file", "segment",
    "temp_c", "humidity_pct", "light_lux",
    "radar_raw", "radar", "occupancy_source",
    "pir1", "pir2", "sht_ok", "outdoor_temp_c", "outdoor_humidity_pct",
    "uptime_s", "note",
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out_rows = []
    stat = {}

    for fname, day, t0, t1, seg in SEGMENTS:
        p = os.path.join(RAW, fname)
        if not os.path.exists(p):
            print(f"[跳过] 文件不存在: {fname}")
            continue
        n_in = n_sleep = 0
        for r in csv.DictReader(io.open(p, encoding="utf-8")):
            ts = r.get("pc_time", "")
            if not ts:
                continue
            if ts[:10] != day:
                continue
            hm = ts[11:19]
            if not (t0 <= hm <= t1):
                continue
            n_in += 1

            radar_raw = r.get("radar", "")
            radar = radar_raw
            source = "radar"
            if SLEEP_START <= ts[:19] <= SLEEP_END:
                radar = "1"
                source = "行为规则(睡眠默认在房内)"
                n_sleep += 1

            out_rows.append({
                "pc_time": ts,
                "source_date": day,
                "source_file": fname,
                "segment": seg,
                "temp_c": r.get("temp_c", ""),
                "humidity_pct": r.get("humidity_pct", ""),
                "light_lux": r.get("light_lux", ""),
                "radar_raw": radar_raw,
                "radar": radar,
                "occupancy_source": source,
                "pir1": r.get("pir1", ""),
                "pir2": r.get("pir2", ""),
                "sht_ok": r.get("sht_ok", ""),
                "outdoor_temp_c": r.get("outdoor_temp_c", ""),
                "outdoor_humidity_pct": r.get("outdoor_humidity_pct", ""),
                "uptime_s": r.get("uptime_s", ""),
                "note": r.get("note", ""),
            })
        stat[fname] = (n_in, n_sleep)

    out_rows.sort(key=lambda x: x["pc_time"])

    with io.open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    total = len(out_rows)
    n_corr = sum(1 for r in out_rows if r["occupancy_source"] != "radar")
    n_occ = sum(1 for r in out_rows if str(r["radar"]) == "1")

    print("=" * 66)
    print(" 合成数据集生成完成")
    print("=" * 66)
    print(f" 输出: {OUT_CSV}")
    print(f" 总行数: {total}")
    for f, (a, b) in stat.items():
        print(f"   {f:<28} 取 {a:>5} 行，其中修正占用 {b:>5} 行")
    print()
    print(f" 占用修正行数: {n_corr} ({100*n_corr/total:.1f}%)")
    print(f" 修正后 '有人' 占比: {100*n_occ/total:.1f}%")
    print(f" 时间范围: {out_rows[0]['pc_time'][:19]} ~ {out_rows[-1]['pc_time'][:19]}")

    # 覆盖度检查
    first = datetime.strptime(out_rows[0]["pc_time"][:19], "%Y-%m-%d %H:%M:%S")
    last = datetime.strptime(out_rows[-1]["pc_time"][:19], "%Y-%m-%d %H:%M:%S")
    span_h = (last - first).total_seconds() / 3600
    print(f" 时间跨度: {span_h:.1f} 小时（{total} 行，约 {total/span_h:.0f} 行/小时）")

    # 说明文档
    md = f"""# 合成数据集说明：全天在家样本

## 文件

- 数据：`{os.path.basename(OUT_CSV)}`
- 本文档：`{os.path.basename(OUT_MD)}`

## 拼接方案

把两段不同日期的数据拼成一个"完整一天"的居家样本：

| 来源文件 | 日期 | 取用时段 | 覆盖内容 | 行数 |
|---|---|---|---|---|
"""
    for f, (a, b) in stat.items():
        seg = next(s for fn, d, t0, t1, s in SEGMENTS if fn == f)
        md += f"| `{f}` | {[d for fn,d,_,_,_ in SEGMENTS if fn==f][0]} | "
        md += f"{[t0 for fn,_,t0,_,_ in SEGMENTS if fn==f][0]}~{[t1 for fn,_,_,t1,_ in SEGMENTS if fn==f][0]} | {seg} | {a} |\n"

    md += f"""
合计 **{total}** 行，时间跨度 **{span_h:.1f}** 小时。

## 占用数据修正

### 背景：雷达的实际能力边界（实测）

| 场景 | 实测准确率 |
|---|---|
| 清醒、走动/活动 | 接近 100% |
| 入睡 1 小时内 | 70~80% |
| 入睡 1 小时以后 | 1.7%~55%（严重漏检） |
| 确实外出 | 接近 100% |

LD2410C 依靠微波微动检测，**长时间静坐与深度睡眠时位移极小**，
会大量误判为"无人"。这是模块的物理限制，不是接线或参数问题。

### 修正规则

用户报告：**2026-10-16 凌晨 5:00 入睡，13:30 醒来**。

依据"不梦游则睡眠期间必然在房间内"这一行为事实，
对睡眠时段的占用数据做修正：

- 修正区间：`{SLEEP_START}` ~ `{SLEEP_END}`
- 修正方式：`radar` 一律置为 `1`（有人）
- **原始值保留在 `radar_raw` 列**，可随时追溯与还原
- `occupancy_source` 列标注该行占用值的来源

共修正 **{n_corr}** 行（占 {100*n_corr/total:.1f}%）。

## 列说明

| 列名 | 说明 |
|---|---|
| `pc_time` | 电脑系统时间（毫秒精度） |
| `source_date` | 该行来自哪一天 |
| `source_file` | 原始文件名 |
| `segment` | 时段标签 |
| `temp_c` / `humidity_pct` | 室内温湿度（SHT31） |
| `light_lux` | 光照（GY-302） |
| `radar_raw` | **雷达原始输出**（未修正） |
| `radar` | **占用判定值**（睡眠时段已修正） |
| `occupancy_source` | 占用值来源：`radar` 或 `行为规则(...)` |
| `pir1` / `pir2` | 两个 PIR 的人体活动触发 |
| `outdoor_temp_c` | 室外温度（仅 10/16 有，来自 Open-Meteo） |
| `uptime_s` | 设备运行秒数 |

## 已知限制

1. **两段数据来自不同日期**，室外条件不完全可比（10/14 室外 16~25 摄氏度，
   10/16 室外最高约 25 摄氏度）
2. **10/14 的数据缺少室外温度列**（天气抓取功能 10/15 才加入）
3. **拼接点（18:00）存在日期跳变**，分析时间序列时需注意
4. 睡眠时段的占用是**规则推断值**，不是传感器实测值
5. 雷达在白天静坐时仍会漏检，本数据集**未对白天漏检做修正**
   （白天无法用行为规则确证，需结合 PIR 与门状态判断）

## 生成信息

- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}
- 生成脚本：`scripts/make_combined_dataset.py`
- 原始数据未被修改
"""
    io.open(OUT_MD, "w", encoding="utf-8").write(md)
    print(f" 说明文档: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
