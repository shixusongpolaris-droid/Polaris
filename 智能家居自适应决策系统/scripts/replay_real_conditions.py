# -*- coding: utf-8 -*-
"""真实工况复演验证：用实测条件驱动仿真，检验结论是否仍然成立。

为什么需要这个验证
------------------
项目此前的全部结论都建立在**合成场景**上：仿真自行生成典型上班族作息、
正弦室外温度、以及固定参数的空调控制。审阅时最可能的质疑是：
"你的策略在真实工况下还有效吗？"

本脚本把**实测的 49.9 小时真实条件**注入仿真，替换掉三个合成外生输入：

    · 室外温度  -> 实测值（10-15/10-16）与 Open-Meteo 回填值（10-14）
    · 人员占用  -> 人工核对过的 `timeline_final.csv`
    · 室内光照  -> 实测光照（换算为等效室外光照，走正常光照通道）

**空调、照明、通风仍由策略控制** —— 这正是要检验的部分。
热工模型使用 gen3 联合标定参数（全时段 RMSE 1.728 °C）。

因此这既是"真实条件复演"，也是**跨工况迁移测试**：策略在合成场景下
训练，在真实条件下评估。

做法
----
用实例方法打补丁（monkey-patch）替换 `HomeSimulator` 的三个外生输入方法，
不改动库代码：

    sim._update_outdoor     -> 读实测室外温度
    sim._update_occupancy   -> 读实测占用
    sim._update_light       -> 读实测光照

评估口径
--------
默认 `RewardConfig`（未启用分时电价），故"电费"数值 = 电量(kWh)。
规则策略扫设定温度 22~29 °C；强化学习用已训练的 8 档权重 × 5 种子模型。

输出
----
docs/真实工况复演验证.md
models/replay_validation/results.json
"""

import csv
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
from src.env.config import ActionConfig                # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "replay_validation")
OUT_MD = os.path.join(PROJ, "docs", "真实工况复演验证.md")

DT_MIN = 15
AC_OFF = 0
RULE_SETPOINTS = [22, 23, 24, 25, 26, 27, 28, 29]
SCALES = [10, 20, 25, 30, 40, 50, 60, 100]
SEEDS = [42, 66, 2026, 7, 123]

MODEL_DIRS = [os.path.join(PROJ, "models", "energy_scale_fine"),
              os.path.join(PROJ, "models", "energy_scale_verify")]


# ============================================================
# 1. 构造实测时间序列（15 分钟格点）
# ============================================================
def load_series():
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
            occ_segs.append((s, e, str(r.get("state", "")).strip().startswith("在房间")))

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
    if not pts:
        return [], None

    # 归并到 15 分钟格点
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
        occ = any(a <= tk < b and o for a, b, o in occ_segs)
        series.append({
            "t": tk,
            "T_out": mean(g["tout"]) if g["tout"] else None,
            "T_in": mean(g["T"]),
            "light": mean(g["light"]),
            "occ": occ,
        })
    # 填补缺失的室外温度
    last = None
    for s in series:
        if s["T_out"] is None:
            s["T_out"] = last if last is not None else 22.0
        last = s["T_out"]
    return series, t0


# ============================================================
# 2. 打补丁：把实测条件注入仿真
# ============================================================
def patch(sim, series):
    n = len(series)
    cfg = sim.sim_cfg

    def _update_outdoor(self):
        i = min(self.step_count, n - 1)
        self._temp_out = series[i]["T_out"]
        # 把实测室内光照换算为等效室外光照，仍走正常光照通道，
        # 这样策略的照明动作依然生效（室内光照 = 室外×窗比 + 人造光）
        self._light_out = min(
            series[i]["light"] / max(cfg.light_window_ratio, 1e-6),
            cfg.light_outdoor_max)

    def _update_occupancy(self, is_weekend):
        i = min(self.step_count, n - 1)
        o = 1 if series[i]["occ"] else 0
        self.state.occ_a, self.state.occ_b = (o, 0)
        self.state.occ_dist = 2 if o else 0

    sim._update_outdoor = types.MethodType(_update_outdoor, sim)
    sim._update_occupancy = types.MethodType(_update_occupancy, sim)


def make_env(series, t0, rew_cfg=None):
    env = SmartHomeEnv(rew_cfg=rew_cfg or RewardConfig(),
                       episode_steps=len(series), start_datetime=t0)
    patch(env.sim, series)
    return env


