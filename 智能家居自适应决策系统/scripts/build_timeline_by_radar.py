# -*- coding: utf-8 -*-
"""按用户规则生成时间表与雷达表。

用户规则（本轮确定）
--------------------
1. **除睡眠时段外，一律以雷达读数为准**（雷达说不在就不在）。
2. 睡眠时段：用户不梦游，必然在房间 -> 视为「在房间·睡眠」；
   该时段雷达的低检出率是已知缺陷，不计入准确率统计。
3. 用户报告的精确睡眠时段：
     · 10/14 22:56:47 ~ 10/15 09:16
     · 10/16 05:06 ~ 13:28

输出
----
data/processed/timeline_radar.csv    时间表（雷达分段）
data/processed/radar_stats.csv       雷达准确率表
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
OUT_TL = os.path.join(PROJ, "data", "processed", "timeline_radar.csv")
OUT_ST = os.path.join(PROJ, "data", "processed", "radar_stats.csv")

FILES = ["start_2026-10-14.csv", "start_2026-10-15.csv",
         "daily_2026-10-15.csv", "daily_2026-10-16.csv"]

# 睡眠时段（用户报告）
SLEEP = [
    ("2026-10-14 22:56:47", "2026-10-15 09:16:00"),
    ("2026-10-16 05:06:00", "2026-10-16 13:28:00"),
]

# 用户报告的关键事件（用于对表一加注）
MARKS = [
    ("2026-10-14 17:29:43", "关空调 + 出门"),
    ("2026-10-14 17:48:53", "返回房间"),
    ("2026-10-14 18:41:03", "开空调"),
    ("2026-10-14 19:54:44", "关空调 + 开窗 + 离开"),
    ("2026-10-14 20:51:37", "返回 + 关窗 + 开空调"),
    ("2026-10-14 22:56:47", "上床睡觉 + 空调 4h 定时"),
    ("2026-10-15 02:54:00", "空调定时关闭"),
    ("2026-10-15 05:40:00", "中途清醒 + 重新开空调"),
    ("2026-10-15 08:42:51", "关空调 + 开窗"),
    ("2026-10-15 09:16:00", "起床出门"),
    ("2026-10-15 17:06:00", "家人短暂进入"),
    ("2026-10-15 21:05:00", "本人回家"),
    ("2026-10-15 21:06:00", "打开房门"),
    ("2026-10-15 21:07:21", "出去洗澡（门开着）"),
    ("2026-10-15 21:45:41", "返回房间"),
    ("2026-10-15 21:46:18", "关门 + 开空调"),
    ("2026-10-16 05:06:00", "入睡"),
    ("2026-10-16 13:28:00", "醒来"),
    ("2026-10-16 13:34:00", "拉开窗帘"),
    ("2026-10-16 13:48:00", "关空调"),
    ("2026-10-16 14:09:00", "出房间去吃午饭"),
]


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
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    seen, out = set(), []
    for r in rows:
        if r["_t"] in seen:
            continue
        seen.add(r["_t"])
        out.append(r)
    return out


def in_sleep(t):
    s = t.strftime("%Y-%m-%d %H:%M:%S")
    for a, b in SLEEP:
        if a <= s < b:
            return True
    return False


def mark_near(t):
    for ms, txt in MARKS:
        mt = datetime.strptime(ms, "%Y-%m-%d %H:%M:%S")
        if abs((t - mt).total_seconds()) <= 60:
            return txt
    return ""


def main():
    rows = load()
    print(f"载入 {len(rows)} 行")

    # ---- 逐点判定状态 ----
    for r in rows:
        if in_sleep(r["_t"]):
            r["_state"] = "在房间·睡眠"
            r["_src"] = "规则(睡眠必然在房内)"
        else:
            r["_state"] = "在房间" if str(r.get("radar", "0")).strip() == "1" else "不在房间"
            r["_src"] = "雷达"

    # 合并规则：
    #   1. 相邻同状态且间隔 <= 60 秒 -> 合并
    #   2. 时长 < 5 分钟的孤立区间 -> 并入前一段（视为雷达抖动）
    #   3. 合并后再次扫描，直到稳定
    def merge_pass(segs, min_dur=300):
        out = []
        for s in segs:
            dur = (s["end"] - s["start"]).total_seconds()
            if dur < min_dur and out:
                out[-1]["end"] = s["end"]
                out[-1]["n"] += s["n"]
            else:
                out.append(s)
        return out

    segs0 = []
    cur = None
    for r in rows:
        st = r["_state"]
        if cur and cur["state"] == st and (r["_t"] - cur["end"]).total_seconds() <= 60:
            cur["end"] = r["_t"]
            cur["n"] += 1
        else:
            if cur:
                segs0.append(cur)
            cur = {"start": r["_t"], "end": r["_t"], "state": st,
                   "src": r["_src"], "n": 1}
    if cur:
        segs0.append(cur)

    merged = merge_pass(segs0, 300)
    # 再合并一次相连的同状态段
    final = []
    for s in merged:
        if final and final[-1]["state"] == s["state"]:
            final[-1]["end"] = s["end"]
            final[-1]["n"] += s["n"]
        else:
            final.append(s)
    merged = merge_pass(final, 300)

    # ---- 输出时间表 ----
    print()
    print("=" * 96)
    print("表一：时间表（雷达分段 + 睡眠规则修正）")
    print("=" * 96)
    print(f"{'起始':<18}{'结束':<18}{'时长':>8}  {'人员状态':<14}{'来源':<20}{'事件'}")
    print("-" * 96)
    with io.open(OUT_TL, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["start", "end", "minutes", "state", "source", "event"])
        for s in merged:
            dur = (s["end"] - s["start"]).total_seconds() / 60
            mk = mark_near(s["start"]) or mark_near(s["end"])
            print(f"{s['start']:%m-%d %H:%M:%S}  {s['end']:%m-%d %H:%M:%S}  "
                  f"{dur:>6.0f}分  {s['state']:<14}{s['src']:<20}{mk}")
            w.writerow([s["start"].strftime("%Y-%m-%d %H:%M:%S"),
                        s["end"].strftime("%Y-%m-%d %H:%M:%S"),
                        f"{dur:.0f}", s["state"], s["src"], mk])

    # ---- 雷达准确率表 ----
    # 以"雷达为准"的时段无法再算准确率（雷达就是真值）。
    # 因此只统计两类可比对的时段：
    #   A. 睡眠时段：真值=有人，看雷达漏检率
    #   B. 用户明确报告在房间/外出的时段：与雷达比对
    USER_TRUTH = [
        # (起, 止, 真值, 说明)
        ("2026-10-14 16:08", "2026-10-14 17:29", "有人", "用户报告在房间"),
        ("2026-10-14 19:54", "2026-10-14 20:51", "无人", "用户报告离开"),
        ("2026-10-14 21:07", "2026-10-14 21:45", "无人", "用户报告洗澡(门开)"),
        ("2026-10-14 22:56", "2026-10-15 09:16", "有人", "睡眠(规则)"),
        ("2026-10-15 09:16", "2026-10-15 17:06", "无人", "用户报告全天不在家"),
        ("2026-10-15 17:06", "2026-10-15 17:13", "有人", "母亲短暂进入"),
        ("2026-10-15 17:13", "2026-10-15 21:05", "无人", "用户报告未回家"),
        ("2026-10-15 21:07", "2026-10-15 21:45", "无人", "用户报告洗澡(门开)"),
        ("2026-10-15 21:45", "2026-10-16 05:06", "有人", "用户报告在房间"),
        ("2026-10-16 05:06", "2026-10-16 13:28", "有人", "睡眠(规则)"),
        ("2026-10-16 13:28", "2026-10-16 14:09", "有人", "用户报告在房间"),
        ("2026-10-16 14:09", "2026-10-16 15:55", "无人", "用户报告出去吃午饭"),
    ]

    print()
    print("=" * 96)
    print("表二：雷达准确率（仅统计有明确真值的时段）")
    print("=" * 96)
    print(f"{'时段':<40}{'真值':<8}{'条数':>7}{'雷达有人':>10}{'准确率':>9}  说明")
    print("-" * 96)
    stats = []
    for a, b, truth, why in USER_TRUTH:
        v = [r for r in rows
             if a <= r["_t"].strftime("%Y-%m-%d %H:%M") < b]
        if not v:
            continue
        n1 = sum(1 for r in v if str(r.get("radar", "0")).strip() == "1")
        n0 = len(v) - n1
        acc = 100 * (n1 if truth == "有人" else n0) / len(v)
        stats.append((a, b, truth, len(v), 100 * n1 / len(v), acc, why))
        print(f"{a[5:]} ~ {b[5:]:<22}{truth:<8}{len(v):>7}{100*n1/len(v):>9.1f}%{acc:>8.1f}%  {why}")

    with io.open(OUT_ST, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["start", "end", "truth", "samples", "radar_present_pct",
                    "accuracy_pct", "note"])
        for a, b, truth, n, rp, acc, why in stats:
            w.writerow([a, b, truth, n, f"{rp:.1f}", f"{acc:.1f}", why])

    print()
    print(f"输出: {OUT_TL}")
    print(f"输出: {OUT_ST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
