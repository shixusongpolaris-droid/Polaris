r"""
能量权重扫描 —— 尝试让强化学习在分时电价场景下超过规则

问题背景（v3 实验结论）：
    在分时电价场景下，规则控制的电费显著低于 PPO：
        规则 1.87~1.97 元/天  vs  PPO 2.58~3.39 元/天
    等舒适度比较后结论依然成立（8 个配置 0 个占优）。

    进一步分析发现两件事：
      (1) PPO 的总能耗高出约 45%（5.25 vs 3.61 kWh/天），
          即"能学会错峰，但学不会少用电"；
      (2) **前视电价机制的方向可能是错的**：
            峰段真实电价 1.00 元，但奖励里用的是前视电价 0.78~0.86 元，
            即**峰段惩罚被削弱了 14~22%**，反而抑制了错峰行为。

本脚本的策略：
    1. 扫描 `energy_scale`（能耗项权重），找到"愿意少用电"的强度
    2. 同时对比 **关闭前视电价** 的效果
       （`lookahead_weight=0` 时 price_eff = price_now，即按真实电价惩罚）

    若关闭前视 + 提高能耗权重后 PPO 能接近或超过规则，
    则说明此前失败的原因是奖励设计缺陷，而非强化学习本身不行。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\energy_weight_sweep.py --quick
    python scripts\energy_weight_sweep.py --seeds 2 --episodes 8
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from src.env import RewardConfig, SmartHomeEnv, encode_action

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
OUT_DIR = PROJECT_ROOT / "models" / "energy_sweep"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

AC_OFF = 0


def _ac_idx_for(target_c, act_cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def policy_rule_tou(env):
    """分时电价规则：谷段 25°C、平段 26°C、峰段 28°C。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    tier = obs["price_tier"]
    target = 25.0 if tier == 0.0 else (28.0 if tier == 2.0 else 26.0)
    t = obs["temp_in"]
    if t > target + 0.5:
        ac = _ac_idx_for(target, env.act_cfg)
    elif t < target - 0.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


# ============================================================
# 评估
# ============================================================
def evaluate(env_maker, action_fn, n_episodes, seed0):
    costs, disc, energy = [], [], []
    tiers = {"valley": 0.0, "flat": 0.0, "peak": 0.0}

    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        te = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            te[c["price_tier"]] += c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                costs.append(cost / days)
                disc.append(float(m["discomfort_ratio"]))
                energy.append(float(m["energy_per_day_kwh"]))
                tot = sum(te.values())
                for k in tiers:
                    tiers[k] += te[k] / tot if tot > 0 else 0.0
                break
        env.close()

    n = len(costs)
    return {
        "cost": float(np.mean(costs)),
        "cost_sd": float(np.std(costs, ddof=1)) if n > 1 else 0.0,
        "discomfort": float(np.mean(disc)),
        "energy": float(np.mean(energy)),
        "valley_share": tiers["valley"] / max(n, 1),
        "peak_share": tiers["peak"] / max(n, 1),
    }


def make_model_fn(model):
    def fn(env):
        obs = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(obs, deterministic=True)[0])
    return fn


def train_ppo(rew_cfg, seed, timesteps, n_envs=4):
    vec = DummyVecEnv([
        (lambda i=i: Monitor(SmartHomeEnv(rew_cfg=rew_cfg, seed=seed + i)))
        for i in range(n_envs)])
    model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                n_steps=1024, batch_size=256, learning_rate=3e-4,
                ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
    model.learn(total_timesteps=timesteps)
    vec.close()
    return model


