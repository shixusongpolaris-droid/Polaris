# -*- coding: utf-8 -*-
"""压缩机启动损耗的敏感性分析。

背景
----
用户确认家中的空调是**定频**机型。定频空调靠压缩机直接启停控温，
启动浪涌为额定电流的 5~8 倍，且启动后有一段压力未平衡的低效运行期。

项目已在 `_step_energy_kwh` 中建模该损耗：

    if ac_started:
        energy += rcfg.ac_startup_penalty_kwh     # 当前 0.010 kWh

但 0.010 kWh/次（约合额定功率运行 33 秒）落在工程估计区间的下限。
常见说法是"启动一次相当于多运行 3~5 分钟"，即 0.055 ~ 0.092 kWh。
若真值显著更高，规则控制"频繁启停"的代价会被放大，
RL"启停少"的优势可能改变两者对比。

本脚本回答三个问题
------------------
1. **记账效应**：保持策略不变，仅改变启动损耗的计价，
   规则与 RL 的电量差如何随该参数变化？
2. **适应效应**：若把启动损耗调高，RL 是否会学着减少启停？
3. **临界点**：在什么取值下，规则的能耗优势消失 / RL 反超？

做法
----
  A 组：以当前损耗（0.010）训练 es=30 的 PPO（3 种子）
  B 组：以高损耗（0.050）训练 es=30 的 PPO（3 种子）
  对每一组策略，在损耗 ∈ {0.005 ... 0.120} 下重新评估（策略不变）
  规则基线同样在各损耗下评估

输出
----
docs/启动损耗敏感性.md
models/startup_penalty/results.json
"""

import json
import os
import sys
import time
from datetime import datetime
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
from stable_baselines3.common.monitor import Monitor   # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "startup_penalty")
OUT_MD = os.path.join(PROJ, "docs", "启动损耗敏感性.md")

AC_OFF = 0
_AC = ActionConfig()
AC_25C = _AC.ac_setpoints.index(25.0) + 1
AC_27C = _AC.ac_setpoints.index(27.0) + 1

PENALTIES = [0.005, 0.010, 0.020, 0.030, 0.050, 0.080, 0.120]
TRAIN_PENALTIES = [0.010, 0.050]
SEEDS = [42, 66, 2026]
SCALE = 30.0
TIMESTEPS = 300_000
EVAL_EPISODES = 12
SEED0 = 9000


def cfg_for(penalty, scale=SCALE):
    rc = RewardConfig(weight_mode="scenario")
    rc.energy_scale = float(scale)
    rc.ac_startup_penalty_kwh = float(penalty)
    return rc


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(target=25.0):
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


def evaluate(action_fn, penalty, n=EVAL_EPISODES):
    rc = cfg_for(penalty)
    E, D, S, ST = [], [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=SEED0 + ep)
        env.reset(seed=SEED0 + ep)
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                D.append(float(m.get("discomfort_ratio", 0.0)))
                S.append(float(m.get("switch_per_day", 0.0)))
                ST.append(float(m.get("ac_start_count", 0)) /
                          max(steps * env.sim_cfg.dt_minutes / 60 / 24, 1e-9))
                break
        env.close()
    return {"energy": mean(E), "energy_sd": pstdev(E) if len(E) > 1 else 0.0,
            "discomfort": mean(D), "switch": mean(S), "starts": mean(ST),
            "n": len(E)}


