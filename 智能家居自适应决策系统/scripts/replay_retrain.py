# -*- coding: utf-8 -*-
"""在真实工况上重新训练策略，检验"迁移失效"能否通过重训修复。

背景
----
`replay_real_conditions.py` 发现：在**合成场景**训练的策略迁移到
**实测工况**后性能大幅下降（RL es=30 的不达标率由 10.2% 恶化到 64.7%）。

根本原因是占用率差异：
    合成（典型上班族模型）  有人在室 62%
    实测（10-14 ~ 10-16）   有人在室 96%

策略在训练时学到"无人时关空调"（此时不受舒适度约束），
而在实测工况中它 96% 的时间都在室内，同一行为持续受罚。

因此需要回答：**用真实工况重新训练后，强化学习是否重新取得优势？**

做法
----
训练环境：与 `replay_real_conditions.py` 相同的实测工况复演，但加入
随机化以避免对单条确定性轨迹的死记硬背：
    · 室内初始温度在 [22, 30] 内随机
    · 室外温度叠加 ±0.5 °C 噪声
评估环境：**确定性**的实测工况复演（无随机化），与规则对比。

**注意口径**：训练与评估使用同一段实测工况，因此评估是"样本内"性能。
这是本实验的局限，需在报告中说明；但它对"能否通过重训修复迁移失效"
这个问题仍然有效。规则的评估不涉及训练，故该局限不影响两者对比的
相对关系。

输出
----
docs/真实工况重训验证.md
models/replay_retrain/results.json
"""

import json
import os
import random
import sys
import types
import time
from datetime import datetime, timedelta
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402

# 复用复演脚本的数据加载与打补丁逻辑
import importlib.util                                  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "rr", os.path.join(PROJ, "scripts", "replay_real_conditions.py"))
RR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RR)

OUT_DIR = os.path.join(PROJ, "models", "replay_retrain")
OUT_MD = os.path.join(PROJ, "docs", "真实工况重训验证.md")

SCALES = [10, 30, 60]
SEEDS = [42, 66, 2026]
TIMESTEPS = 300_000
RULE_SETPOINTS = [22, 23, 24, 25, 26, 27, 28, 29]


def patch_random(sim, series, rng):
    """训练用：实测工况 + 随机化。"""
    n = len(series)
    cfg = sim.sim_cfg
    noise = [rng.uniform(-0.5, 0.5) for _ in range(n)]

    def _update_outdoor(self):
        i = min(self.step_count, n - 1)
        self._temp_out = series[i]["T_out"] + noise[i]
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


def make_train_env(series, t0, scale, seed, rng):
    """训练环境：实测工况 + 随机化（初始室温随机、室外温度加噪）。"""
    rc = RewardConfig(weight_mode="scenario")
    rc.energy_scale = float(scale)
    env = SmartHomeEnv(rew_cfg=rc, episode_steps=len(series),
                       start_datetime=t0, seed=seed)
    patch_random(env.sim, series, rng)

    # 在 reset 之后随机化室内初始温度（避免每条回合都从同一状态起）
    orig_reset = env.reset

    def _reset(seed=None, options=None):
        obs, info = orig_reset(seed=seed, options=options)
        env.sim.state.temp_in = rng.uniform(22.0, 30.0)
        od = env.sim.get_observation()
        env._last_obs = od
        return env._to_array(od), info

    env.reset = _reset
    return env


