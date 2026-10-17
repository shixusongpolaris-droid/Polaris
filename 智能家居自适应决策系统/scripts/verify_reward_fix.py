# -*- coding: utf-8 -*-
"""验证"无人时不施加区间内梯度"的修复效果。

背景
----
修复前，`temp_cost` 在舒适区间内部也施加"趋向区间中心"的梯度，
导致 energy_scale=10 的 PPO 在无人时把空房间压到 23.67 °C、
空调开机率 98.9%，无人段耗电占总电量 44.7%。

修复后应观察到：无人时空调开机率显著下降、无人段耗电下降、
而有人时段的舒适度不应变差。

本脚本训练 2 个模型（es=10、es=30）并对比修复前后的关键指标。

用法
----
    python scripts/verify_reward_fix.py

输出
----
docs/奖励函数修复验证.md
models/reward_fix_verify/results.json
"""

import importlib
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
from src.env import RewardConfig, SmartHomeEnv        # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402
from stable_baselines3.common.monitor import Monitor   # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "reward_fix_verify")
OUT_MD = os.path.join(PROJ, "docs", "奖励函数修复验证.md")

TIMESTEPS = 300_000
SEEDS = [42, 66, 2026]
SCALES = [10.0, 30.0]
EVAL_EPISODES = 8
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6


def rew_for(scale):
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=float(scale),
                        lookahead_weight=LOOKAHEAD)


def train(scale, seed):
    d = os.path.join(OUT_DIR, f"es{int(scale)}_s{seed}")
    os.makedirs(d, exist_ok=True)
    ck = os.path.join(d, "final_model.zip")
    if os.path.exists(ck):
        return ck
    venv = DummyVecEnv([lambda: Monitor(
        SmartHomeEnv(rew_cfg=rew_for(scale), seed=seed))])
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                gae_lambda=0.95, ent_coef=0.01, device="cpu")
    model.learn(total_timesteps=TIMESTEPS)
    model.save(ck)
    venv.close()
    return ck


