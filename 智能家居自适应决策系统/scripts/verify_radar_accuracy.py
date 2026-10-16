# -*- coding: utf-8 -*-
"""雷达准确率精确复核（以用户报告的真值为基准）。

用户意见
--------
· 只有【睡眠】时段雷达可能出问题；其他时段（清醒在房间 / 确实外出）应当正常。
· 精确作息：10/16 入睡 05:06，醒来 13:28。

真值分三类
----------
1. 外出（expected 无人）
2. 在房间·清醒（expected 有人）
3. 在房间·睡眠（expected 有人，但已知雷达可能漏检）

额外：门开状态的说明（洗澡期间门开着，雷达会探测到门外）。
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

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
RAW = os.path.join(PROJ, "data", "raw")
OUT = os.path.join(PROJ, "docs", "雷达准确率复核.md")

FILES = ["start_2026-10-14.csv", "start_2026-10-15.csv",
         "daily_2026-10-15.csv", "daily_2026-10-16.csv"]

# ---- 真值区间（用户报告 + 事件核对）----
# (起, 止, 类别, 说明)
TRUTH = [
    # ---------- 10/14 ----------
    ("2026-10-14 16:08", "2026-10-14 17:29", "在房间·清醒", "在房间，空调开"),
    ("2026-10-14 17:29", "2026-10-14 17:48", "外出", "出门"),
    ("2026-10-14 17:48", "2026-10-14 19:54", "在房间·清醒", "返回（17:48~18:41 空调关）"),
    ("2026-10-14 19:54", "2026-10-14 20:51", "外出", "开窗实验期间离开"),
    ("2026-10-14 20:51", "2026-10-14 21:07", "在房间·清醒", "返回关窗开空调"),
    ("2026-10-14 21:07", "2026-10-14 21:45", "外出·门开", "洗澡（房门开着，雷达可见门外）"),
    ("2026-10-14 21:45", "2026-10-14 22:56", "在房间·清醒", "回房，未睡"),
    ("2026-10-14 22:56", "2026-10-15 05:40", "在房间·睡眠", "入睡（02:54 空调定时关）"),
    # ---------- 10/15 ----------
    ("2026-10-15 05:40", "2026-10-15 09:16", "在房间·清醒", "醒来开空调、起床活动"),
    ("2026-10-15 09:16", "2026-10-15 17:06", "外出", "出远门，全天不在家"),
    ("2026-10-15 17:06", "2026-10-15 17:13", "家人短暂进入", "母亲进门约 2 分钟"),
    ("2026-10-15 17:13", "2026-10-15 21:05", "外出", "尚未回家"),
    ("2026-10-15 21:05", "2026-10-15 21:07", "在房间·清醒", "刚回家"),
    ("2026-10-15 21:07", "2026-10-15 21:45", "外出·门开", "洗澡（房门开着）"),
    ("2026-10-15 21:45", "2026-10-16 05:06", "在房间·清醒", "回房，清醒到入睡"),
    # ---------- 10/16 ----------
    ("2026-10-16 05:06", "2026-10-16 13:28", "在房间·睡眠", "入睡（用户报告 05:06~13:28）"),
    ("2026-10-16 13:28", "2026-10-16 14:09", "在房间·清醒", "醒来、开窗帘、关空调"),
    ("2026-10-16 14:09", "2026-10-16 15:55", "外出", "出门"),
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


def cls_of(t):
    s = t.strftime("%Y-%m-%d %H:%M")
    for a, b, c, why in TRUTH:
        if a <= s < b:
            return c, why
    return "未标注", ""


def main():
    rows = load()
    print(f"载入 {len(rows)} 行")

    # 分类统计
    grp = {}
    for r in rows:
        c, why = cls_of(r["_t"])
        g = grp.setdefault(c, {"n": 0, "p": 0, "det": 0, "pir": 0, "why": set()})
        g["n"] += 1
        d = str(r.get("radar", "0")).strip() == "1"
        if d:
            g["det"] += 1
        if str(r.get("pir1", "0")).strip() == "1" or str(r.get("pir2", "0")).strip() == "1":
            g["pir"] += 1
        g["why"].add(why)

    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("# 雷达准确率复核")
    P()
    P(f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P(f"- 样本：{len(rows)} 行")
    P("- 真值来源：用户报告 + 事件文件核对")
    P("- 精确作息：10/16 入睡 05:06、醒来 13:28")
    P()
    P("## 分类准确率")
    P()
    P("| 真值类别 | 样本 | 雷达报有人 | 雷达报无人 | 准确率 |")
    P("|---|---|---|---|---|")
    order = ["在房间·清醒", "在房间·睡眠", "外出", "外出·门开",
             "家人短暂进入", "未标注"]
    for c in order:
        if c not in grp:
            continue
        g = grp[c]
        n = g["n"]
        det = g["det"]
        miss = n - det
        # 「有人」类别准确率 = 报有人的比例；「无人」类别准确率 = 报无人的比例
        if c.startswith("在房间") or c == "家人短暂进入":
            acc = 100 * det / n
            expect = "有人"
        else:
            acc = 100 * miss / n
            expect = "无人"
        P(f"| {c} | {n} | {det} | {miss} | **{acc:.1f}%**（应报{expect}） |")

    # 逐小时（在房间·清醒）
    P()
    P("## 在房间·清醒 逐小时明细")
    P()
    P("| 小时 | 样本 | 雷达有人 | 准确率 | PIR触发 | 备注 |")
    P("|---|---|---|---|---|---|")
    buckets = {}
    for r in rows:
        c, why = cls_of(r["_t"])
        if c != "在房间·清醒":
            continue
        k = r["_t"].strftime("%m-%d %H")
        buckets.setdefault(k, []).append(r)
    for k in sorted(buckets):
        v = buckets[k]
        det = sum(1 for x in v if str(x.get("radar", "0")).strip() == "1")
        pir = sum(1 for x in v
                  if str(x.get("pir1", "0")).strip() == "1"
                  or str(x.get("pir2", "0")).strip() == "1")
        flag = "" if det / len(v) >= 0.8 else " ⚠️"
        P(f"| {k} | {len(v)} | {det} | {100*det/len(v):.1f}%{flag} | {pir} | |")

    P()
    P("## 在房间·睡眠 逐小时明细")
    P()
    P("| 小时 | 样本 | 雷达有人 | 准确率 | PIR触发 | 备注 |")
    P("|---|---|---|---|---|---|")
    buckets = {}
    for r in rows:
        c, why = cls_of(r["_t"])
        if c != "在房间·睡眠":
            continue
        k = r["_t"].strftime("%m-%d %H")
        buckets.setdefault(k, []).append(r)
    for k in sorted(buckets):
        v = buckets[k]
        det = sum(1 for x in v if str(x.get("radar", "0")).strip() == "1")
        pir = sum(1 for x in v
                  if str(x.get("pir1", "0")).strip() == "1"
                  or str(x.get("pir2", "0")).strip() == "1")
        flag = "" if det / len(v) >= 0.8 else " ⚠️"
        P(f"| {k} | {len(v)} | {det} | {100*det/len(v):.1f}%{flag} | {pir} | |")

    io.open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print()
    print(f"输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
