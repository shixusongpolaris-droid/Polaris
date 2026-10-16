# -*- coding: utf-8 -*-
"""读取全部实测数据，整理成"时间 + 人员状态"总表。

方法
----
1. 读取 data/raw 下全部采集文件（按时间排序去重）
2. 用多传感器信号分段：
     · radar       雷达检测到有人
     · pir1/pir2   人体活动触发
     · light_lux   光照（变化剧烈 => 有人在动/开关门）
     · 光照标准差   作为"活动"的辅助判据
3. 依据信号组合推断每个时段的占用状态：
     在房间(活动)   雷达有人 且 PIR 触发频繁
     在房间(静坐)   雷达有人 但 PIR 很少
     睡眠(规则补充) 用户报告的睡眠时段（雷达可能漏检）
     无人           雷达无人 且 PIR 长时间无触发
4. 叠加用户报告的事件作为"基准真值"，标注来源

输出
----
docs/时间与人员状态总表.md
data/processed/occupancy_timeline.csv
"""

import csv
import io
import os
import statistics
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
RAW = os.path.join(PROJ, "data", "raw")
OUT_MD = os.path.join(PROJ, "docs", "时间与人员状态总表.md")
OUT_CSV = os.path.join(PROJ, "data", "processed", "occupancy_timeline.csv")

FILES = [
    "start_2026-10-14.csv",
    "start_2026-10-15.csv",
    "daily_2026-10-15.csv",
    "daily_2026-10-16.csv",
]

# ---- 用户报告的基准真值（我/家人在房间的时段）----
# (开始, 结束, 状态, 依据)
TRUTH = [
    ("2026-10-14 16:08", "2026-10-14 17:29", "在房间", "用户报告：在房间，空调开着"),
    ("2026-10-14 17:29", "2026-10-14 17:48", "不在房间", "用户报告：第三次出门"),
    ("2026-10-14 17:48", "2026-10-14 19:54", "在房间", "用户报告：返回房间并开空调"),
    ("2026-10-14 19:54", "2026-10-14 20:51", "不在房间", "用户报告：关空调开窗后离开"),
    ("2026-10-14 20:51", "2026-10-14 21:07", "在房间", "用户报告：返回房间关窗开空调"),
    ("2026-10-14 21:07", "2026-10-14 21:45", "不在房间", "用户报告：出去洗澡"),
    ("2026-10-14 21:45", "2026-10-15 05:00", "在房间", "用户报告：回房，后入睡"),
    ("2026-10-15 05:00", "2026-10-15 05:40", "在房间", "睡眠中"),
    ("2026-10-15 05:40", "2026-10-15 09:16", "在房间", "用户报告：醒来开空调，起床活动"),
    ("2026-10-15 09:16", "2026-10-15 17:06", "不在房间", "用户报告：出远门，全天不在家"),
    ("2026-10-15 17:06", "2026-10-15 17:13", "家人短暂进入", "用户报告：母亲进门约 2 分钟"),
    ("2026-10-15 17:13", "2026-10-15 21:05", "不在房间", "用户报告：尚未回家"),
    ("2026-10-15 21:05", "2026-10-15 21:07", "在房间", "用户报告：刚回家"),
    ("2026-10-15 21:07", "2026-10-15 21:45", "不在房间", "用户报告：出去洗澡（房门开着）"),
    ("2026-10-15 21:45", "2026-10-16 05:00", "在房间", "用户报告：回房，清醒至凌晨 5 点"),
    ("2026-10-16 05:00", "2026-10-16 13:30", "睡眠(在房间)", "用户报告：凌晨 5 点睡到下午 1:30"),
    ("2026-10-16 13:30", "2026-10-16 14:09", "在房间", "用户报告：醒来，开窗帘，关空调"),
    ("2026-10-16 14:09", "2026-10-16 15:40", "不在房间", "数据推断 + 用户确认：出门"),
]


def load_all():
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
            r["_file"] = fn
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    # 去重（同一时刻只留一条）
    seen = set()
    out = []
    for r in rows:
        k = r["_t"]
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def fnum(r, k):
    try:
        return float(r[k])
    except (TypeError, ValueError, KeyError):
        return None


def fbool(r, k):
    return str(r.get(k, "0")).strip() == "1"


def truth_of(t):
    s = t.strftime("%Y-%m-%d %H:%M")
    for a, b, st, why in TRUTH:
        if a <= s < b:
            return st, why
    return "", ""


