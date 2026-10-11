r"""
舒适度—能耗权重重标定 —— 寻找能超过规则的奖励配置

问题诊断（v3 发现）：
    在分时电价场景下规则电费显著低于 PPO。根本原因经量化诊断如下：

        超出舒适带 1°C 的舒适代价 = alpha_hot × temp_scale = 1.0 × 3.0 = 3.0
        峰段一个 15 分钟步的能耗代价 = λ × energy_scale × price × E
                                     ≈ 1.0 × 10.0 × 0.78 × 0.06 = 0.468
        => 舒适度代价是能耗代价的 **6 倍**

    也就是说，当前奖励配置下**算法宁可多耗电，也绝不让温度偏离舒适带**。
    而人工规则恰恰靠"峰段允许温度升到 28°C"来省电——这个行为在
    当前奖励下是"不划算"的，因此 PPO 在结构上被禁止了规则的做法。

    注意：单纯提高 energy_scale 无效（实测 10→40 能耗几乎不变），
    因为它同时放大了能耗项，但舒适项仍占绝对优势。
    **真正的杠杆是降低 temp_scale**。

本脚本：
    扫描 `temp_scale`（舒适度权重），并同时构造规则的"电费—舒适度"
    权衡曲线，从而在**匹配的舒适度水平**上比较两者。
    这是回答"RL 能否超过规则"的正确口径。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\reward_rebalance.py --quick
    python scripts\reward_rebalance.py --seeds 2 --episodes 8
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
OUT_DIR = PROJECT_ROOT / "models" / "reward_rebalance"
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


def make_rule(peak_t, flat_t=26.0, valley_t=25.0, tou=True):
    """构造按时段调温的规则策略。"""
    def policy(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        if tou:
            tier = obs["price_tier"]
            target = (valley_t if tier == 0.0
                      else (peak_t if tier == 2.0 else flat_t))
        else:
            target = flat_t
        t = obs["temp_in"]
        if t > target + 0.5:
            ac = _ac_idx_for(target, env.act_cfg)
        elif t < target - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return policy


def evaluate(env_maker, action_fn, n_episodes, seed0):
    costs, disc, energy, valleys = [], [], [], []
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
                valleys.append(te["valley"] / tot if tot > 0 else 0.0)
                break
        env.close()
    n = len(costs)
    return {
        "cost": float(np.mean(costs)),
        "cost_sd": float(np.std(costs, ddof=1)) if n > 1 else 0.0,
        "discomfort": float(np.mean(disc)),
        "energy": float(np.mean(energy)),
        "valley_share": float(np.mean(valleys)),
        "n": n,
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


def matched_rule_cost(rule_curve, target_d):
    """在规则的（不达标率, 电费）曲线上按不达标率插值出电费。

    rule_curve 需按不达标率升序。返回 (cost, status)。
    """
    xs = [c[0] for c in rule_curve]
    ys = [c[1] for c in rule_curve]
    if target_d < xs[0] - 1e-9:
        return None, "below"
    if target_d > xs[-1] + 1e-9:
        return None, "above"
    for i in range(len(xs) - 1):
        if xs[i] <= target_d <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            if span < 1e-9:
                return ys[i], "ok"
            w = (target_d - xs[i]) / span
            return ys[i] + w * (ys[i + 1] - ys[i]), "ok"
    return ys[-1], "ok"


def main():
    ap = argparse.ArgumentParser(description="舒适度—能耗权重重标定")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--temp-scales", type=float, nargs="+",
                    default=[0.10, 0.15, 0.20, 0.25])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 5, 120000
        args.temp_scales = [0.06, 0.12, 0.20]

    t0 = time.time()
    print("=" * 92)
    print(" 舒适度—能耗权重重标定 —— 寻找能超过规则的奖励配置")
    print("=" * 92)
    print(f"  temp_scale 扫描: {args.temp_scales}")
    print(f"  种子 {args.seeds}，每种子评估 {args.episodes} 回合，")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  诊断结论（本脚本的出发点）：")
    print("    超出舒适带 1°C 的代价 = 1.0 × temp_scale")
    print("    峰段一步的能耗代价   ≈ 0.468（energy_scale=10 时）")
    print("    temp_scale=3.0 时舒适代价是能耗代价的 6 倍")
    print("    => 算法被禁止采用规则那种'让温度飘高以省电'的做法")
    print("    因此需要降低 temp_scale，让能耗与舒适度真正处于可比量级。")

    # ---------- 规则的权衡曲线（扫描峰段设定温度）----------
    print("\n" + "-" * 92)
    print(" 规则控制的'电费—舒适度'权衡曲线")
    print("-" * 92)
    rew_base = RewardConfig(weight_mode="scenario", tou_enabled=True)
    rule_curve = []
    print(f"  {'峰段设定':>10}{'电费元/天':>12}{'不达标率':>11}{'能耗kWh/天':>13}"
          f"{'谷段占比':>11}")
    print("  " + "-" * 58)
    for peak_t in [26.0, 27.0, 28.0, 29.0, 30.0, 31.0]:
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew_base),
                     make_rule(peak_t), args.episodes * args.seeds, 100000)
        rule_curve.append((r["discomfort"], r["cost"], peak_t, r))
        print(f"  {peak_t:>10.1f}{r['cost']:>12.3f}{r['discomfort']:>11.2%}"
              f"{r['energy']:>13.3f}{r['valley_share']:>11.1%}")
    rule_curve.sort(key=lambda x: x[0])
    rule_d_range = (rule_curve[0][0], rule_curve[-1][0])
    print(f"\n  规则可达的不达标率范围: {rule_d_range[0]:.1%} ~ "
          f"{rule_d_range[1]:.1%}")

    # ---------- 扫描 temp_scale ----------
    print("\n" + "-" * 92)
    print(" PPO：扫描 temp_scale")
    print("-" * 92)
    print(f"  {'temp_scale':>11}{'电费元/天':>11}{'不达标':>9}{'能耗':>9}"
          f"{'谷段占比':>10}{'规则同舒适度电费':>18}{'对比':>10}{'判定':>10}")
    print("  " + "-" * 86)

    results = []
    for ts in args.temp_scales:
        rew = RewardConfig(weight_mode="scenario", tou_enabled=True,
                           temp_scale=ts)
        ms = []
        for si in range(args.seeds):
            seed = 3000 + si * 53
            model = train_ppo(rew, seed, args.timesteps)
            ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                               make_model_fn(model), args.episodes,
                               seed0=650000 + si * 1000))
        avg = {k: float(np.mean([m[k] for m in ms])) for k in ms[0]}

        rc, status = matched_rule_cost(rule_curve, avg["discomfort"])
        if status == "ok" and rc:
            diff = (avg["cost"] - rc) / rc
            verdict = "超过规则" if diff < 0 else "仍逊于规则"
            diff_s = f"{diff:>+9.1%}"
            rc_s = f"{rc:>18.3f}"
        else:
            diff = float("nan")
            verdict = {"below": "规则做不到", "above": "规则做不到"}[status]
            diff_s = f"{'—':>10}"
            rc_s = f"{'—':>18}"

        results.append({"temp_scale": ts, "metrics": avg,
                        "rule_cost_matched": rc, "status": status,
                        "diff": diff, "verdict": verdict})
        print(f"  {ts:>11.2f}{avg['cost']:>11.3f}"
              f"{avg['discomfort']:>9.2%}{avg['energy']:>9.3f}"
              f"{avg['valley_share']:>10.1%}{rc_s}{diff_s}{verdict:>12}")

    # ---------- 分析 ----------
    print("\n" + "=" * 92)
    print(" 分析")
    print("=" * 92)

    valid = [r for r in results if r["status"] == "ok"]
    wins = [r for r in valid if r["diff"] < 0]
    print(f"\n  可比较的配置: {len(valid)}/{len(results)}")
    print(f"  等舒适度下电费低于规则的: {len(wins)} 个")

    if valid:
        print(f"\n  各配置在等舒适度下的电费差异：")
        for r in sorted(valid, key=lambda x: x["diff"]):
            print(f"    temp_scale={r['temp_scale']:<5.2f} "
                  f"不达标 {r['metrics']['discomfort']:>6.2%}  "
                  f"电费 {r['metrics']['cost']:.3f} vs 规则 "
                  f"{r['rule_cost_matched']:.3f}  ->  {r['diff']:+.1%}")

    if wins:
        best = min(wins, key=lambda r: r["diff"])
        print(f"\n  ✅ [结论] 在等舒适度条件下，强化学习超过了人工规则！")
        print(f"     temp_scale = {best['temp_scale']}")
        print(f"     不达标 {best['metrics']['discomfort']:.2%}，"
              f"电费 {best['metrics']['cost']:.3f} 元/天")
        print(f"     同舒适度下规则需要 {best['rule_cost_matched']:.3f} 元/天")
        print(f"     -> 降低 {abs(best['diff']):.1%}")
        print(f"\n     这说明此前'规则更优'的结论，根源是**奖励权重标定不当**：")
        print(f"     temp_scale=3.0 时舒适代价是能耗代价的 6 倍，")
        print(f"     算法被禁止采用'让温度飘高以省电'的做法。")
    else:
        nearest = min(valid, key=lambda r: r["diff"]) if valid else None
        print(f"\n  [结论] 等舒适度下仍未超过规则。")
        if nearest:
            print(f"     最接近的配置 temp_scale={nearest['temp_scale']}，"
                  f"差异 {nearest['diff']:+.1%}")
        print(f"     需要从机制层面入手（如显式储能建模）。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))

    ax = axes[0]
    rc_x = [c[0] * 100 for c in rule_curve]
    rc_y = [c[1] for c in rule_curve]
    ax.plot(rc_x, rc_y, "s-", color="#1f77b4", markersize=8,
            label="规则控制（扫描峰段设定）")
    px = [r["metrics"]["discomfort"] * 100 for r in results]
    py = [r["metrics"]["cost"] for r in results]
    ax.plot(px, py, "o--", color="#d62728", markersize=9, label="PPO")
    for r, x, y in zip(results, px, py):
        ax.annotate(f"ts={r['temp_scale']}", (x, y), fontsize=7,
                    textcoords="offset points", xytext=(4, 4))
    ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
    ax.set_ylabel("日均电费 (元)  —— 越下越好")
    ax.set_title("电费—舒适度权衡曲线（分时电价场景）", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    valid_r = [r for r in results if r["status"] == "ok"]
    if valid_r:
        lbl = [f"{r['temp_scale']:.2f}" for r in valid_r]
        diffs = [r["diff"] * 100 for r in valid_r]
        cols = ["#2ca02c" if d < 0 else "#d62728" for d in diffs]
        ax.bar(lbl, diffs, color=cols)
        ax.axhline(0, color="black", linewidth=1)
        ax.set_xlabel("temp_scale（舒适度权重）")
        ax.set_ylabel("等舒适度下的电费差异 (%)")
        ax.set_title("等舒适度比较：负值表示 PPO 更优", fontsize=12)
        ax.grid(alpha=0.3, axis="y", linestyle=":")
    else:
        ax.text(0.5, 0.5, "无可比较的配置", ha="center", va="center",
                transform=ax.transAxes)
        ax.set_title("等舒适度比较", fontsize=12)

    fig.suptitle("奖励权重重标定：舒适度权重是决定性因素", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "reward_rebalance.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args),
                   "rule_curve": [{"discomfort": c[0], "cost": c[1],
                                   "peak_target": c[2], "metrics": c[3]}
                                  for c in rule_curve],
                   "ppo_results": results},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/reward_rebalance/results.json")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