def train(penalty, seed):
    d = os.path.join(OUT_DIR, f"p{int(penalty*1000)}_s{seed}")
    os.makedirs(d, exist_ok=True)
    ck = os.path.join(d, "final_model.zip")
    if os.path.exists(ck):
        return ck

    def mk():
        return Monitor(SmartHomeEnv(rew_cfg=cfg_for(penalty), seed=seed))
    venv = DummyVecEnv([mk])
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=2048,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                gae_lambda=0.95, ent_coef=0.01, device="cpu")
    model.learn(total_timesteps=TIMESTEPS)
    model.save(ck)
    venv.close()
    return ck


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print("=" * 84)
    print(" 压缩机启动损耗敏感性分析")
    print("=" * 84)
    print(f"  评估用损耗取值：{PENALTIES}")
    print(f"  训练用损耗取值：{TRAIN_PENALTIES}（各 {len(SEEDS)} 种子）")
    print()

    res = {"penalties": PENALTIES, "rule": {}, "rl": {}}

    # ---- 规则基线（各损耗下评估；策略不随损耗变化）----
    print("=== 规则基线 ===")
    print(f"  {'损耗':>8}{'电量kWh/天':>12}{'不达标':>10}{'空调启动/天':>13}")
    print("  " + "-" * 46)
    rule_fn = make_rule(25.0)
    for p in PENALTIES:
        r = evaluate(rule_fn, p)
        res["rule"][str(p)] = r
        print(f"  {p:>8.3f}{r['energy']:>12.3f}{r['discomfort']:>10.1%}"
              f"{r['starts']:>13.1f}")
    print()

    # ---- RL：两档训练损耗 ----
    for tp in TRAIN_PENALTIES:
        key = f"train{tp:.3f}"
        res["rl"][key] = {"train_penalty": tp, "by_penalty": {},
                          "seeds": {}}
        print(f"=== RL：以损耗 {tp:.3f} 训练 ===")
        models = []
        for seed in SEEDS:
            ck = train(tp, seed)
            models.append((seed, PPO.load(ck, device="cpu")))

        print(f"  {'评估损耗':>10}{'电量kWh/天':>14}{'不达标':>10}"
              f"{'空调启动/天':>13}{'切换/天':>10}")
        print("  " + "-" * 58)
        for p in PENALTIES:
            rs = []
            for seed, model in models:
                def fn(env, _m=model):
                    v = np.array(env.sim.to_vector(env.get_obs_dict()),
                                 dtype=np.float32)
                    return int(_m.predict(v, deterministic=True)[0])
                rs.append(evaluate(fn, p))
            agg = {"energy": mean(x["energy"] for x in rs),
                   "energy_sd": pstdev([x["energy"] for x in rs])
                   if len(rs) > 1 else 0.0,
                   "discomfort": mean(x["discomfort"] for x in rs),
                   "starts": mean(x["starts"] for x in rs),
                   "switch": mean(x["switch"] for x in rs), "n": len(rs)}
            res["rl"][key]["by_penalty"][str(p)] = agg
            print(f"  {p:>10.3f}{agg['energy']:>14.3f}{agg['discomfort']:>10.1%}"
                  f"{agg['starts']:>13.1f}{agg['switch']:>10.1f}")
        for seed, _ in models:
            res["rl"][key]["seeds"][str(seed)] = True
        print()

    # ---- 对比与临界点 ----
    # 注意口径：adv = (规则电量 − RL电量) / 规则电量
    #   adv > 0 表示规则耗电更多，即 RL 更省电
    #   adv < 0 表示规则更省电
    print("=" * 84)
    print(" 对比：同一损耗下，RL 相对规则的省电幅度")
    print("=" * 84)
    print(f"  {'损耗':>8}{'规则电量':>10}{'RL(0.010训)':>13}"
          f"{'RL(0.050训)':>13}{'RL省电':>10}{'规则启动':>10}{'RL启动':>9}")
    print("  " + "-" * 74)
    rows = []
    for p in PENALTIES:
        r = res["rule"][str(p)]["energy"]
        a = res["rl"]["train0.010"]["by_penalty"][str(p)]["energy"]
        b = res["rl"]["train0.050"]["by_penalty"][str(p)]["energy"]
        ad = (r - a) / r
        rows.append({"p": p, "rule": r, "rl_a": a, "rl_b": b, "adv": ad})
        print(f"  {p:>8.3f}{r:>10.3f}{a:>13.3f}{b:>13.3f}{ad:>+10.1%}"
              f"{res['rule'][str(p)]['starts']:>10.1f}"
              f"{res['rl']['train0.010']['by_penalty'][str(p)]['starts']:>9.1f}")
    print()
    lose = [x for x in rows if x["adv"] <= 0]
    if lose:
        print(f"  RL 的省电优势在启动损耗 >= {lose[0]['p']:.3f} 时消失")
    else:
        print(f"  在评估范围内（到 {PENALTIES[-1]:.3f} kWh/次），"
              f"RL 始终比规则省电")

    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    md = ["# 压缩机启动损耗敏感性分析", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          "- 空调机型：**定频**（项目所有者确认）",
          "- 评估损耗取值：" + ", ".join(f"{x}" for x in PENALTIES),
          f"- RL 训练损耗：{TRAIN_PENALTIES}，各 {len(SEEDS)} 种子，"
          f"energy_scale={SCALE:.0f}", "",
          "## 一、为什么要做", "",
          "定频空调靠压缩机直接启停控温，启动浪涌为额定电流的 5~8 倍，",
          "且启动后有压力未平衡的低效运行期。项目已建模该项损耗", "",
          "```python",
          "if ac_started:",
          "    energy += rcfg.ac_startup_penalty_kwh     # 当前 0.010 kWh",
          "```", "",
          "但 0.010 kWh/次（约合额定功率运行 33 秒）落在工程估计区间下限；",
          "常见说法为\"启动一次相当于多运行 3~5 分钟\"，即 0.055~0.092 kWh。",
          "若真值更高，规则控制频繁启停的代价会被放大，",
          "RL 启停少的优势可能改变两者对比。", "",
          "## 二、规则基线（各损耗下）", "",
          "| 启动损耗(kWh/次) | 电量(kWh/天) | 不达标 | 空调启动(次/天) |",
          "|---|---|---|---|"]
    for p in PENALTIES:
        r = res["rule"][str(p)]
        md.append(f"| {p:.3f} | {r['energy']:.3f} | {r['discomfort']:.1%} | "
                  f"{r['starts']:.1f} |")
    md += ["", "## 三、强化学习", ""]
    for tp in TRAIN_PENALTIES:
        key = f"train{tp:.3f}"
        md += [f"**以启动损耗 {tp:.3f} 训练（3 种子）**", "",
               "| 评估损耗 | 电量(kWh/天) | 不达标 | 空调启动(次/天) | 切换(次/天) |",
               "|---|---|---|---|---|"]
        for p in PENALTIES:
            a = res["rl"][key]["by_penalty"][str(p)]
            md.append(f"| {p:.3f} | {a['energy']:.3f} ± {a['energy_sd']:.3f} | "
                      f"{a['discomfort']:.1%} | {a['starts']:.1f} | "
                      f"{a['switch']:.1f} |")
        md.append("")
    md += ["## 四、对比与临界点", "",
           "口径：`RL省电 = (规则电量 − RL电量) / 规则电量`，"
           "正值表示 **RL 比规则省电**。", "",
           "| 启动损耗 | 规则电量 | RL(0.010 训) | RL(0.050 训) | **RL 省电幅度** |",
           "|---|---|---|---|---|"]
    for x in rows:
        md.append(f"| {x['p']:.3f} | {x['rule']:.3f} | {x['rl_a']:.3f} | "
                  f"{x['rl_b']:.3f} | **{x['adv']:+.1%}** |")
    md.append("")
    if lose:
        md.append(f"**RL 的省电优势在启动损耗 ≥ {lose[0]['p']:.3f} kWh/次 "
                  f"时消失。**")
    else:
        md.append(f"**在评估范围内（至 {PENALTIES[-1]:.3f} kWh/次），"
                  f"RL 始终比规则省电，且差距随启动损耗增大而扩大。**")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  总耗时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
