# -*- coding: utf-8 -*-
"""时间表工具：提供实测空调启停的权威来源。

为什么需要这个模块
------------------
`data/processed/timeline_final.csv` 的 `ac` 列是**人工标注的自由文本**，
实际取值只有 5 种：

    '关'                              整段关闭
    '开'                              整段开启
    '开→02:54关→05:40开→08:42关'       段内多次转移
    '21:46开'                         段首关闭，指定时刻开启
    '开→13:48关'                      段首开启，指定时刻关闭

此前两个脚本用子串 / 前缀判断，都会把开机时长算多：

    calibrate_ac_cooling.py:118   "开" in str(...)           -> 5 种里 4 种判 True
    diagnose_ac_sensor.py:89      str(...).startswith("开")  -> 5 种里 3 种判 True

其中两种写法都错误地把"段内多次转移"的标注整段判为开机，
而两者又各自在相反的边界上出错：子串判定把整段都当开机，
前缀判定则把 '21:46开'（段首关闭，共 269 分钟）整段当作关机。

以 '开→02:54关→05:40开→08:42关'（22:56 ~ 09:16，620 分钟）为例，
其中 02:54~05:40 与 08:42~09:16 合计约 200 分钟（32%）实际关机。

为什么不能靠解析该文本修复
--------------------------
把标注解析成子段后，共得 13 个开机区间，而权威表只有 5 个。原因是
timeline_final.csv 的相邻行之间存在 10 秒到 8 分 43 秒不等的缺口
（行边界按 10 秒取整、且部分时段未被标注覆盖），因此无法靠合并还原。

正确的做法
----------
以 `data/raw/*_events.csv` 记录的启停时刻为准（来源是用户报告 + 雷达反推），
它也是 `joint_fit_thermal.py` 全时段联合标定所用的输入，已被验证：
以此为准标定得到的 tau=108 分钟、稳态温差 5.50 °C 物理自洽。

本模块把该权威启停表固化下来，并提供按时刻查询与按区间遍历的接口。
（该缺陷由团队审计成员 script-auditor 发现）
"""

from datetime import datetime, timedelta

# 实测空调开机时段（权威来源：data/raw/*_events.csv 的用户报告与雷达反推）
_RAW_PERIODS = [
    ("2026-10-14 16:08:10", "2026-10-14 17:29:43"),
    ("2026-10-14 18:41:03", "2026-10-14 19:54:44"),
    ("2026-10-14 20:51:37", "2026-10-15 02:54:00"),
    ("2026-10-15 05:40:00", "2026-10-15 08:42:51"),
    ("2026-10-15 21:46:18", "2026-10-16 13:48:00"),
    # 第 6 段（2026-10-17 补）：events 记 "10-16 16:50:44 用户报告
    # 打开空调（此前 13:48 关闭）"；实测佐证 16:52:53 起室温由
    # 27.7 °C 开始下降，数据终点 17:59:33。此前误记为关机。
    ("2026-10-16 16:50:44", "2026-10-16 17:59:33"),
]

AC_ON_PERIODS = [
    (datetime.strptime(a, "%Y-%m-%d %H:%M:%S"),
     datetime.strptime(b, "%Y-%m-%d %H:%M:%S"))
    for a, b in _RAW_PERIODS
]


def ac_on_at(t):
    """判断时刻 t 空调是否开启。"""
    for a, b in AC_ON_PERIODS:
        if a <= t < b:
            return True
    return False


def ac_on_intervals(start=None, end=None):
    """返回（可裁剪的）开机区间列表。"""
    out = []
    for a, b in AC_ON_PERIODS:
        a2 = max(a, start) if start else a
        b2 = min(b, end) if end else b
        if b2 > a2:
            out.append((a2, b2))
    return out


def total_on_minutes(start=None, end=None):
    """开机总分钟数。"""
    return sum((b - a).total_seconds() for a, b in
               ac_on_intervals(start, end)) / 60.0


def _selftest():
    """自检：接口自洽，并给出关键统计。"""
    ok = True
    print(f"  权威开机区间 {len(AC_ON_PERIODS)} 个")
    for i, (a, b) in enumerate(AC_ON_PERIODS, 1):
        mins = (b - a).total_seconds() / 60
        print(f"    #{i} {a:%m-%d %H:%M:%S} ~ {b:%m-%d %H:%M:%S}"
              f"   {mins:>6.1f} 分钟")
    print(f"  合计开机 {total_on_minutes():.1f} 分钟")

    # 边界检查
    a0, b0 = AC_ON_PERIODS[0]
    checks = [
        ("区间内", ac_on_at(a0 + timedelta(minutes=10)), True),
        ("区间前 1 秒", ac_on_at(a0 - timedelta(seconds=1)), False),
        ("区间后 1 秒", ac_on_at(b0 + timedelta(seconds=1)), False),
        ("末尾区间内", ac_on_at(AC_ON_PERIODS[-1][1] - timedelta(minutes=1)),
         True),
    ]
    for lab, got, want in checks:
        good = got == want
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {lab}: {got}（期望 {want}）")

    # 已知缺陷的历史数字对比
    print()
    print("  对比：旧判定（子串/前缀）会把开机时长算成多少")
    print("    旧判定把 5 个区间 + 3 段多转移标注整段视为开机，")
    print("    其中 '开→02:54关→05:40开→08:42关' 与 '21:46开'、")
    print("    '开→13:48关' 的段首/段尾会多算约 200 分钟。")
    return ok


if __name__ == "__main__":
    print("=== 空调启停权威表自检 ===")
    raise SystemExit(0 if _selftest() else 1)
