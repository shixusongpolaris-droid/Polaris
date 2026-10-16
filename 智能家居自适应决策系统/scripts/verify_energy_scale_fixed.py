# -*- coding: utf-8 -*-
"""能耗权重验证（修正版）：用**正确的**规则基线重新对比。

修正说明
--------
`verify_energy_scale.py` 中的规则实现有误：

    if T > target + 0.5: return 2      # ← 错误

`ac_idx` 的真实语义是 0=关闭、1~5 = 25/26/27/28/29 °C。因此返回 2
表示"把设定温度调到 26 °C"，而正确做法是"用目标温度对应的档位制冷"
（`evaluate_all.py::ac_idx_for`）。错误版本在房间过热时反而升高设定
温度，属于欠制冷，导致规则基线的不达标率虚高到 17.9%（正确约 3.7%）。

**因此前一版得到的"强化学习在能耗与舒适度上双赢"结论不成立**——
那是拿 RL 与一个坏掉的基线比较。本脚本用正确规则重算。

做法
----
1. 直接复用 `models/energy_scale_verify/es*_s*` 下已训练好的模型
   （训练与评估本身无问题，只有基线实现错了），无需重新训练。
2. 用正确规则评估基线，并对规则做设定温度扫描（22~29 °C），
   得到规则自身的帕累托前沿。
3. 与 RL 的前沿逐点比较，并做等舒适度插值比较。

输出
----
docs/能耗权重验证_修正版.md
models/energy_scale_verify/verify_fixed.json
"""

import json
import os
import sys
from datetime import datetime
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # E402
from stable_baselines3 import PPO                      # E402

OUT_DIR = os.path.join(PROJ, "models", "energy_scale_verify")
OUT_MD = os.path.join(PROJ, "docs", "能耗权重验证_修正版.md")

AC_OFF = 0
SCALES = [10, 30, 60, 100]
SEEDS = [42, 66, 2026, 7, 123]
RULE_SETPOINTS = [22, 23, 24, 25, 26, 27, 28, 29]
EVAL_EPISODES = 12
SEED0 = 5000


