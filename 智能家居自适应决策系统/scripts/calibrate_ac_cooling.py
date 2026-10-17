# -*- coding: utf-8 -*-
"""标定空调制冷能力：反推 ac_coef 与 ac_max_cooling。

为什么必须标定
--------------
2026-10-16 的机制诊断发现：仿真里"全程开空调 25°C"的平衡室温为
26.10 °C，高于有人舒适上限 26 °C，即**空调开满也压不住温度**，
导致连"全程开空调"都有 16.7% 的不舒适率。

自洽性检查给出的直接证据：
    仿真最大制冷速率 = 1.2 °C/步 ÷ 0.25 小时 =  4.8 °C/小时
    实测最大降温速率                          = 14.9 °C/小时
即制冷能力被低估约 3.1 倍。

但 ua_coef 与 internal_gain 已用实测标定，若只提高制冷能力而不重新
核对，会破坏已标定的热平衡。因此本次标定在**已标定的 ua_coef 与
internal_gain 基础上**，只反推空调项的两个参数。

标定方法
--------
仿真温度更新式（src/env/simulator.py::_update_temperature）：

    dT = -ua_coef·(T − T_out)                          # 围护传热
         − min(ac_coef·(T − T_set), ac_max_cooling)    # 空调制冷
         + internal_gain·1{有人}                        # 内部热增益

移项可得实测隐含的制冷量：

    cooling_measured = −dT − ua_coef·(T − T_out) + internal_gain·1{有人}

于是对每个"空调开启"的 15 分钟窗口，都能算出一个 (T − T_set, cooling) 点。
再拟合 cooling = min(ac_coef·x, ac_max_cooling) 即可。

关键实测事实
------------
用户在事件记录中明确报告：**空调设定 23 °C 制冷模式**
（`data/raw/start_2026-10-14_events.csv`），因此 T_set = 23 °C。

注意（2026-10-17 更新）：仿真的动作空间原本最低为 25 °C，
无法表达 23 °C 的设定；现已扩展为 [23..29]（ac_idx 1..7），
最低档位即 23 °C，与实测设定一致。

输出
----
docs/空调制冷能力标定.md
models/ac_calibration.json
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

from src.env.config import SimConfig
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _timeline_util import (ac_on_at as _ac_on_at,          # noqa: E402
                          ac_on_intervals as _ac_periods,
                          total_on_minutes as _ac_total_minutes)

OUT_MD = os.path.join(PROJ, "docs", "空调制冷能力标定.md")
OUT_JSON = os.path.join(PROJ, "models", "ac_calibration.json")

DT_MIN = 15          # 仿真步长（分钟）
T_SET = 23.0         # 用户报告的空调设定温度


def load_data():
    """读取全部实测样本，按时间排序去重。"""
    raw = os.path.join(PROJ, "data", "raw")
    files = ["start_2026-10-14.csv", "start_2026-10-15.csv",
             "daily_2026-10-15.csv", "daily_2026-10-16.csv"]
    seen = {}
    for f in files:
        p = os.path.join(raw, f)
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = r.get("pc_time", "")
                if not t:
                    continue
                seen[t] = r          # 同时间戳以后者为准
    rows = []
    for t in sorted(seen):
        r = seen[t]
        try:
            rows.append({
                "t": datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                "temp_in": float(r["temp_c"]) if r.get("temp_c") else None,
                "temp_out": float(r["outdoor_temp_c"])
                if r.get("outdoor_temp_c") else None,
            })
        except (ValueError, KeyError):
            continue
    return [r for r in rows if r["temp_in"] is not None]


def load_timeline():
    """读取人工核对过的时段表（含空调开/关与是否有人）。"""
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
            out.append({
                "start": s, "end": e,
                "occupied": (str(r.get("state", "")).strip().startswith("在房间")),
                "window": ("开" in str(r.get("door_window", ""))),
            })
    return out


def state_at(tl, t):
    for seg in tl:
        if seg["start"] <= t < seg["end"]:
            return seg
    return None


def build_samples(data, tl):
    """构造 (T − T_set, cooling) 样本。

    对每个时间点 t，用其后 DT_MIN 分钟的温度变化计算 dT。
    """
    # 修正（2026-10-17，由团队复核成员 fix-reviewer 指出）：
    # gen4 起模型含常开基础得热 gain_base 与日照项 solar_coef，
    # 反演里若漏掉这两项，每个样本的制冷量会被系统性低估约
    # 0.76~0.99 °C/步，得出"实测制冷能力远低于当前参数"的假象。
    _c = SimConfig()
    ua = _c.ua_coef
    gain = _c.internal_gain
    gain_base = _c.gain_base
    solar_coef = _c.solar_coef
    by_t = {r["t"]: r for r in data}
    samples = []
    for r in data:
        t0 = r["t"]
        t1 = t0 + timedelta(minutes=DT_MIN)
        r1 = by_t.get(t1)
        if r1 is None or r1["temp_in"] is None:
            continue
        seg = state_at(tl, t0)
        if seg is None:
            continue
        # 空调是否开启改用权威启停表判断（2026-10-17 修正）。
        # 原实现取时间表 `ac` 列的子串包含判断，"开" in "关" 之类的
        # 多转移标注会被整段判为开机，导致开机时长虚增约 32%。
        # （由团队审计成员 script-auditor 发现）
        if not _ac_on_at(t0):
            continue
        # 要求整个窗口都在空调开启区间内
        if not _ac_on_at(t1 - timedelta(seconds=1)):
            continue
        # 占用状态仍取自时间表
        seg1 = state_at(tl, t1 - timedelta(seconds=1))
        if seg1 is None:
            continue
        T = r["temp_in"]
        T_out = r["temp_out"] if r["temp_out"] is not None else \
            SimConfig().temp_outdoor_mean
        dT = r1["temp_in"] - T
        # 窗口内若有人则加热，否则不加
        occ = 1.0 if (seg["occupied"] or seg1["occupied"]) else 0.0
        _hf = t0.hour + t0.minute / 60.0
        sun = (math.sin(math.pi * (_hf - 6.0) / 12.0)
               if 6.0 <= _hf <= 18.0 else 0.0)
        cooling = (-dT - ua * (T - T_out) + gain_base
                   + solar_coef * sun + gain * occ)
        samples.append({
            "t": t0.strftime("%Y-%m-%d %H:%M:%S"),
            "T": T, "T_out": T_out, "dT": dT,
            "x": T - T_SET, "cooling": cooling, "occ": occ,
        })
    return samples


def fit(samples):
    """拟合 cooling = min(ac_coef·x, ac_max)。网格搜索 + 最小二乘。"""
    import numpy as np
    xs = np.array([s["x"] for s in samples])
    ys = np.array([s["cooling"] for s in samples])
    best = None
    for ac_coef in np.arange(0.05, 2.01, 0.01):
        cap = np.minimum(ac_coef * np.maximum(xs, 0.0), 1e9)
        # 对每个候选 ac_coef，最优 ac_max 是使误差最小的截断值
        for ac_max in np.arange(0.2, 6.01, 0.05):
            pred = np.minimum(ac_coef * np.maximum(xs, 0.0), ac_max)
            err = float(np.mean((pred - ys) ** 2))
            if best is None or err < best[0]:
                best = (err, float(ac_coef), float(ac_max))
    return best


def main():
    data = load_data()
    tl = load_timeline()
    print("=" * 74)
    print(" 空调制冷能力标定")
    print("=" * 74)
    print(f"  实测样本 {len(data)} 条")
    print(f"  时段表 {len(tl)} 段（仅用于占用状态）")
    print(f"  空调启动用权威启停表：{_ac_periods()} 段，"
          f"合计开机 {_ac_total_minutes():.1f} 分钟")
    print(f"  设定温度（用户报告）：{T_SET} °C")
    print()

    samples = build_samples(data, tl)
    print(f"  可用标定样本（空调开启且窗口完整）：{len(samples)} 个")
    if not samples:
        print("  ⚠️ 无可用样本，退出")
        return 1
    xs = [s["x"] for s in samples]
    ys = [s["cooling"] for s in samples]
    print(f"  (T − T_set) 范围：{min(xs):.2f} ~ {max(xs):.2f} °C")
    print(f"  隐含制冷量范围：{min(ys):.3f} ~ {max(ys):.3f} °C/步")
    print()

    err, ac_coef, ac_max = fit(samples)
    print("=" * 74)
    print(" 拟合结果")
    print("=" * 74)
    print(f"  ac_coef        = {ac_coef:.3f}   （当前仿真值 0.30）")
    print(f"  ac_max_cooling = {ac_max:.2f} °C/步  （当前仿真值 1.2）")
    print(f"  RMSE           = {err ** 0.5:.4f} °C/步")
    print()
    cur_hour = SimConfig().ac_max_cooling / (DT_MIN / 60.0)
    new_hour = ac_max / (DT_MIN / 60.0)
    print(f"  最大制冷速率：仿真 {cur_hour:.1f} °C/小时 "
          f"-> 标定后 {new_hour:.1f} °C/小时")
    print(f"  实测最大降温：14.9 °C/小时（含同时得热，故应略低于空调净制冷）")
    print()

    # 复算平衡温度
    c = SimConfig()
    ua, gain = c.ua_coef, c.internal_gain
    Tout = c.temp_outdoor_mean
    for name, ac in [("当前 ac_coef", c.ac_coef), ("标定 ac_coef", ac_coef)]:
        # 用 25°C 设定（仿真最低档）估算
        Teq = (ua * Tout + gain + ac * 25.0) / (ua + ac)
        print(f"  {name}：空调全开@25°C 的平衡室温 = {Teq:.2f} °C "
              f"（{'压不住' if Teq > 26 else '可压住'}）")
    print()

    out = {
        "T_set": T_SET, "dt_min": DT_MIN,
        "n_samples": len(samples),
        "fitted": {"ac_coef": ac_coef, "ac_max_cooling": ac_max,
                   "rmse": err ** 0.5},
        "current": {"ac_coef": SimConfig().ac_coef,
                    "ac_max_cooling": SimConfig().ac_max_cooling},
        "samples": samples,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 空调制冷能力标定", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 实测样本：{len(data)} 条（49.9 小时）",
          f"- 标定样本：{len(samples)} 个 15 分钟窗口（空调开启且窗口完整）",
          f"- 空调设定温度：**{T_SET} °C**（用户报告，制冷模式）", "",
          "## 一、为什么必须标定", "",
          "机制诊断发现：仿真中\"全程开空调 25 °C\"的平衡室温为 "
          "**26.10 °C**，高于有人舒适上限 26 °C，",
          "即空调开满也压不住温度，导致连该基线都有 16.7% 的不舒适率。", "",
          "自洽性检查给出的直接证据：", "",
          "```",
          f"仿真最大制冷速率 = {SimConfig().ac_max_cooling} °C/步 ÷ 0.25 小时"
          f" = {cur_hour:.1f} °C/小时",
          "实测最大降温速率                          = 14.9 °C/小时",
          "```", "",
          "## 二、标定方法", "",
          "仿真温度更新式：", "",
          "```",
          "dT = -ua_coef·(T − T_out)                        # 围护传热",
          "     − min(ac_coef·(T − T_set), ac_max_cooling)  # 空调制冷",
          "     + internal_gain·1{有人}                      # 内部热增益",
          "```", "",
          "移项得实测隐含制冷量：", "",
          "```",
          "cooling = −dT − ua_coef·(T − T_out) + internal_gain·1{有人}",
          "```", "",
          "对每个空调开启的 15 分钟窗口计算一个 (T − T_set, cooling) 点，",
          "再拟合 `cooling = min(ac_coef·x, ac_max_cooling)`。", "",
          "**注意**：`ua_coef` 与 `internal_gain` 保持已标定值不变，",
          "本次只反推空调项的两个参数，以免破坏已标定的热平衡。", "",
          "## 三、标定数据概况", "",
          f"- (T − T_set) 范围：{min(xs):.2f} ~ {max(xs):.2f} °C",
          f"- 隐含制冷量范围：{min(ys):.3f} ~ {max(ys):.3f} °C/步", "",
          "## 四、拟合结果", "",
          "| 参数 | 当前仿真值 | **标定值** | 倍数 |",
          "|---|---|---|---|",
          f"| `ac_coef` | {SimConfig().ac_coef} | **{ac_coef:.3f}** | "
          f"{ac_coef/SimConfig().ac_coef:.1f}× |",
          f"| `ac_max_cooling` | {SimConfig().ac_max_cooling} | "
          f"**{ac_max:.2f}** | {ac_max/SimConfig().ac_max_cooling:.1f}× |",
          "",
          f"拟合 RMSE = {err ** 0.5:.4f} °C/步。", "",
          "对应的最大制冷速率：", "",
          "```",
          f"仿真   {SimConfig().ac_max_cooling} °C/步 -> {cur_hour:.1f} °C/小时",
          f"标定后 {ac_max:.2f} °C/步 -> {new_hour:.1f} °C/小时",
          "实测   最大降温 14.9 °C/小时（含同时得热，净制冷应更高）",
          "```", "",
          "## 五、重要说明：仿真的动作空间无法表达实际设定", "",
          f"实测中用户把空调设为 **{T_SET} °C**，而仿真动作空间的最低档位是 "
          "**25 °C**。",
          "这意味着仿真无法复现实际的最冷设定，属于建模限制，需在报告中说明。", "",
          ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
