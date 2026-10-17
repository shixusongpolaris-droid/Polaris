# -*- coding: utf-8 -*-
"""强制空调复演验证：判断仿真是否需要扩展最低设定温度。

问题
----
仿真的空调档位是 `[25, 26, 27, 28, 29]`，最低只能设 25 °C；
而实测中用户把空调设为 **23 °C**（见 events.csv 的用户报告）。
因此仿真无法表达真实的最冷档位。

更严重的后果：规则策略的设定温度扫描里，22/23/24 °C 全部被
`ac_idx_for` 映射到 25 °C，导致"降低设定温度"这条节能路径在
仿真中根本不存在。

本脚本给出客观判据
------------------
**用实测的空调启停序列强制驱动仿真**（策略不参与），
比较"仿真室温"与"实测室温"的吻合程度：

    若把最低档设为 23 °C 后吻合度明显提升，则应扩展动作空间。

做法
----
对每个候选最低设定温度（23 / 24 / 25 °C）：
  · 构造复演环境（实测室外温度、占用、光照）
  · 每个 15 分钟步按实测启停表强制空调开/关，设定值取候选温度
  · 记录仿真室温序列
  · 与实测室温比较 RMSE、偏差、极值

注意
----
`encode_action` / `decode_action` 只依赖光照与通风档数，与
`ac_setpoints` 的长度无关，因此可逐环境修改设定值而不影响动作编码。

输出
----
docs/空调最低档位验证.md
models/forced_ac_validation.json
"""

import io
import json
import os
import sys
import types
from datetime import datetime, timedelta
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402

OUT_JSON = os.path.join(PROJ, "models", "forced_ac_validation.json")
OUT_MD = os.path.join(PROJ, "docs", "空调最低档位验证.md")

DT_MIN = 15
AC_OFF = 0
CANDIDATES = [23.0, 24.0, 25.0]

# 实测空调启停（来自 data/raw/*_events.csv 的用户报告）
AC_SCHEDULE = [
    ("2026-10-14 16:08:10", "2026-10-14 17:29:43"),
    ("2026-10-14 18:41:03", "2026-10-14 19:54:44"),
    ("2026-10-14 20:51:37", "2026-10-15 02:54:00"),
    ("2026-10-15 05:40:00", "2026-10-15 08:42:51"),
    ("2026-10-15 21:46:18", "2026-10-16 13:48:00"),
    ("2026-10-16 16:50:44", "2026-10-16 17:59:33"),
]