def measure(action_fn, n=EVAL_EPISODES):
    rc = rew_for(100.0)
    E, C, per = [], [], []
    tot = bad = occ_steps = occ_bad = 0
    occ_e = emp_e = 0.0
    occ_T, emp_T = [], []
    ac_on_emp = emp_n = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            e = float(info["components"]["energy_kwh"])
            if not occ:
                lo, hi = rc.temp_low_empty, rc.temp_high_empty
                emp_e += e
                emp_T.append(T)
                emp_n += 1
                if env.sim.state.ac_idx > 0:
                    ac_on_emp += 1
            else:
                occ_steps += 1
                occ_e += e
                occ_T.append(T)
                if hour >= rc.sleep_start_hour or hour < rc.sleep_end_hour:
                    lo, hi = rc.temp_low_sleep, rc.temp_high_sleep
                else:
                    lo, hi = rc.temp_low_occupied, rc.temp_high_occupied
            tot += 1
            if T > hi or T < lo:
                bad += 1
                if occ:
                    occ_bad += 1
            cost += info["components"]["elec_price"] * \
                info["components"]["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                days = max(env.episode_steps * env.sim_cfg.dt_minutes
                           / 60 / 24, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                break
        env.close()
    return {"energy": mean(E), "cost": mean(C),
            "total_discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1),
            "occ_energy": occ_e / max(n * 7, 1),
            "emp_energy": emp_e / max(n * 7, 1),
            "occ_T": mean(occ_T) if occ_T else 0.0,
            "emp_T": mean(emp_T) if emp_T else 0.0,
            "ac_on_emp": ac_on_emp / max(emp_n, 1)}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("=" * 90)
    print(" 奖励函数修复验证：无人时不施加区间内梯度")
    print("=" * 90)
    print()

    res = {}
    for es in SCALES:
        for seed in SEEDS:
            ck = train(es, seed)
            model = PPO.load(ck, device="cpu")

            def fn(env, _m=model):
                v = np.array(env.sim.to_vector(env.get_obs_dict()),
                             dtype=np.float32)
                return int(_m.predict(v, deterministic=True)[0])
            r = measure(fn)
            res[f"es{int(es)}_s{seed}"] = r
            print(f"  es={es:.0f} seed={seed}")
            print(f"    总电量 {r['energy']:.3f} kWh/天  "
                  f"有人段 {r['occ_energy']:.3f}  "
                  f"无人段 {r['emp_energy']:.3f}")
            print(f"    有人时均温 {r['occ_T']:.2f} °C   "
                  f"无人时均温 {r['emp_T']:.2f} °C   "
                  f"无人时空调开机率 {r['ac_on_emp']:.1%}")
            print(f"    总不达标 {r['total_discomfort']:.2%}   "
                  f"有人不达标 {r['occ_discomfort']:.2%}")
            print()

    # 与修复前对比（修复前的数字来自 docs/为何比全开更费电.md）
    BEFORE = {
        "10": {"energy": 7.722, "occ_energy": 4.268, "emp_energy": 3.454,
               "occ_T": 24.61, "emp_T": 23.67, "ac_on_emp": 0.989,
               "total_discomfort": 0.0001, "occ_discomfort": 0.0002},
        "30": {"energy": 6.496, "occ_energy": 3.904, "emp_energy": 2.592,
               "occ_T": 25.11, "emp_T": 25.60, "ac_on_emp": 0.997,
               "total_discomfort": 0.0590, "occ_discomfort": 0.0945},
    }
    print("=" * 90)
    print(" 修复前后对比")
    print("=" * 90)
    print(f"  {'项':<18}{'es=10 修复前':>14}{'es=10 修复后':>14}"
          f"{'es=30 修复前':>14}{'es=30 修复后':>14}")
    print("  " + "-" * 76)
    keys = [("energy", "总电量", "{:.3f}"),
            ("occ_energy", "有人段耗电", "{:.3f}"),
            ("emp_energy", "无人段耗电", "{:.3f}"),
            ("emp_T", "无人时均温", "{:.2f}"),
            ("ac_on_emp", "无人开机率", "{:.1%}"),
            ("occ_discomfort", "有人不达标", "{:.2%}")]
    for k, lab, fmt in keys:
        a = BEFORE["10"][k]
        b = res.get("es10_s42", {}).get(k, float('nan'))
        c = BEFORE["30"][k]
        d = res.get("es30_s42", {}).get(k, float('nan'))
        print(f"  {lab:<18}{fmt.format(a):>14}{fmt.format(b):>14}"
              f"{fmt.format(c):>14}{fmt.format(d):>14}")
    print()

    # 与规则对比
    RULE = {"energy": 6.325, "cost": 3.349, "occ_discomfort": 0.0014,
            "emp_energy": 2.565}
    r10 = res.get("es10_s42")
    if r10:
        print("=== 修复后的 RL es=10 vs 全开 25 °C ===")
        print(f"  电量       : RL {r10['energy']:.3f} vs 规则 {RULE['energy']:.3f}"
              f"   （{(r10['energy']/RULE['energy']-1):+.1%}）")
        print(f"  无人段耗电 : RL {r10['emp_energy']:.3f} vs 规则 "
              f"{RULE['emp_energy']:.3f}"
              f"   （{(r10['emp_energy']/RULE['emp_energy']-1):+.1%}）")
        print(f"  有人不达标 : RL {r10['occ_discomfort']:.2%} vs 规则 "
              f"{RULE['occ_discomfort']:.2%}")
        print()

    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump({"after": res, "before": BEFORE}, f,
                  ensure_ascii=False, indent=2)

    md = ["# 奖励函数修复验证", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 训练 {TIMESTEPS:,} 步，种子 {SEEDS}，es ∈ {SCALES}", "",
          "## 一、修复内容", "",
          "`RewardCalculator.temp_cost` 在舒适区间内部原本也施加"
          "\"趋向区间中心\"的梯度：", "",
          "```python",
          "center = (T_low + T_high) / 2.0",
          "return 0.05 * c.alpha_hot * abs(temp_in - center), 0.0",
          "```", "",
          "无人区间的中心是 (16+30)/2 = 23 °C，因此在无人时段也惩罚"
          "\"温度偏离 23 °C\"，诱导策略给空房间制冷。", "",
          "修复：**无人时不施加该梯度**（越界惩罚仍保留）。", "",
          "## 二、修复前后对比", "",
          "| 项 | es=10 修复前 | es=10 修复后 | es=30 修复前 | es=30 修复后 |",
          "|---|---|---|---|---|"]
    for k, lab, fmt in keys:
        a = BEFORE["10"][k]
        b = res.get("es10_s42", {}).get(k, float('nan'))
        c = BEFORE["30"][k]
        d = res.get("es30_s42", {}).get(k, float('nan'))
        md.append(f"| {lab} | {fmt.format(a)} | {fmt.format(b)} | "
                  f"{fmt.format(c)} | {fmt.format(d)} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
