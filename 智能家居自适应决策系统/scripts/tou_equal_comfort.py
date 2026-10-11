r"""
分时电价场景的等舒适度比较 —— 严谨评判"规则 vs 强化学习"

背景与要解决的问题：
    此前实验得到"分时电价场景下人工规则电费更低"的结论
    （规则 1.87~1.97 元/天 vs PPO 2.8~3.3 元/天）。

    但复查数据发现一个**评判口径问题**：
        规则控制的不达标率约 32~34%
        PPO 的不达标率约 12~16%
    两者**不在同一舒适度水平**上比较——规则是"用舒适度换电费"。

    因此"规则更优"这个结论是不严谨的。要得到可信结论，
    必须做**等舒适度比较**：在相同不达标率下比电费。

本脚本的做法：
    1. 对多个温度惩罚强度（temp_scale）训练 PPO 与 RecurrentPPO，
       得到各自的（电费, 不达标率）帕累托曲线
    2. 同时扫描规则策略的温度设定（构造规则的帕累托曲线）
    3. 在匹配的舒适度水平上比较电费

    若在等舒适度下 PPO 电费更低，则说明此前的结论是口径造成
    的假象；若仍然更高，则结论成立。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\tou_equal_comfort.py --quick
    python scripts\tou_equal_comfort.py --seeds 2 --episodes 8
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
from sb3_contrib import RecurrentPPO
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
OUT_DIR = PROJECT_ROOT / "models" / "tou_equal_comfort"
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


def make_rule_policy(valley_t, flat_t, peak_t):
    """构造按时段调温的规则策略。"""
    def policy(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        tier = obs["price_tier"]
        target = valley_t if tier == 0.0 else (peak_t if tier == 2.0 else flat_t)
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


# ============================================================
# 评估
# ============================================================
def _summ(arr):
    a = np.array(arr, dtype=float)
    n = len(a)
    return {
        "mean": float(a.mean()) if n else 0.0,
        "std": float(a.std(ddof=1)) if n > 1 else 0.0,
        "ci95": float(1.96 * a.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0,
        "n": n,
    }


def evaluate_any(env_maker, action_fn, n_episodes, seed0, reset_fn=None):
    """评价任意策略，返回 (电费, 不达标率, 谷段占比)。

    reset_fn: 循环网络每回合需要重置状态时传入的回调。
    """
    costs, discom, valleys, energies = [], [], [], []
    for ep in range(n_episodes):
        if reset_fn:
            reset_fn()
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        tier_e = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            tier_e[c["price_tier"]] += c["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                days = max(m["total_steps"] * env.sim_cfg.dt_minutes
                           / 60.0 / 24.0, 1e-9)
                costs.append(cost / days)
                discom.append(float(m["discomfort_ratio"]))
                energies.append(float(m["energy_per_day_kwh"]))
                tot = sum(tier_e.values())
                valleys.append(tier_e["valley"] / tot if tot > 0 else 0.0)
                break
        env.close()
    return {
        "cost": _summ(costs),
        "discomfort": _summ(discom),
        "energy": _summ(energies),
        "valley_share": _summ(valleys),
    }


def make_model_fn(model, is_recurrent):
    """把模型包装成动作函数。"""
    if is_recurrent:
        state = {"lstm": None}

        def fn(env):
            obs = np.array(env.sim.to_vector(env.get_obs_dict()),
                           dtype=np.float32)
            if state["lstm"] is None:
                a, state["lstm"] = model.predict(
                    obs, state=None, episode_start=np.array([True]),
                    deterministic=True)
            else:
                a, state["lstm"] = model.predict(
                    obs, state=state["lstm"],
                    episode_start=np.array([False]), deterministic=True)
            return int(a)

        def reset():
            state["lstm"] = None
        return fn, reset

    def fn(env):
        obs = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(obs, deterministic=True)[0])
    return fn, None


# ============================================================
# 训练
# ============================================================
def train(algo, temp_scale, seed, timesteps, tou=True, n_envs=4):
    rew = RewardConfig(weight_mode="scenario", tou_enabled=tou,
                       temp_scale=temp_scale)
    vec = DummyVecEnv([
        (lambda i=i: Monitor(SmartHomeEnv(rew_cfg=rew, seed=seed + i)))
        for i in range(n_envs)])
    if algo == "recurrent":
        model = RecurrentPPO(
            "MlpLstmPolicy", vec, verbose=0, seed=seed, gamma=0.99,
            n_steps=512, batch_size=128, learning_rate=3e-4, ent_coef=0.01,
            policy_kwargs=dict(net_arch=[128, 128], lstm_hidden_size=128,
                               n_lstm_layers=1))
    else:
        model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                    n_steps=1024, batch_size=256, learning_rate=3e-4,
                    ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
    model.learn(total_timesteps=timesteps)
    vec.close()
    return model


def main():
    ap = argparse.ArgumentParser(description="TOU 场景等舒适度比较")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--temp-scales", type=float, nargs="+",
                    default=[0.3, 0.6, 1.0, 2.0])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 4, 80000
        args.temp_scales = [0.5, 2.0]

    t0 = time.time()
    rew = RewardConfig(weight_mode="scenario", tou_enabled=True)

    print("=" * 80)
    print(" 分时电价场景的等舒适度比较")
    print("=" * 80)
    print(f"  温度惩罚强度扫描 : {args.temp_scales}")
    print(f"  种子数 {args.seeds}，每种子评估 {args.episodes} 回合")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  说明：temp_scale 越小，算法越愿意容忍不舒适以省电，")
    print("        因此扫描它可以得到一条'电费—舒适度'权衡曲线。")

    # ---------- 规则的权衡曲线（扫描时段设定温度）----------
    print("\n" + "-" * 80)
    print(" 规则控制的权衡曲线（扫描峰段设定温度）")
    print("-" * 80)
    print(f"  {'谷/平/峰设定':<18}{'电费元/天':>12}{'不达标':>10}{'谷段占比':>10}")
    print("  " + "-" * 58)
    rule_points = []
    for peak_t in [27.0, 28.0, 29.0, 30.0]:
        pol = make_rule_policy(25.0, 26.0, peak_t)
        r = evaluate_any(lambda: SmartHomeEnv(rew_cfg=rew), pol,
                         args.episodes * args.seeds, seed0=100000)
        rule_points.append((peak_t, r))
        print(f"  25/26/{peak_t:<11.0f}{r['cost']['mean']:>12.3f}"
              f"{r['discomfort']['mean']:>10.2%}"
              f"{r['valley_share']['mean']:>10.1%}")

    # ---------- PPO 与 RecurrentPPO 的权衡曲线 ----------
    algo_results = {}
    for algo, label in [("ppo", "普通 PPO"), ("recurrent", "RecurrentPPO")]:
        print("\n" + "-" * 80)
        print(f" {label}：扫描温度惩罚强度")
        print("-" * 80)
        print(f"  {'temp_scale':<14}{'电费元/天':>12}{'不达标':>10}{'谷段占比':>10}")
        print("  " + "-" * 58)

        pts = []
        for ts in args.temp_scales:
            costs, disc, valleys = [], [], []
            for si in range(args.seeds):
                seed = 900 + si * 31
                model = train(algo, ts, seed, args.timesteps)
                fn, reset = make_model_fn(model, algo == "recurrent")
                r = evaluate_any(lambda: SmartHomeEnv(rew_cfg=rew), fn,
                                 args.episodes,
                                 seed0=500000 + si * 1000, reset_fn=reset)
                costs.append(r["cost"]["mean"])
                disc.append(r["discomfort"]["mean"])
                valleys.append(r["valley_share"]["mean"])
            c_m, d_m, v_m = (_summ(costs), _summ(disc), _summ(valleys))
            pts.append((ts, {"cost": c_m, "discomfort": d_m,
                             "valley_share": v_m}))
            print(f"  {ts:<14.2f}{c_m['mean']:>12.3f}"
                  f"{d_m['mean']:>10.2%}{v_m['mean']:>10.1%}")
        algo_results[label] = pts

    # ---------- 等舒适度比较 ----------
    print("\n" + "=" * 80)
    print(" 等舒适度比较（核心结论）")
    print("=" * 80)

    # 规则在各舒适度水平下的最低电费
    rule_curve = [(r["discomfort"]["mean"], r["cost"]["mean"], pk)
                  for pk, r in rule_points]
    rule_curve.sort()

    def interp_cost(curve, target_d):
        """在曲线上按不达标率插值出电费（线性插值）。"""
        xs = [c[0] for c in curve]
        ys = [c[1] for c in curve]
        if target_d <= xs[0]:
            return ys[0]
        if target_d >= xs[-1]:
            return ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= target_d <= xs[i + 1]:
                if xs[i + 1] - xs[i] < 1e-9:
                    return ys[i]
                w = (target_d - xs[i]) / (xs[i + 1] - xs[i])
                return ys[i] + w * (ys[i + 1] - ys[i])
        return ys[-1]

    print(f"\n  {'策略':<20}{'不达标':>10}{'电费元/天':>12}{'规则同舒适度电费':>18}"
          f"{'对比':>10}")
    print("  " + "-" * 72)

    summary = {}
    for label, pts in algo_results.items():
        for ts, r in pts:
            d = r["discomfort"]["mean"]
            c = r["cost"]["mean"]
            rule_c = interp_cost(rule_curve, d)
            diff = (c - rule_c) / rule_c if rule_c > 0 else 0.0
            mark = "更优" if c < rule_c else "更差"
            print(f"  {label+'('+str(ts)+')':<20}{d:>10.2%}{c:>12.3f}"
                  f"{rule_c:>18.3f}{diff:>9.1%} {mark}")
            summary[f"{label}_ts{ts}"] = {
                "discomfort": d, "cost": c,
                "rule_cost_at_same_comfort": rule_c, "diff": diff,
            }

    # ---------- 结论 ----------
    print("\n" + "=" * 80)
    print(" 结论")
    print("=" * 80)

    # 规则自身的最优点
    best_rule = min(rule_curve, key=lambda x: x[1])
    print(f"\n  规则控制的最优点：不达标 {best_rule[0]:.1%} 时电费 "
          f"{best_rule[1]:.3f} 元/天")

    # 所有 RL 配置中，等舒适度下超过规则的比例
    wins = sum(1 for v in summary.values() if v["diff"] < 0)
    total = len(summary)
    print(f"\n  在 {total} 个强化学习配置中，等舒适度下电费低于规则的有 "
          f"{wins} 个（{wins/total:.0%}）")

    if wins > 0:
        best_key = min(summary, key=lambda k: summary[k]["diff"])
        bs = summary[best_key]
        print(f"\n  最优配置：{best_key}")
        print(f"    不达标 {bs['discomfort']:.2%}，电费 {bs['cost']:.3f} 元/天")
        print(f"    同舒适度下规则需要 {bs['rule_cost_at_same_comfort']:.3f} 元/天")
        print(f"    -> 本方法相对规则降低 {abs(bs['diff']):.1%}")
        print(f"\n  [结论] 在等舒适度条件下，强化学习可以超过人工规则。")
        print(f"         此前'规则更优'的结论是**不同舒适度水平比较**造成的假象。")
    else:
        print(f"\n  [结论] 在等舒适度条件下，强化学习仍未超过人工规则。")
        print(f"         说明规则在该场景下确实更有效，")
        print(f"         '缺少记忆'不是唯一原因，需要显式储能建模或")
        print(f"         把电价预期直接写入奖励。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    # 规则曲线
    rc = sorted([(r["discomfort"]["mean"], r["cost"]["mean"])
                 for _, r in rule_points])
    ax.plot([c[0] * 100 for c in rc], [c[1] for c in rc], "s-",
            color="#1f77b4", markersize=8, label="规则控制（扫描峰段设定）")
    colors = {"普通 PPO": "#ff7f0e", "RecurrentPPO": "#2ca02c"}
    for label, pts in algo_results.items():
        xs = [r["discomfort"]["mean"] * 100 for _, r in pts]
        ys = [r["cost"]["mean"] for _, r in pts]
        ax.plot(xs, ys, "o--", color=colors.get(label, "gray"),
                markersize=8, label=label)
    ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
    ax.set_ylabel("日均电费 (元)  —— 越下越好")
    ax.set_title("电费—舒适度权衡曲线", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    labels = list(summary)
    diffs = [summary[k]["diff"] * 100 for k in labels]
    cols = ["#2ca02c" if d < 0 else "#d62728" for d in diffs]
    ax.barh(labels, diffs, color=cols)
    ax.axvline(0, color="black", linewidth=1)
    ax.set_xlabel("相对规则的电费差异 (%)  —— 负值表示本方法更优")
    ax.set_title("等舒适度比较：相对规则的电费差异", fontsize=12)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(alpha=0.3, axis="x", linestyle=":")

    fig.suptitle("分时电价场景：等舒适度下的公平比较", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "tou_equal_comfort.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({
            "args": vars(args),
            "rule_points": [(pk, {k: v for k, v in r.items()})
                            for pk, r in rule_points],
            "algo_results": {lab: [(ts, r) for ts, r in pts]
                             for lab, pts in algo_results.items()},
            "equal_comfort": summary,
        }, f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/tou_equal_comfort/results.json")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