def load_series():
    import csv
    bf = {}
    p = os.path.join(PROJ, "data", "processed",
                     "outdoor_temp_backfill.csv")
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    bf[r["hour"].replace("T", " ")[:13]] = \
                        float(r["outdoor_temp_c"])
                except (ValueError, KeyError):
                    continue
    occ_segs = []
    p = os.path.join(PROJ, "data", "processed", "timeline_final.csv")
    with io.open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                s = datetime.strptime(r["start"][:19], "%Y-%m-%d %H:%M:%S")
                e = datetime.strptime(r["end"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            occ_segs.append((s, e,
                             str(r.get("state", "")).strip()
                             .startswith("在房间")))
    raw = os.path.join(PROJ, "data", "raw")
    seen = {}
    for f in ["start_2026-10-14.csv", "start_2026-10-15.csv",
              "daily_2026-10-15.csv", "daily_2026-10-16.csv"]:
        fp = os.path.join(raw, f)
        if os.path.exists(fp):
            with io.open(fp, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("pc_time"):
                        seen[r["pc_time"]] = r
    pts = []
    for t in sorted(seen):
        r = seen[t]
        try:
            T = float(r["temp_c"])
        except (ValueError, KeyError, TypeError):
            continue
        v = r.get("outdoor_temp_c")
        tout = float(v) if v not in (None, "") else bf.get(t[:13])
        try:
            light = float(r.get("light_lux") or 0.0)
        except (ValueError, TypeError):
            light = 0.0
        pts.append((datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S"),
                    T, tout, light))
    t0 = pts[0][0]
    grid = {}
    for (t, T, tout, light) in pts:
        k = int((t - t0).total_seconds() // (DT_MIN * 60))
        g = grid.setdefault(k, {"T": [], "tout": [], "light": []})
        g["T"].append(T)
        if tout is not None:
            g["tout"].append(tout)
        g["light"].append(light)
    series = []
    for k in sorted(grid):
        g = grid[k]
        tk = t0 + timedelta(minutes=k * DT_MIN)
        series.append({
            "t": tk,
            "T_in": mean(g["T"]),
            "T_out": mean(g["tout"]) if g["tout"] else None,
            "light": mean(g["light"]),
            "occ": any(a <= tk < b and o for a, b, o in occ_segs),
            "ac": any(datetime.strptime(a, "%Y-%m-%d %H:%M:%S") <= tk
                      < datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
                      for a, b in AC_SCHEDULE),
        })
    last = None
    for s in series:
        if s["T_out"] is None:
            s["T_out"] = last if last is not None else 22.0
        last = s["T_out"]
    return series, t0


def patch(sim, series, light_ratio):
    n = len(series)
    cfg = sim.sim_cfg

    def _update_outdoor(self):
        i = min(self.step_count, n - 1)
        self._temp_out = series[i]["T_out"]
        self._light_out = min(series[i]["light"] / max(light_ratio, 1e-6),
                              cfg.light_outdoor_max)

    def _update_occupancy(self, is_weekend):
        i = min(self.step_count, n - 1)
        o = 1 if series[i]["occ"] else 0
        self.state.occ_a, self.state.occ_b = (o, 0)
        self.state.occ_dist = 2 if o else 0

    sim._update_outdoor = types.MethodType(_update_outdoor, sim)
    sim._update_occupancy = types.MethodType(_update_occupancy, sim)


def run_forced(series, t0, setpoint):
    env = SmartHomeEnv(rew_cfg=RewardConfig(), episode_steps=len(series),
                       start_datetime=t0)
    cfg = env.act_cfg
    # 把候选设定值放到第一位，其余保留（去重）
    rest = [s for s in [25.0, 26.0, 27.0, 28.0, 29.0]
            if abs(s - setpoint) > 1e-9]
    cfg.ac_setpoints = [setpoint] + rest
    env.sim.act_cfg = cfg
    patch(env.sim, series, env.sim_cfg.light_window_ratio)

    env.reset(seed=1000)
    env.sim.state.temp_in = series[0]["T_in"]
    sim_temps = []
    while True:
        i = min(env.sim.step_count, len(series) - 1)
        if series[i]["ac"]:
            ac_idx = 1                       # 第一位即候选设定值
            light = 1 if series[i]["light"] < 300 else 0
            a = encode_action(ac_idx, light, 0, cfg)
        else:
            a = encode_action(AC_OFF, 0, 0, cfg)
        _, _, term, trunc, info = env.step(a)
        sim_temps.append(env.sim.state.temp_in)
        if term or trunc:
            break
    env.close()
    return sim_temps


def main():
    series, t0 = load_series()
    print("=" * 84)
    print(" 空调最低档位验证（用实测启停强制驱动）")
    print("=" * 84)
    print(f"  实测序列 {len(series)} 步（{len(series)*DT_MIN/60:.1f} 小时）")
    meas = [s["T_in"] for s in series]
    print(f"  实测室温 {min(meas):.2f} ~ {max(meas):.2f} °C，"
          f"均值 {mean(meas):.2f}，标准差 {pstdev(meas):.3f}")
    print()

    results = {}
    print(f"  {'最低档':>7}{'仿真均值':>10}{'仿真范围':>16}{'RMSE':>9}"
          f"{'偏差':>9}{'相关':>8}")
    print("  " + "-" * 62)
    for sp in CANDIDATES:
        sim = run_forced(series, t0, sp)
        m = np.array(meas[:len(sim)])
        s = np.array(sim)
        rmse = float(np.sqrt(np.mean((s - m) ** 2)))
        bias = float(np.mean(s - m))
        corr = float(np.corrcoef(s, m)[0, 1]) if s.std() > 0 else 0.0
        results[str(sp)] = {
            "mean": float(s.mean()), "min": float(s.min()),
            "max": float(s.max()), "rmse": rmse, "bias": bias,
            "corr": corr,
        }
        print(f"  {sp:>6.0f}°C{s.mean():>10.2f}"
              f"{f'{s.min():.1f}~{s.max():.1f}':>16}{rmse:>9.3f}"
              f"{bias:>+9.3f}{corr:>8.3f}")
    print()

    best = min(results.items(), key=lambda kv: kv[1]["rmse"])
    print(f"  最优最低档 = {best[0]} °C（RMSE {best[1]['rmse']:.3f}）")
    print(f"  相比 25 °C：RMSE "
          f"{results['25.0']['rmse']:.3f} -> {best[1]['rmse']:.3f}，"
          f"改善 {(1-best[1]['rmse']/results['25.0']['rmse'])*100:.1f}%")

    out = {"measured": {"mean": mean(meas), "min": min(meas),
                        "max": max(meas), "sd": pstdev(meas)},
           "results": results, "best": best[0]}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 空调最低档位验证", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 实测序列：{len(series)} 个 15 分钟步"
          f"（{len(series)*DT_MIN/60:.1f} 小时）", "",
          "## 一、问题", "",
          "仿真的空调档位是 `[25, 26, 27, 28, 29]`，最低只能设 25 °C；",
          "而实测中用户把空调设为 **23 °C**。",
          "因此仿真无法表达真实的最冷档位，且规则策略的设定温度扫描中",
          "22/23/24 °C 会被 `ac_idx_for` 全部映射到 25 °C，",
          "使\"降低设定温度\"这条路径在仿真中不存在。", "",
          "## 二、判据", "",
          "**用实测的空调启停序列强制驱动仿真**（策略不参与），",
          "比较仿真室温与实测室温的吻合度。若降低最低档后吻合度明显提升，",
          "则应扩展动作空间。", "",
          "## 三、实测基准", "",
          f"- 室温：{min(meas):.2f} ~ {max(meas):.2f} °C，"
          f"均值 {mean(meas):.2f}，标准差 {pstdev(meas):.3f}", "",
          "## 四、结果", "",
          "| 最低设定档 | 仿真均值 | 仿真范围 | RMSE | 偏差 | 相关系数 |",
          "|---|---|---|---|---|---|"]
    for sp in CANDIDATES:
        r = results[str(sp)]
        md.append(f"| {sp:.0f} °C | {r['mean']:.2f} | "
                  f"{r['min']:.1f}~{r['max']:.1f} | {r['rmse']:.3f} | "
                  f"{r['bias']:+.3f} | {r['corr']:.3f} |")
    md += ["", f"**最优最低档 = {best[0]} °C**（RMSE {best[1]['rmse']:.3f}，"
           f"相比 25 °C 改善 "
           f"{(1-best[1]['rmse']/results['25.0']['rmse'])*100:.1f}%）", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
