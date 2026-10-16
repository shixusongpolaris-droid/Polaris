# -*- coding: utf-8 -*-
"""生成「全天在家」完整 24 小时合成数据集（最终版）。

拼接方案
--------
把两段不同日期的数据拼成一个完整一天：

  10/16 00:00 ~ 18:00   凌晨-上午-下午（含睡眠）
  10/14 16:08 ~ 24:00   下午-傍晚-睡前

拼接点设为 18:00：18:00 之前取 10/16，18:00 之后取 10/14。
两段在时刻上首尾相接，形成一个时刻连续的一天。

占用修正
--------
雷达对睡眠的漏检已实测确认（约 45%）。依据用户行为事实
（不梦游则睡眠期间必然在房间内），对睡眠时段做占用修正：

  10/16 05:06 ~ 13:26   睡眠 -> radar 置 1

原始值保留在 radar_raw 列，并新增 occupancy_source 列标注来源。

输出
----
data/processed/合成_全天在家样本_完整24h.csv
data/processed/合成_全天在家样本_完整24h_说明.md
"""

import csv
import io
import os
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
RAW = os.path.join(PROJ, "data", "raw")
OUT_DIR = os.path.join(PROJ, "data", "processed")
OUT_CSV = os.path.join(OUT_DIR, "合成_全天在家样本_完整24h.csv")
OUT_MD = os.path.join(OUT_DIR, "合成_全天在家样本_完整24h_说明.md")

# 睡眠时段（用户报告，精确）
SLEEP = [("2026-10-16 05:06:00", "2026-10-16 13:26:00")]

# (源文件, 日期, 起始时刻, 结束时刻, 时段标签, 时刻平移分钟数)
#
# 时刻平移：把第二段的时刻整体后移，使其接在第一段之后，
# 避免同一时刻出现两组数据（10-14 与 10-16 都覆盖 16:00~18:00）。
# 10-14 取 16:08~23:59（约 472 分钟），平移 112 分钟后落在
# 18:00~25:52 -> 取到 24:00 为止，形成完整的一天。
SEGMENTS = [
    ("daily_2026-10-16.csv", "2026-10-16", "00:00:00", "18:00:00",
     "凌晨-上午-下午", 0),
    ("start_2026-10-14.csv", "2026-10-14", "16:08:00", "23:59:59",
     "下午-傍晚-睡前", 112),
]