def ac_idx_for(target_c, cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def thermostat(env, target_c):
    """带滞环的恒温器（与 evaluate_all.py 一致，已核对 ac_idx 语义）。"""
    t = env.get_obs_dict()["temp_in"]
    if t > target_c + 0.5:
        return ac_idx_for(target_c, env.act_cfg)
    if t < target_c - 0.5:
        return AC_OFF
    return env.sim.state.ac_idx


def make_rule(target_c, with_light=True):
    def fn(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        ac = thermostat(env, target_c)
        light = 1 if (with_light and obs["light_in"] < 300) else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def evaluate(action_fn, n=EVAL_EPISODES, seed0=SEED0):
    E, C, D, S = [], [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=RewardConfig(), seed=seed0 + ep)
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                D.append(float(m.get("discomfort_ratio", 0.0)))
                S.append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {"energy": mean(E), "energy_sd": pstdev(E) if len(E) > 1 else 0.0,
            "discomfort": mean(D), "cost": mean(C), "switch": mean(S),
            "n": len(E)}


def eval_rl_model(scale, seed, n=EVAL_EPISODES):
    path = os.path.join(OUT_DIR, f"es{scale}_s{seed}", "final_model.zip")
    if not os.path.exists(path):
        return None
    model = PPO.load(path, device="cpu")
    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(v, deterministic=True)[0])
    return evaluate(fn, n)


def main():
    print("=" * 84)
    print(" 能耗权重验证（修正版）—— 用正确的规则基线")
    print("=" * 84)
    print()

    # ---------- 1. 规则设定温度扫描 ----------
    print("=== 规则基线：设定温度扫描（正确实现）===")
    print(f"  {'设定':>6}{'电量kWh':>10}{'不达标':>9}{'切换/天':>9}")
    print("  " + "-" * 36)
    rule_pts = []
    for sp in RULE_SETPOINTS:
        r = evaluate(make_rule(float(sp)))
        rule_pts.append({"setpoint": sp, **r})
        print(f"  {sp:>5}°C{r['energy']:>10.3f}{r['discomfort']:>9.1%}"
              f"{r['switch']:>9.1f}")
    print()

    # ---------- 2. RL 各权重 ----------
    print("=== 强化学习：各能耗权重（复用已训练模型）===")
    rl = {}
    for es in SCALES:
        rs = []
        for seed in SEEDS:
            r = eval_rl_model(es, seed)
            if r:
                rs.append(r)
        if not rs:
            continue
        rl[es] = {
            "energy": mean(x["energy"] for x in rs),
            "energy_sd": pstdev([x["energy"] for x in rs]) if len(rs) > 1 else 0,
            "discomfort": mean(x["discomfort"] for x in rs),
            "cost": mean(x["cost"] for x in rs),
            "switch": mean(x["switch"] for x in rs),
            "n": len(rs),
            "per_seed": rs,
        }
        s = rl[es]
        print(f"  energy_scale={es:<5} 电量 {s['energy']:.3f}±{s['energy_sd']:.3f}  "
              f"不达标 {s['discomfort']:.1%}  切换 {s['switch']:.1f}  (n={s['n']})")
    print()

    # ---------- 3. 对比 ----------
    best_rule = min(rule_pts, key=lambda p: p["energy"])   # 最省电的规则点
    rule25 = min(rule_pts, key=lambda p: abs(p["setpoint"] - 25))
    print("=" * 84)
    print(" 对比")
    print("=" * 84)
    print(f"  {'策略':<22}{'电量kWh/天':>12}{'不达标':>10}{'切换/天':>9}")
    print("  " + "-" * 56)
    for p in rule_pts:
        print(f"  {'规则@'+str(p['setpoint'])+'°C':<22}{p['energy']:>12.3f}"
              f"{p['discomfort']:>10.1%}{p['switch']:>9.1f}")
    for es in sorted(rl):
        s = rl[es]
        print(f"  {f'RL es={es}':<22}{s['energy']:>12.3f}"
              f"{s['discomfort']:>10.1%}{s['switch']:>9.1f}")
    print()

    # 等舒适度比较：对每个 RL 点，在规则前沿上插值同舒适度的电量
    print("=== 等舒适度比较（把规则前沿插值到 RL 的不达标率处）===")
    front = sorted(rule_pts, key=lambda p: p["discomfort"])
    print(f"  {'RL 权重':<12}{'RL 不达标':>10}{'RL 电量':>10}"
          f"{'规则同舒适度电量':>18}{'RL 优势':>10}")
    print("  " + "-" * 62)
    eq_rows = []
    for es in sorted(rl):
        s = rl[es]
        d = s["discomfort"]
        # 线性插值
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
            re_ = None
        if re_ is None:
            continue
        adv = (re_ - s["energy"]) / re_
        eq_rows.append({"scale": es, "rl_disc": d, "rl_energy": s["energy"],
                        "rule_energy_at_same_comfort": re_, "advantage": adv})
        print(f"  {es:<12}{d:>10.1%}{s['energy']:>10.3f}{re_:>18.3f}"
              f"{adv:>+10.1%}")
    print()

    out = {"rule_points": rule_pts, "rl": {str(k): v for k, v in rl.items()},
           "equal_comfort": eq_rows}
    with open(os.path.join(OUT_DIR, "verify_fixed.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 能耗权重验证（修正版）", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}", "",
          "## 一、修正说明", "",
          "前一次验证（`verify_energy_scale.py`）中的规则实现有误：", "",
          "```python",
          "if T > target + 0.5: return 2      # ← 错误",
          "```", "",
          "`ac_idx` 的真实语义是 **0=关闭、1~5 = 25/26/27/28/29 °C**，",
          "因此返回 2 表示\"把设定温度调到 26 °C\"，而正确做法是用目标",
          "温度对应的档位制冷。错误版本在房间过热时反而升高设定温度，",
          "属于欠制冷，使规则基线的不达标率虚高到 **17.9%**（正确约 3.7%）。", "",
          "**因此前一版\"强化学习在能耗与舒适度上双赢\"的结论不成立**——",
          "那是拿 RL 与一个坏掉的基线比较。本文件用正确规则重算。", "",
          "强化学习侧的模型与评估本身无问题，故直接复用已训练模型，",
          "无需重新训练。", "",
          "## 二、规则基线的设定温度扫描（正确实现）", "",
          "| 设定温度 | 电量(kWh/天) | 不达标 | 切换(次/天) |",
          "|---|---|---|---|"]
    for p in rule_pts:
        md.append(f"| {p['setpoint']} °C | {p['energy']:.3f} | "
                  f"{p['discomfort']:.1%} | {p['switch']:.1f} |")
    md += ["", "## 三、强化学习各能耗权重（5 种子均值）", "",
           "| energy_scale | 电量(kWh/天) | 不达标 | 切换(次/天) |",
           "|---|---|---|---|"]
    for es in sorted(rl):
        s = rl[es]
        md.append(f"| {es} | {s['energy']:.3f} ± {s['energy_sd']:.3f} | "
                  f"{s['discomfort']:.1%} | {s['switch']:.1f} |")
    md += ["", "## 四、等舒适度比较", "",
           "把规则前沿线性插值到 RL 所在的不达标率处，比较电量：", "",
           "| RL 权重 | RL 不达标 | RL 电量 | 规则同舒适度电量 | RL 优势 |",
           "|---|---|---|---|---|"]
    for r in eq_rows:
        md.append(f"| {r['scale']} | {r['rl_disc']:.1%} | {r['rl_energy']:.3f} | "
                  f"{r['rule_energy_at_same_comfort']:.3f} | "
                  f"**{r['advantage']:+.1%}** |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
