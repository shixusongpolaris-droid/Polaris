# -*- coding: utf-8 -*-
"""为什么 RL 会比"一直开 25 °C"更费电？

反常之处
--------
"一直开空调 25 °C" 听起来是最费电的策略：空调永不关闭。
但实测结果反而是它比 RL（energy_scale=10）省 22% 的电。
逻辑上必须解释清楚，否则结论不可信。

可能的原因
----------
1. 奖励函数在舒适区间内部也有梯度：

       temp_cost 在区间内返回  0.05 * alpha_hot * |T − 区间中心|

   于是"越靠近区间中心越好"。各区间的中心为：

       无人         (16+30)/2 = 23.0 °C
       有人·睡眠    (24+27)/2 = 25.5 °C
       有人·清醒    (22+26)/2 = 24.0 °C

   若策略真的追这个梯度，它会把温度压到 24 °C（而非 25 °C），
   并在**无人时也压到 23 °C** —— 而"无人时关机"的规则完全不做这件事。

2. 因此 RL 可能把大量电量花在给空房间降温上。

本脚本直接量化
--------------
对每个策略统计：

    · 有人时段与无人时段各自的平均室温
    · 有人时段与无人时段各自的耗电量（kWh/天）与占比
    · 各状态的步数占比

若 RL 的无人时段耗电显著高于规则，即可确认上述机制。

输出
----
docs/为何比全开更费电.md
models/energy_by_occupancy.json
"""

import json
import os
import sys
from datetime import datetime
from statistics import mean

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

OUT_JSON = os.path.join(PROJ, "models", "energy_by_occupancy.json")
OUT_MD = os.path.join(PROJ, "docs", "为何比全开更费电.md")

EVAL_EPISODES = 12
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6
AC_OFF = 0


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(sp):
    def fn(env):
        obs = env.get_obs_dict()
        T = obs["temp_in"]
        if T > sp + 0.5:
            ac = ac_idx_for(sp, env.act_cfg)
        elif T < sp - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def rew_cfg():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=100.0, lookahead_weight=LOOKAHEAD)


def measure(action_fn, n=EVAL_EPISODES):
    rc = rew_cfg()
    # 累计量（按回合归一后取均值）
    per_ep = []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        occ_e = emp_e = 0.0
        occ_T, emp_T = [], []
        occ_n = emp_n = 0
        ac_on_occ = ac_on_emp = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            e = float(info["components"]["energy_kwh"])
            ac_on = env.sim.state.ac_idx > 0
            if occ:
                occ_e += e
                occ_T.append(T)
                occ_n += 1
                ac_on_occ += 1 if ac_on else 0
            else:
                emp_e += e
                emp_T.append(T)
                emp_n += 1
                ac_on_emp += 1 if ac_on else 0
            if term or trunc:
                days = max(env.episode_steps * env.sim_cfg.dt_minutes
                           / 60 / 24, 1e-9)
                per_ep.append({
                    "occ_energy": occ_e / days,
                    "emp_energy": emp_e / days,
                    "total_energy": (occ_e + emp_e) / days,
                    "occ_T": mean(occ_T) if occ_T else 0.0,
                    "emp_T": mean(emp_T) if emp_T else 0.0,
                    "occ_share": occ_n / max(occ_n + emp_n, 1),
                    "ac_on_occ": ac_on_occ / max(occ_n, 1),
                    "ac_on_emp": ac_on_emp / max(emp_n, 1),
                })
                break
        env.close()
    return {k: mean(x[k] for x in per_ep) for k in per_ep[0]}