# ============================================================
# 3. 运行一个回合
# ============================================================
def run_episode(series, t0, action_fn, rew_cfg=None):
    env = make_env(series, t0, rew_cfg)
    env.reset(seed=1000)
    # 初始室内温度取实测首值
    env.sim.state.temp_in = series[0]["T_in"]
    cost, steps = 0.0, 0
    while True:
        a = action_fn(env)
        _, _, term, trunc, info = env.step(a)
        c = info["components"]
        cost += c["elec_price"] * c["energy_kwh"]
        steps += 1
        if term or trunc:
            m = info["episode_metrics"]
            days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
            out = {"energy": float(m.get("energy_per_day_kwh", 0.0)),
                   "discomfort": float(m.get("discomfort_ratio", 0.0)),
                   "cost": cost / days,
                   "switch": float(m.get("switch_per_day", 0.0)),
                   "hours": steps * env.sim_cfg.dt_minutes / 60.0}
            break
    env.close()
    return out


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(target):
    def fn(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        T = obs["temp_in"]
        if T > target + 0.5:
            ac = ac_idx_for(target, env.act_cfg)
        elif T < target - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def find_model(scale, seed):
    for d in MODEL_DIRS:
        p = os.path.join(d, f"es{scale}_s{seed}", "final_model.zip")
        if os.path.exists(p):
            return p
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    series, t0 = load_series()
    if not series:
        print("  未找到实测数据")
        return 1
    print("=" * 84)
    print(" 真实工况复演验证")
    print("=" * 84)
    print(f"  时间序列：{len(series)} 个 15 分钟步"
          f"（{len(series)*DT_MIN/60:.1f} 小时）")
    print(f"  覆盖：{series[0]['t']} ~ {series[-1]['t']}")
    occ_n = sum(1 for s in series if s["occ"])
    print(f"  有人在室步数：{occ_n} / {len(series)}（{occ_n/len(series):.0%}）")
    tout = [s["T_out"] for s in series]
    print(f"  室外温度：{min(tout):.1f} ~ {max(tout):.1f} °C")
    tin = [s["T_in"] for s in series]
    print(f"  实测室温：{min(tin):.1f} ~ {max(tin):.1f} °C")
    print()

    results = {"n_steps": len(series), "hours": len(series) * DT_MIN / 60.0}

    # ---------- 规则 ----------
    print("=== 规则基线（在实测工况下）===")
    print(f"  {'设定':>6}{'电量kWh/天':>12}{'不达标':>10}{'切换/天':>10}")
    print("  " + "-" * 38)
    rule = []
    for sp in RULE_SETPOINTS:
        r = run_episode(series, t0, make_rule(float(sp)))
        r["setpoint"] = sp
        rule.append(r)
        print(f"  {sp:>5}°C{r['energy']:>12.3f}{r['discomfort']:>10.1%}"
              f"{r['switch']:>10.1f}")
    results["rule"] = rule
    print()

    # ---------- RL ----------
    print("=== 强化学习（合成场景训练，实测工况评估）===")
    print(f"  {'权重':>6}{'电量kWh/天':>16}{'不达标':>10}{'切换/天':>10}")
    print("  " + "-" * 44)
    rl = {}
    for es in SCALES:
        rs = []
        for seed in SEEDS:
            p = find_model(es, seed)
            if not p:
                continue
            model = PPO.load(p, device="cpu")
            rc = RewardConfig(weight_mode="scenario")
            rc.energy_scale = float(es)

            def fn(env, _m=model):
                v = np.array(env.sim.to_vector(env.get_obs_dict()),
                             dtype=np.float32)
                return int(_m.predict(v, deterministic=True)[0])
            rs.append(run_episode(series, t0, fn, rc))
        if not rs:
            continue
        rl[es] = {"energy": mean(x["energy"] for x in rs),
                  "energy_sd": pstdev([x["energy"] for x in rs])
                  if len(rs) > 1 else 0.0,
                  "discomfort": mean(x["discomfort"] for x in rs),
                  "switch": mean(x["switch"] for x in rs),
                  "n": len(rs),
                  "seeds": [{"energy": x["energy"],
                             "discomfort": x["discomfort"]} for x in rs]}
        s = rl[es]
        print(f"  {es:>6}{s['energy']:>9.3f}±{s['energy_sd']:<6.3f}"
              f"{s['discomfort']:>10.1%}{s['switch']:>10.1f}  (n={s['n']})")
    results["rl"] = {str(k): v for k, v in rl.items()}
    print()

    # ---------- 等舒适度对比 ----------
    front = sorted(rule, key=lambda r: r["discomfort"])
    print("=" * 84)
    print(" 实测工况下的等舒适度对比")
    print("=" * 84)
    print(f"  {'权重':>6}{'RL不达标':>10}{'RL电量':>10}"
          f"{'规则同舒适度':>14}{'RL优势':>10}")
    print("  " + "-" * 52)
    eq = []
    for es in sorted(rl):
        d, e = rl[es]["discomfort"], rl[es]["energy"]
        lo = max([p for p in front if p["discomfort"] <= d],
                 key=lambda p: p["discomfort"], default=None)
        hi = min([p for p in front if p["discomfort"] >= d],
                 key=lambda p: p["discomfort"], default=None)
        if lo and hi and hi["discomfort"] > lo["discomfort"]:
            w = (d - lo["discomfort"]) / (hi["discomfort"] - lo["discomfort"])
            re_ = lo["energy"] + w * (hi["energy"] - lo["energy"])
        elif lo:
            re_ = lo["energy"]
        elif hi:
            re_ = hi["energy"]
        else:
            continue
        adv = (re_ - e) / re_
        eq.append({"scale": es, "rl_d": d, "rl_e": e, "rule_e": re_, "adv": adv})
        print(f"  {es:>6}{d:>10.1%}{e:>10.3f}{re_:>14.3f}{adv:>+10.1%}")
    results["equal_comfort"] = eq
    print()

    # 支配关系
    dom = []
    for es in sorted(rl):
        de, ee = rl[es]["discomfort"], rl[es]["energy"]
        beat = [p for p in rule
                if de <= p["discomfort"] and ee <= p["energy"]
                and (de < p["discomfort"] or ee < p["energy"])]
        if beat:
            dom.append({"scale": es, "beat": [p["setpoint"] for p in beat]})
            print(f"  RL es={es:<4}（{de:.1%}, {ee:.3f}）支配规则设定点 "
                  f"{[p['setpoint'] for p in beat]}")
    results["dominated"] = dom
    print()

    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    md = ["# 真实工况复演验证", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 时间序列：{len(series)} 个 15 分钟步"
          f"（{len(series)*DT_MIN/60:.1f} 小时，"
          f"{series[0]['t']:%m-%d %H:%M} ~ {series[-1]['t']:%m-%d %H:%M}）",
          "- 外生输入：实测室外温度 + 实测占用 + 实测光照",
          "- 热工模型：gen3 联合标定参数（全时段 RMSE 1.728 °C）",
          "- 策略：空调/照明/通风由策略控制（不复现实测空调动作）", "",
          "## 一、为什么做这个验证", "",
          "此前的全部结论都建立在**合成场景**上（仿真自行生成典型上班族",
          "作息与正弦室外温度）。审阅时最可能的质疑是"
          "\"策略在真实工况下还有效吗\"。", "",
          "本验证把实测的 49.9 小时真实条件注入仿真，替换三个外生输入：", "",
          "| 输入 | 原（合成） | 现（实测） |",
          "|---|---|---|",
          "| 室外温度 | 正弦日变化 | 实测值 + Open-Meteo 回填 |",
          "| 人员占用 | 概率模型（典型上班族） | `timeline_final.csv` 人工核对 |",
          "| 室内光照 | 室外光照 × 窗比 | 实测光照 |", "",
          "**空调、照明、通风仍由策略控制** —— 这正是要检验的部分。",
          "因此这同时是一个**跨工况迁移测试**：策略在合成场景训练，",
          "在真实条件下评估。", "",
          "## 二、实测工况概况", "",
          f"- 室外温度：{min(tout):.1f} ~ {max(tout):.1f} °C",
          f"- 实测室温：{min(tin):.1f} ~ {max(tin):.1f} °C",
          f"- 有人在室：{occ_n}/{len(series)} 步（{occ_n/len(series):.0%}）",
          "", "## 三、规则基线", "",
          "| 设定温度 | 电量(kWh/天) | 不达标 | 切换(次/天) |",
          "|---|---|---|---|"]
    for r in rule:
        md.append(f"| {r['setpoint']} °C | {r['energy']:.3f} | "
                  f"{r['discomfort']:.1%} | {r['switch']:.1f} |")
    md += ["", "## 四、强化学习（合成场景训练，实测工况评估）", "",
           "| energy_scale | 电量(kWh/天) | 不达标 | 切换(次/天) |",
           "|---|---|---|---|"]
    for es in sorted(rl):
        s = rl[es]
        md.append(f"| {es} | {s['energy']:.3f} ± {s['energy_sd']:.3f} | "
                  f"{s['discomfort']:.1%} | {s['switch']:.1f} |")
    md += ["", "## 五、等舒适度对比", "",
           "| energy_scale | RL 不达标 | RL 电量 | 规则同舒适度电量 | RL 优势 |",
           "|---|---|---|---|---|"]
    for r in eq:
        md.append(f"| {r['scale']} | {r['rl_d']:.1%} | {r['rl_e']:.3f} | "
                  f"{r['rule_e']:.3f} | **{r['adv']:+.1%}** |")
    md += ["", "## 六、支配关系", ""]
    for d in dom:
        md.append(f"- RL es={d['scale']} 支配规则设定点 {d['beat']}")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