def main():
    ap = argparse.ArgumentParser(description="能量权重扫描")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--energy-scales", type=float, nargs="+",
                    default=[10.0, 30.0, 60.0])
    ap.add_argument("--lookahead-weights", type=float, nargs="+",
                    default=[0.6, 0.0])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 4, 100000
        args.energy_scales = [10.0, 40.0]
        args.lookahead_weights = [0.0]

    t0 = time.time()
    print("=" * 88)
    print(" 能量权重扫描 —— 尝试在分时电价场景下超过规则")
    print("=" * 88)
    print(f"  energy_scale 扫描    : {args.energy_scales}")
    print(f"  lookahead_weight 扫描: {args.lookahead_weights}")
    print(f"  种子 {args.seeds}，每种子评估 {args.episodes} 回合，")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  背景：此前结论是规则电费显著更低。但发现前视电价在峰段")
    print("        把真实电价 1.00 元削弱到 0.78~0.86 元，方向可能相反。")

    # ---------- 基线：规则控制 ----------
    rew_base = RewardConfig(weight_mode="scenario", tou_enabled=True)
    rule = evaluate(lambda: SmartHomeEnv(rew_cfg=rew_base),
                    policy_rule_tou, args.episodes * args.seeds, 100000)
    print("\n" + "-" * 88)
    print(" 基线：规则控制（谷 25°C / 平 26°C / 峰 28°C）")
    print("-" * 88)
    print(f"  电费 {rule['cost']:.3f} 元/天，能耗 {rule['energy']:.3f} kWh/天，"
          f"不达标 {rule['discomfort']:.2%}")
    print(f"  谷段占比 {rule['valley_share']:.1%}，"
          f"峰段占比 {rule['peak_share']:.1%}")

    # ---------- 扫描 ----------
    results = []
    print("\n" + "-" * 88)
    print(" 扫描结果")
    print("-" * 88)
    print(f"  {'energy_scale':>13}{'前视权重':>10}{'电费元/天':>11}"
          f"{'能耗kWh/天':>12}{'不达标':>9}{'谷段占比':>10}"
          f"{'vs规则':>10}{'结果':>10}")
    print("  " + "-" * 84)

    for lw in args.lookahead_weights:
        for es in args.energy_scales:
            rew = RewardConfig(weight_mode="scenario", tou_enabled=True,
                               energy_scale=es, lookahead_weight=lw)
            metrics = []
            for si in range(args.seeds):
                seed = 2000 + si * 41
                model = train_ppo(rew, seed, args.timesteps)
                m = evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                             make_model_fn(model), args.episodes,
                             seed0=600000 + si * 1000)
                metrics.append(m)
            avg = {k: float(np.mean([m[k] for m in metrics]))
                   for k in metrics[0]}

            diff = (avg["cost"] - rule["cost"]) / rule["cost"]
            verdict = "超过规则" if diff < 0 else "仍逊于规则"
            results.append({"energy_scale": es, "lookahead_weight": lw,
                            "metrics": avg, "diff_vs_rule": diff,
                            "verdict": verdict})

            print(f"  {es:>13.1f}{lw:>10.2f}{avg['cost']:>11.3f}"
                  f"{avg['energy']:>12.3f}{avg['discomfort']:>9.2%}"
                  f"{avg['valley_share']:>10.1%}"
                  f"{diff:>+10.1%}{verdict:>10}")

    # ---------- 分析 ----------
    print("\n" + "=" * 88)
    print(" 分析")
    print("=" * 88)

    wins = [r for r in results if r["diff_vs_rule"] < 0]
    print(f"\n  在 {len(results)} 个配置中，电费低于规则的有 {len(wins)} 个")

    # 关掉前视的效果
    la_on = [r for r in results if r["lookahead_weight"] > 0]
    la_off = [r for r in results if r["lookahead_weight"] == 0]
    if la_on and la_off:
        # 在相同 energy_scale 下比较
        for es in args.energy_scales:
            on = next((r for r in la_on if r["energy_scale"] == es), None)
            off = next((r for r in la_off if r["energy_scale"] == es), None)
            if on and off:
                print(f"\n  energy_scale={es} 时，关闭前视电价的效果：")
                print(f"    开前视: 电费 {on['metrics']['cost']:.3f}, "
                      f"谷段占比 {on['metrics']['valley_share']:.1%}")
                print(f"    关前视: 电费 {off['metrics']['cost']:.3f}, "
                      f"谷段占比 {off['metrics']['valley_share']:.1%}")
                dc = off['metrics']['cost'] - on['metrics']['cost']
                dv = off['metrics']['valley_share'] - on['metrics']['valley_share']
                print(f"    变化  : 电费 {dc:+.3f} 元/天，"
                      f"谷段占比 {dv:+.1%}")

    if wins:
        best = min(wins, key=lambda r: r["diff_vs_rule"])
        print(f"\n  [结论] 找到超过规则的配置！")
        print(f"    energy_scale={best['energy_scale']}, "
              f"lookahead_weight={best['lookahead_weight']}")
        print(f"    电费 {best['metrics']['cost']:.3f} vs 规则 "
              f"{rule['cost']:.3f} 元/天（{best['diff_vs_rule']:+.1%}）")
        print(f"    不达标 {best['metrics']['discomfort']:.2%} vs 规则 "
              f"{rule['discomfort']:.2%}")
    else:
        best = min(results, key=lambda r: r["diff_vs_rule"])
        print(f"\n  [结论] 仍未超过规则，但最佳配置已接近")
        print(f"    energy_scale={best['energy_scale']}, "
              f"lookahead_weight={best['lookahead_weight']}")
        print(f"    电费 {best['metrics']['cost']:.3f} vs 规则 "
              f"{rule['cost']:.3f} 元/天（{best['diff_vs_rule']:+.1%}）")
        print(f"\n    说明：仅靠调整奖励权重不足以弥补差距，")
        print(f"          需要从'降低总能耗'的机制层面入手（如显式储能建模）。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    labels = [f"es={r['energy_scale']:.0f}\nla={r['lookahead_weight']:.1f}"
              for r in results]
    colors = ["#2ca02c" if r["diff_vs_rule"] < 0 else "#d62728"
              for r in results]

    ax = axes[0]
    ax.bar(labels, [r["metrics"]["cost"] for r in results], color=colors)
    ax.axhline(rule["cost"], color="#1f77b4", linestyle="--", linewidth=1.5,
               label=f"规则 {rule['cost']:.2f}")
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("电费对比（虚线为规则）", fontsize=11)
    ax.legend(fontsize=9)
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    ax.bar(labels, [r["metrics"]["energy"] for r in results], color=colors)
    ax.axhline(rule["energy"], color="#1f77b4", linestyle="--", linewidth=1.5,
               label=f"规则 {rule['energy']:.2f}")
    ax.set_ylabel("日均能耗 (kWh)")
    ax.set_title("能耗对比（虚线为规则）", fontsize=11)
    ax.legend(fontsize=9)
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[2]
    ax.bar(labels, [r["metrics"]["valley_share"] * 100 for r in results],
           color=colors)
    ax.axhline(rule["valley_share"] * 100, color="#1f77b4", linestyle="--",
               linewidth=1.5, label=f"规则 {rule['valley_share']:.0%}")
    ax.axhline(33.3, color="gray", linestyle=":", linewidth=1.5,
               label="时间占比 33%")
    ax.set_ylabel("谷段用电占比 (%)")
    ax.set_title("错峰效果（虚线为规则）", fontsize=11)
    ax.legend(fontsize=9)
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("能量权重扫描：能否让强化学习在分时电价场景下超过规则",
                 fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "energy_weight_sweep.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "rule_baseline": rule,
                   "results": results}, f, ensure_ascii=False,
                  indent=2, default=float)
    print(f"  结果已保存: models/energy_sweep/results.json")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