def main():
    print("=" * 94)
    print(" 为什么 RL 比\"一直开 25 °C\"更费电")
    print("=" * 94)
    print()
    print("  奖励函数在舒适区间内部也有梯度（temp_cost 区间内分支）：")
    print("      代价 = 0.05 * alpha_hot * |T − 区间中心|")
    print("  区间中心：无人 23.0 / 睡眠 25.5 / 有人 24.0 °C")
    print("  因此若策略追这个梯度，会把温度压到 24 °C，且**无人时也压到 23 °C**。")
    print()

    cases = [("全开 25 °C（永不停机）", make_rule(25.0), None),
             ("全开 24 °C", make_rule(24.0), None),
             ("全开 26 °C", make_rule(26.0), None)]
    for es in [10.0, 30.0, 60.0, 100.0]:
        ck = os.path.join(PROJ, "models", "energy_scale_ci",
                          f"es{int(es)}_s42", "final_model.zip")
        if not os.path.exists(ck):
            continue
        model = PPO.load(ck, device="cpu")

        def fn(env, _m=model):
            v = np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32)
            return int(_m.predict(v, deterministic=True)[0])
        cases.append((f"RL es={es:.0f}", fn, es))

    res = {}
    print(f"  {'策略':<22}{'总电量':>9}{'有人耗电':>10}{'无人耗电':>10}"
          f"{'无人占比':>10}")
    print("  " + "-" * 66)
    for name, fn, es in cases:
        r = measure(fn)
        res[name] = r
        print(f"  {name:<22}{r['total_energy']:>9.3f}{r['occ_energy']:>10.3f}"
              f"{r['emp_energy']:>10.3f}"
              f"{r['emp_energy']/max(r['total_energy'],1e-9):>10.1%}")
    print()
    print(f"  {'策略':<22}{'有人时均温':>12}{'无人时均温':>12}"
          f"{'有人时AC开机率':>14}{'无人时AC开机率':>14}")
    print("  " + "-" * 78)
    for name, fn, es in cases:
        r = res[name]
        print(f"  {name:<22}{r['occ_T']:>12.2f}{r['emp_T']:>12.2f}"
              f"{r['ac_on_occ']:>14.1%}{r['ac_on_emp']:>14.1%}")
    print()

    base = res.get("全开 25 °C（永不停机）")
    rl10 = res.get("RL es=10")
    if base and rl10:
        print("=" * 94)
        print(" 关键对比：全开 25 °C vs RL es=10")
        print("=" * 94)
        print(f"  总电量    ：{base['total_energy']:.3f} vs "
              f"{rl10['total_energy']:.3f} kWh/天"
              f"   （RL 多用 {(rl10['total_energy']/base['total_energy']-1):+.1%}）")
        print(f"  有人段耗电：{base['occ_energy']:.3f} vs "
              f"{rl10['occ_energy']:.3f} kWh/天"
              f"   （RL 多用 {(rl10['occ_energy']/base['occ_energy']-1):+.1%}）")
        print(f"  无人段耗电：{base['emp_energy']:.3f} vs "
              f"{rl10['emp_energy']:.3f} kWh/天"
              f"   （RL 多用 {(rl10['emp_energy']/base['emp_energy']-1):+.1%}）")
        print(f"  有人时均温：{base['occ_T']:.2f} vs {rl10['occ_T']:.2f} °C")
        print(f"  无人时均温：{base['emp_T']:.2f} vs {rl10['emp_T']:.2f} °C")
        print()
        # 归因
        d_tot = rl10['total_energy'] - base['total_energy']
        d_occ = rl10['occ_energy'] - base['occ_energy']
        d_emp = rl10['emp_energy'] - base['emp_energy']
        print(f"  多出的 {d_tot:.3f} kWh 中：")
        print(f"    有人时段贡献 {d_occ:+.3f} kWh（{d_occ/d_tot:>6.1%}）")
        print(f"    无人时段贡献 {d_emp:+.3f} kWh（{d_emp/d_tot:>6.1%}）")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    md = ["# 为什么 RL 比\"一直开 25 °C\"更费电", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}+", "",
          "## 一、反常之处", "",
          "\"一直开空调 25 °C\"听起来是最费电的策略：空调永不关闭。",
          "但实测结果反而是它比 `energy_scale=10` 的强化学习省 22% 的电。",
          "这个反常识的结果必须解释清楚，否则整个对比不可信。", "",
          "## 二、原因：奖励函数在舒适区间**内部**也有梯度", "",
          "`RewardCalculator.temp_cost` 的区间内分支：", "",
          "```python",
          "# 区间内：轻微代价，提供\"越接近中心越好\"的梯度信号",
          "center = (T_low + T_high) / 2.0",
          "return 0.05 * c.alpha_hot * abs(temp_in - center), 0.0",
          "```", "",
          "各区间的中心：", "",
          "| 状态 | 舒适区间 | 中心 |",
          "|---|---|---|",
          "| 无人 | 16 ~ 30 °C | **23.0 °C** |",
          "| 有人·睡眠 | 24 ~ 27 °C | 25.5 °C |",
          "| 有人·清醒 | 22 ~ 26 °C | **24.0 °C** |", "",
          "因此追逐该梯度的策略会把有人时温度压到 24 °C（而非 25 °C），",
          "**并在无人时也往 23 °C 压** —— 而\"无人关机\"或\"恒温 25 °C\"的规则",
          "完全不做无人时段降温这件事。", "",
          "## 三、实测验证", "",
          "| 策略 | 总电量 | 有人段耗电 | 无人段耗电 | 无人段占比 |",
          "|---|---|---|---|---|"]
    for name, _, _ in cases:
        r = res[name]
        md.append(f"| {name} | {r['total_energy']:.3f} | {r['occ_energy']:.3f} | "
                  f"{r['emp_energy']:.3f} | "
                  f"{r['emp_energy']/max(r['total_energy'],1e-9):.1%} |")
    md += ["", "| 策略 | 有人时均温 | 无人时均温 | 有人时空调开机率 | 无人时空调开机率 |",
           "|---|---|---|---|---|"]
    for name, _, _ in cases:
        r = res[name]
        md.append(f"| {name} | {r['occ_T']:.2f} °C | {r['emp_T']:.2f} °C | "
                  f"{r['ac_on_occ']:.1%} | {r['ac_on_emp']:.1%} |")
    if base and rl10:
        d_tot = rl10['total_energy'] - base['total_energy']
        d_occ = rl10['occ_energy'] - base['occ_energy']
        d_emp = rl10['emp_energy'] - base['emp_energy']
        md += ["", "## 四、归因", "",
               f"RL（`es=10`）比\"全开 25 °C\"多耗电 "
               f"**{d_tot:.3f} kWh/天**，其中：", "",
               f"- 有人时段贡献 **{d_occ:+.3f} kWh**（{d_occ/d_tot:.0%}）",
               f"- 无人时段贡献 **{d_emp:+.3f} kWh**（{d_emp/d_tot:.0%}）", ""]
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