FIELDS = ["pc_time", "source_date", "source_file", "segment", "clock_time",
          "temp_c", "humidity_pct", "light_lux",
          "radar_raw", "radar", "occupancy_source",
          "pir1", "pir2", "sht_ok",
          "outdoor_temp_c", "outdoor_humidity_pct", "uptime_s", "note"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = []
    stat = {}

    for fn, day, t0, t1, seg, shift in SEGMENTS:
        p = os.path.join(RAW, fn)
        if not os.path.exists(p):
            print(f"[跳过] {fn} 不存在")
            continue
        n_in = n_sleep = 0
        t0s = datetime.strptime(t0, "%H:%M:%S")
        t1s = datetime.strptime(t1, "%H:%M:%S")
        for r in csv.DictReader(io.open(p, encoding="utf-8")):
            ts = r.get("pc_time", "")
            if len(ts) < 19 or ts[:10] != day:
                continue
            hm = ts[11:19]
            if not (t0 <= hm <= t1):
                continue

            # 平移时刻：使其接在前一段之后
            dt = datetime.strptime(hm, "%H:%M:%S") + timedelta(minutes=shift)
            if dt.day != t0s.day:
                continue          # 平移后越过 24:00 的行丢弃
            clock_str = dt.strftime("%H:%M:%S")

            n_in += 1

            raw_v = r.get("radar", "")
            val = raw_v
            src = "radar"
            for a, b in SLEEP:
                if a <= ts[:19] <= b:
                    val = "1"
                    src = "行为规则(睡眠默认在房内)"
                    n_sleep += 1
                    break

            out.append({
                "pc_time": ts,
                "source_date": day,
                "source_file": fn,
                "segment": seg,
                "clock_time": clock_str,
                "temp_c": r.get("temp_c", ""),
                "humidity_pct": r.get("humidity_pct", ""),
                "light_lux": r.get("light_lux", ""),
                "radar_raw": raw_v,
                "radar": val,
                "occupancy_source": src,
                "pir1": r.get("pir1", ""),
                "pir2": r.get("pir2", ""),
                "sht_ok": r.get("sht_ok", ""),
                "outdoor_temp_c": r.get("outdoor_temp_c", ""),
                "outdoor_humidity_pct": r.get("outdoor_humidity_pct", ""),
                "uptime_s": r.get("uptime_s", ""),
                "note": r.get("note", ""),
            })
        stat[f"{fn}(+{shift}min)"] = (n_in, n_sleep)

    # 按"时刻"排序，使两段首尾相接形成连续的一天
    out.sort(key=lambda x: x["clock_time"])

    with io.open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)

    total = len(out)
    n_corr = sum(1 for r in out if r["occupancy_source"] != "radar")
    n_occ = sum(1 for r in out if str(r["radar"]) == "1")
    print("=" * 70)
    print(" 完整 24 小时合成数据集生成完成")
    print("=" * 70)
    print(f" 输出: {OUT_CSV}")
    print(f" 总行数: {total}")
    for f, (a, b) in stat.items():
        print(f"   {f:<28} 取 {a:>5} 行，修正占用 {b:>5} 行")
    print(f" 占用修正合计: {n_corr} 行 ({100*n_corr/total:.1f}%)")
    print(f" 修正后「有人」占比: {100*n_occ/total:.1f}%")
    print(f" 时刻范围: {out[0]['clock_time']} ~ {out[-1]['clock_time']}")

    # 按小时覆盖检查
    hours = {}
    for r in out:
        hours.setdefault(r["clock_time"][:2], 0)
        hours[r["clock_time"][:2]] += 1
    missing = [f"{h:02d}" for h in range(24) if f"{h:02d}" not in hours]
    print(f" 覆盖小时数: {len(hours)}/24")
    if missing:
        print(f" 缺失小时: {', '.join(missing)}")

    md = f"""# 完整 24 小时「全天在家」合成数据集说明

## 文件

- 数据：`{os.path.basename(OUT_CSV)}`
- 说明：`{os.path.basename(OUT_MD)}`
- 原始数据：`data/raw/`（未被修改）

## 拼接方案

把两段不同日期的实测数据按**时刻**拼成完整一天：

| 来源 | 日期 | 取用时段 | 覆盖内容 | 行数 |
|---|---|---|---|---|
| `daily_2026-10-16.csv` | 10-16 | 00:00 ~ 18:00 | 凌晨-上午-下午 | {stat.get('daily_2026-10-16.csv', (0,0))[0]} |
| `start_2026-10-14.csv` | 10-14 | 16:08 ~ 24:00 | 下午-傍晚-睡前 | {stat.get('start_2026-10-14.csv', (0,0))[0]} |

拼接点：**18:00**（18:00 之前取 10-16，之后取 10-14）。
两段按时刻排序后首尾相接，构成一个完整的 24 小时周期。

合计 **{total}** 行，覆盖 **{len(hours)}/24** 小时。
{'缺失小时：' + ', '.join(missing) + ' 时' if missing else '24 小时全覆盖。'}

## 占用数据修正

### 雷达实测能力

| 场景 | 准确率 |
|---|---|
| 清醒在房间 / 确实外出 | 约 98% |
| 睡眠时段 | 约 45%（LD2410C 依赖微动检测，静止时漏检） |

### 修正规则

用户报告睡眠时段：**10-16 05:06 ~ 13:26**。

依据「不梦游则睡眠期间必然在房间内」这一行为事实，
对该时段的占用值置为 `1`（有人）。

- 修正行数：**{n_corr}**（占 {100*n_corr/total:.1f}%）
- **原始雷达值保留在 `radar_raw` 列**，可随时还原
- `occupancy_source` 列标注该行占用值的来源
  （`radar` 或 `行为规则(睡眠默认在房内)`）

## 列说明

| 列名 | 说明 |
|---|---|
| `pc_time` | 原始采集时间（含日期与毫秒） |
| `source_date` / `source_file` | 该行来自哪一天/哪个文件 |
| `segment` | 时段标签 |
| `clock_time` | 时刻（HH:MM:SS），拼接后的一天内时间轴 |
| `temp_c` / `humidity_pct` | 室内温湿度（SHT31） |
| `light_lux` | 光照（GY-302） |
| `radar_raw` | **雷达原始输出**（未修正） |
| `radar` | **占用判定值**（睡眠时段已修正） |
| `occupancy_source` | 占用值来源 |
| `pir1` / `pir2` | 两个 PIR 的人体活动触发 |
| `outdoor_temp_c` / `outdoor_humidity_pct` | 室外气象（仅 10-16 有） |
| `uptime_s` | 设备运行秒数 |

## 已知限制

1. 两段数据来自不同日期，室外条件不完全可比
   （10-14 室外 16~25 摄氏度，10-16 最高约 25 摄氏度）
2. 10-14 的数据缺少室外温度列（天气抓取功能于 10-15 才加入）
3. 拼接点 18:00 处存在日期跳变，做时间序列分析时需注意
4. 睡眠时段占用为**规则推断值**，非传感器实测
5. 白天雷达对长时间静止者仍会漏检，本数据集**未对该情况做修正**

## 生成信息

- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}
- 生成脚本：`scripts/make_combined_dataset_24h.py`
- 原始数据未被修改
"""
    io.open(OUT_MD, "w", encoding="utf-8").write(md)
    print(f" 说明文档: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