def train_one(series, t0, scale, seed):
    d = os.path.join(OUT_DIR, f"es{scale}_s{seed}")
    os.makedirs(d, exist_ok=True)
    ck = os.path.join(d, "final_model.zip")
    if os.path.exists(ck):
        return ck
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv
    rng = random.Random(seed)
    venv = DummyVecEnv([lambda: Monitor(
        make_train_env(series, t0, scale, seed, rng))])
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=1024,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                gae_lambda=0.95, ent_coef=0.01, device="cpu")
    model.learn(total_timesteps=TIMESTEPS)
    model.save(ck)
    venv.close()
    return ck


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    series, t0 = RR.load_series()
    print("=" * 84)
    print(" 真实工况重训验证")
    print("=" * 84)
    print(f"  实测工况：{len(series)} 步（{len(series)*15/60:.1f} 小时），"
          f"有人在室 {sum(1 for s in series if s['occ'])/len(series):.0%}")
    print(f"  权重 {SCALES}，种子 {SEEDS}，每种子 {TIMESTEPS:,} 步")
    print()

    # 规则基线
    print("=== 规则基线（确定性实测工况）===")
    rule = []
    for sp in RULE_SETPOINTS:
        r = RR.run_episode(series, t0, RR.make_rule(float(sp)))
        r["setpoint"] = sp
        rule.append(r)
        print(f"  {sp:>3}°C  电量 {r['energy']:>6.3f}  "
              f"不达标 {r['discomfort']:>6.1%}  切换 {r['switch']:.1f}")
    print()

    # 重训
    print("=== 在实测工况上重训 ===")
    rl = {}
    for es in SCALES:
        rs = []
        for seed in SEEDS:
            ck = train_one(series, t0, es, seed)
            model = PPO.load(ck, device="cpu")

            def fn(env, _m=model):
                v = np.array(env.sim.to_vector(env.get_obs_dict()),
                             dtype=np.float32)
                return int(_m.predict(v, deterministic=True)[0])
            rc = RewardConfig(weight_mode="scenario")
            rc.energy_scale = float(es)
            rs.append(RR.run_episode(series, t0, fn, rc))
        rl[es] = {"energy": mean(x["energy"] for x in rs),
                  "energy_sd": pstdev([x["energy"] for x in rs]),
                  "discomfort": mean(x["discomfort"] for x in rs),
                  "switch": mean(x["switch"] for x in rs),
                  "n": len(rs),
                  "seeds": rs}
        s = rl[es]
        print(f"  es={es:<4} 电量 {s['energy']:.3f}±{s['energy_sd']:.3f}  "
              f"不达标 {s['discomfort']:.1%}  切换 {s['switch']:.1f}")
    print()

    # 对比
    front = sorted(rule, key=lambda r: r["discomfort"])
    print("=" * 84)
    print(" 重训后的等舒适度对比")
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
    print()

    dom = []
    for es in sorted(rl):
        de, ee = rl[es]["discomfort"], rl[es]["energy"]
        beat = [p["setpoint"] for p in rule
                if de <= p["discomfort"] and ee <= p["energy"]
                and (de < p["discomfort"] or ee < p["energy"])]
        if beat:
            dom.append({"scale": es, "beat": beat})
            print(f"  RL es={es:<4}（{de:.1%}, {ee:.3f}）支配规则 {beat}")

    out = {"rule": rule, "rl": {str(k): v for k, v in rl.items()},
           "equal_comfort": eq, "dominated": dom,
           "note": "训练与评估使用同一段实测工况，属样本内评估"}
    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 真实工况重训验证", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 实测工况：{len(series)} 步（{len(series)*15/60:.1f} 小时），"
          f"有人在室 {sum(1 for s in series if s['occ'])/len(series):.0%}",
          f"- 权重 {SCALES} × 种子 {SEEDS}，每种子 {TIMESTEPS:,} 步", "",
          "## 一、要回答的问题", "",
          "前一实验发现：**在合成场景训练的策略迁移到实测工况后失效**",
          "（RL es=30 的不达标率由 10.2% 恶化到 64.7%）。",
          "根本原因是占用率差异（合成 62% vs 实测 96%）。", "",
          "本实验回答：**用真实工况重新训练后，强化学习能否重新取得优势？**", "",
          "## 二、口径与局限", "",
          "训练环境为实测工况复演 + 随机化（初始室温 22~30 °C 随机、",
          "室外温度 ±0.5 °C 噪声），以避免对单条确定性轨迹的死记硬背；",
          "评估环境为**确定性**的同一段实测工况。", "",
          "⚠️ **因此评估属\"样本内\"性能**，这是本实验的局限。",
          "但它足以回答\"重训能否修复迁移失效\"这一问题；",
          "规则不涉及训练，该局限不影响两者的相对比较。", "",
          "## 三、规则基线", "",
          "| 设定温度 | 电量(kWh/天) | 不达标 | 切换(次/天) |",
          "|---|---|---|---|"]
    for r in rule:
        md.append(f"| {r['setpoint']} °C | {r['energy']:.3f} | "
                  f"{r['discomfort']:.1%} | {r['switch']:.1f} |")
    md += ["", "## 四、重训后的强化学习", "",
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
    print()
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