def main():
    rows = load_all()
    if not rows:
        print("无数据")
        return 1

    print(f"载入 {len(rows)} 行")
    print(f"范围 {rows[0]['_t']} ~ {rows[-1]['_t']}")

    # ---- 以 10 分钟为粒度聚合，便于阅读 ----
    BIN = 10  # 分钟
    bins = {}
    for r in rows:
        t = r["_t"]
        key = t.replace(minute=(t.minute // BIN) * BIN, second=0, microsecond=0)
        bins.setdefault(key, []).append(r)

    table = []
    for key in sorted(bins):
        v = bins[key]
        n = len(v)
        n_radar = sum(1 for x in v if fbool(x, "radar"))
        n_pir = sum(1 for x in v if fbool(x, "pir1") or fbool(x, "pir2"))
        L = [fnum(x, "light_lux") for x in v]
        L = [x for x in L if x is not None]
        l_sd = statistics.pstdev(L) if len(L) > 1 else 0.0
        l_mean = statistics.mean(L) if L else 0.0
        T = [fnum(x, "temp_c") for x in v]
        T = [x for x in T if x is not None]
        H = [fnum(x, "humidity_pct") for x in v]
        H = [x for x in H if x is not None]
        st, why = truth_of(key)
        table.append({
            "time": key,
            "n": n,
            "radar_pct": 100 * n_radar / n,
            "pir_cnt": n_pir,
            "light_mean": l_mean,
            "light_sd": l_sd,
            "temp": statistics.mean(T) if T else None,
            "hum": statistics.mean(H) if H else None,
            "truth": st,
            "why": why,
        })

    # ---- 会话级分段（把连续的相同状态合并）----
    # 判定信号状态
    def sig_state(b):
        if b["radar_pct"] >= 60 and b["pir_cnt"] >= 3:
            return "在房间(活动)"
        if b["radar_pct"] >= 60:
            return "在房间(静坐)"
        if b["pir_cnt"] >= 3 or b["light_sd"] > 40:
            return "有人在动"
        return "无信号"

    for b in table:
        b["sig"] = sig_state(b)

    # ---- 生成 Markdown ----
    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("# 时间与人员状态总表")
    P()
    P(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    P(f"- 数据总量：{len(rows)} 行")
    P(f"- 时间范围：{rows[0]['_t']:%Y-%m-%d %H:%M} ~ {rows[-1]['_t']:%Y-%m-%d %H:%M}")
    P(f"- 聚合粒度：{BIN} 分钟")
    P()
    P("---")
    P()
    P("## 一、按用户报告整理的作息总表（权威）")
    P()
    P("| 起始 | 结束 | 时长 | 人员状态 | 依据 |")
    P("|---|---|---|---|---|")
    for a, b, st, why in TRUTH:
        t1 = datetime.strptime(a, "%Y-%m-%d %H:%M")
        t2 = datetime.strptime(b, "%Y-%m-%d %H:%M")
        dur = (t2 - t1).total_seconds() / 3600
        P(f"| {a} | {b} | {dur:.1f} 小时 | **{st}** | {why} |")
    P()
    P("---")
    P()
    P("## 二、传感器信号细表（每 10 分钟）")
    P()
    P("列说明：`雷达%` = 该 10 分钟内雷达报「有人」的比例（你确证在房间时若低于 100% 即为漏检）；")
    P("`PIR次数` = 该时段内 PIR 活动触发次数；`光照SD` = 光照标准差（>40 表示有人在动或开关门）。")
    P()
    P("| 时间 | 样本 | 雷达% | PIR次数 | 光照均值 | 光照SD | 温度 | 湿度 | 用户报告状态 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for b in table:
        tstr = b["time"].strftime("%m-%d %H:%M")
        tp = f"{b['temp']:.1f}" if b["temp"] is not None else "-"
        hp = f"{b['hum']:.0f}" if b["hum"] is not None else "-"
        flag = ""
        if b["truth"] and "不在" not in b["truth"] and b["radar_pct"] < 60:
            flag = " ⚠️漏检"
        P(f"| {tstr} | {b['n']} | {b['radar_pct']:.0f}% | {b['pir_cnt']} | "
          f"{b['light_mean']:.0f} | {b['light_sd']:.1f} | {tp} | {hp} | "
          f"{b['truth'] or '—'}{flag} |")

    # ---- 统计 ----
    P()
    P("---")
    P()
    P("## 三、雷达准确率统计（以用户报告为真值）")
    P()
    P("| 用户状态 | 样本数 | 雷达报有人占比 | 结论 |")
    P("|---|---|---|---|")
    groups = {}
    for b in table:
        st = b["truth"] or "未标注"
        g = groups.setdefault(st, [0, 0.0])
        g[0] += b["n"]
        g[1] += b["radar_pct"] * b["n"]
    for st in sorted(groups):
        n, wsum = groups[st]
        P(f"| {st} | {n} | {wsum/n:.1f}% | |")

    io.open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # CSV
    with io.open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "samples", "radar_pct", "pir_count",
                    "light_mean", "light_sd", "temp_c", "humidity_pct",
                    "user_reported_state", "basis"])
        for b in table:
            w.writerow([b["time"].strftime("%Y-%m-%d %H:%M:%S"), b["n"],
                        f"{b['radar_pct']:.1f}", b["pir_cnt"],
                        f"{b['light_mean']:.1f}", f"{b['light_sd']:.2f}",
                        "" if b["temp"] is None else f"{b['temp']:.2f}",
                        "" if b["hum"] is None else f"{b['hum']:.1f}",
                        b["truth"], b["why"]])

    print()
    print(f"输出: {OUT_MD}")
    print(f"输出: {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
