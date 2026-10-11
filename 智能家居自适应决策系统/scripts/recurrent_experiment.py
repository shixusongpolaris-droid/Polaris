r"""
循环网络对比实验 —— RecurrentPPO vs 普通 PPO（针对分时电价场景）

背景：
    此前实验发现，在分时电价（TOU）场景下，普通 PPO **学不会错峰用电**：
    其用电在时间上近乎均匀分布（谷段占比 24%，甚至低于时间占比 33%），
    而人工规则能做到谷段占比 47.5%，电费低约 35%。

    原因分析：错峰用电本质是**时序决策**问题——需要"记住"当前处于哪个
    电价阶段、距离下一个阶段还有多久。而普通 PPO 使用 MLP 策略，
    **没有记忆能力**，这是它学不会错峰的结构性原因。

本实验：
    把策略网络换成 LSTM（RecurrentPPO），看是否能学会错峰。

    对比三组：
      B1  规则控制（按时段调温）  —— 人工基线
      B2  普通 PPO                —— 无记忆
      B3  RecurrentPPO（本实验）  —— 有记忆

    判据：B3 的谷段用电占比是否显著高于 B2，电费是否更低。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\recurrent_experiment.py --seeds 3 --episodes 10
    python scripts\recurrent_experiment.py --quick      # 快速试跑

输出：
    models\recurrent\rl_*.zip            训练好的模型
    models\recurrent\results.json        实验结果
    models\figures\recurrent_compare.png 对比图
"""

import argparse
import csv
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
OUT_DIR = PROJECT_ROOT / "models" / "recurrent"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5


# ============================================================
# 基线：按时段调温的规则控制
# ============================================================
def _ac_idx_for(target_c, act_cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def policy_rule_tou(env):
    """谷段 25°C、平段 26°C、峰段 28°C。"""
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
# 评估（含分时段用电统计）
# ============================================================
def evaluate_fn(env_maker, action_fn, n_episodes, seed0, record_tiers=True):
    """评价任意动作函数，返回指标与分时段用电统计。"""
    keys = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
            "switch_per_day", "response_delay_mean"]
    acc = {k: [] for k in keys}
    acc["energy_cost_yuan"] = []
    tier_e = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
    tier_steps = {"valley": 0, "flat": 0, "peak": 0}

    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            if record_tiers:
                tier = c["price_tier"]
                tier_e[tier] += c["energy_kwh"]
                tier_steps[tier] += 1
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                for k in keys:
                    acc[k].append(float(m.get(k, 0.0)))
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                acc["energy_cost_yuan"].append(cost / days)
                break
        env.close()

    out = {}
    for k, v in acc.items():
        arr = np.array(v, dtype=float)
        n = len(arr)
        m = float(arr.mean()) if n else 0.0
        s = float(arr.std(ddof=1)) if n > 1 else 0.0
        ci = 1.96 * s / np.sqrt(n) if n > 1 else 0.0
        out[k] = {"mean": m, "std": s, "ci95": ci, "n": n}

    total_e = sum(tier_e.values())
    total_s = sum(tier_steps.values())
    out["tier_share"] = {k: (tier_e[k] / total_e if total_e > 0 else 0.0)
                         for k in tier_e}
    out["tier_time_share"] = {k: (tier_steps[k] / total_s if total_s > 0 else 0.0)
                              for k in tier_steps}
    return out


def evaluate_model(model, env_maker, n_episodes, seed0, is_recurrent=False):
    """评价训练好的模型（区分是否循环网络）。"""
    def fn(env):
        obs = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        if is_recurrent:
            # 循环网络需要传入并更新 LSTM 状态
            if fn.lstm_state is None:
                action, fn.lstm_state = model.predict(
                    obs, state=None, episode_start=np.array([True]),
                    deterministic=True)
            else:
                action, fn.lstm_state = model.predict(
                    obs, state=fn.lstm_state,
                    episode_start=np.array([False]), deterministic=True)
            return int(action)
        return int(model.predict(obs, deterministic=True)[0])

    fn.lstm_state = None

    # 逐回合重置 LSTM 状态
    orig_maker = env_maker
    results_acc = None
    all_tiers = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
    all_steps = {"valley": 0, "flat": 0, "peak": 0}
    keys = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
            "switch_per_day", "response_delay_mean"]
    acc = {k: [] for k in keys}
    acc["energy_cost_yuan"] = []

    for ep in range(n_episodes):
        fn.lstm_state = None          # 每回合重置记忆
        env = orig_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            tier = c["price_tier"]
            all_tiers[tier] += c["energy_kwh"]
            all_steps[tier] += 1
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                for k in keys:
                    acc[k].append(float(m.get(k, 0.0)))
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                acc["energy_cost_yuan"].append(cost / days)
                break
        env.close()

    out = {}
    for k, v in acc.items():
        arr = np.array(v, dtype=float)
        n = len(arr)
        m = float(arr.mean()) if n else 0.0
        s = float(arr.std(ddof=1)) if n > 1 else 0.0
        ci = 1.96 * s / np.sqrt(n) if n > 1 else 0.0
        out[k] = {"mean": m, "std": s, "ci95": ci, "n": n}
    total_e = sum(all_tiers.values())
    total_s = sum(all_steps.values())
    out["tier_share"] = {k: (all_tiers[k] / total_e if total_e > 0 else 0.0)
                         for k in all_tiers}
    out["tier_time_share"] = {k: (all_steps[k] / total_s if total_s > 0 else 0.0)
                              for k in all_steps}
    return out


# ============================================================
# 训练
# ============================================================
def train_algo(algo: str, seed: int, timesteps: int, n_envs: int = 4,
               tou: bool = True, verbose: bool = True):
    """训练指定算法，返回模型。algo: 'ppo' 或 'recurrent'。"""
    rew = RewardConfig(weight_mode="scenario", tou_enabled=tou)
    vec = DummyVecEnv([
        (lambda i=i: Monitor(SmartHomeEnv(rew_cfg=rew, seed=seed + i)))
        for i in range(n_envs)])

    if algo == "recurrent":
        model = RecurrentPPO(
            "MlpLstmPolicy", vec, verbose=0, seed=seed, gamma=0.99,
            n_steps=512, batch_size=128, learning_rate=3e-4,
            ent_coef=0.01,
            policy_kwargs=dict(
                net_arch=[128, 128],
                lstm_hidden_size=128,
                n_lstm_layers=1,
            ),
        )
    else:
        model = PPO(
            "MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
            n_steps=1024, batch_size=256, learning_rate=3e-4,
            ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]),
        )

    t0 = time.time()
    model.learn(total_timesteps=timesteps)
    dt = time.time() - t0
    vec.close()

    n_params = sum(p.numel() for p in model.policy.parameters())
    if verbose:
        print(f"      训练完成：{dt/60:.1f} 分钟，参数 {n_params:,} 个")
    return model, n_params, dt


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="RecurrentPPO 对比实验")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--timesteps", type=int, default=300000)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 3, 60000

    t0 = time.time()
    print("=" * 78)
    print(" 循环网络对比实验 —— RecurrentPPO vs 普通 PPO（分时电价场景）")
    print("=" * 78)
    print(f"  随机种子 {args.seeds} 个，每种子评估 {args.episodes} 回合")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  对比三组：")
    print("    B1 规则控制（按时段调温）—— 人工基线")
    print("    B2 普通 PPO              —— MLP 策略，无记忆")
    print("    B3 RecurrentPPO          —— LSTM 策略，有记忆")

    rew = RewardConfig(weight_mode="scenario", tou_enabled=True)
    results = {}

    # ---- B1 规则控制 ----
    print("\n" + "-" * 78)
    print(" B1：规则控制（按时段调温）")
    print("-" * 78)
    b1 = evaluate_fn(lambda: SmartHomeEnv(rew_cfg=rew), policy_rule_tou,
                     args.episodes * args.seeds, seed0=100000)
    results["B1 规则控制"] = b1
    print(f"  电费 {b1['energy_cost_yuan']['mean']:.3f} 元/天，"
          f"不达标 {b1['discomfort_ratio']['mean']:.2%}")
    print(f"  谷段用电占比 {b1['tier_share']['valley']:.1%}，"
          f"峰段 {b1['tier_share']['peak']:.1%}")

    # ---- B2 / B3 训练 ----
    algo_specs = [("ppo", "B2 普通 PPO"), ("recurrent", "B3 RecurrentPPO")]
    for algo, label in algo_specs:
        print("\n" + "-" * 78)
        print(f" {label}")
        print("-" * 78)
        acc_keys = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
                    "switch_per_day", "response_delay_mean"]
        agg = {k: [] for k in acc_keys}
        agg["energy_cost_yuan"] = []
        tiers = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
        tsteps = {"valley": 0, "flat": 0, "peak": 0}
        param_counts = []

        for si in range(args.seeds):
            seed = 700 + si * 23
            print(f"    种子 {seed} …")
            model, n_par, dt = train_algo(algo, seed, args.timesteps)
            param_counts.append(n_par)

            r = evaluate_model(model, lambda: SmartHomeEnv(rew_cfg=rew),
                               args.episodes, seed0=400000 + si * 1000,
                               is_recurrent=(algo == "recurrent"))
            for k in acc_keys:
                agg[k].append(r[k]["mean"])
            agg["energy_cost_yuan"].append(r["energy_cost_yuan"]["mean"])
            for k in tiers:
                tiers[k] += r["tier_share"][k] / args.seeds
                tsteps[k] += r["tier_time_share"][k] / args.seeds

            print(f"      电费 {r['energy_cost_yuan']['mean']:.3f} 元/天，"
                  f"不达标 {r['discomfort_ratio']['mean']:.2%}，"
                  f"谷段占比 {r['tier_share']['valley']:.1%}")

            if si == 0:
                model.save(str(OUT_DIR / f"rl_{algo}"))

        # 汇总（跨种子均值）
        summ = {}
        for k in agg:
            arr = np.array(agg[k], dtype=float)
            n = len(arr)
            summ[k] = {
                "mean": float(arr.mean()),
                "std": float(arr.std(ddof=1)) if n > 1 else 0.0,
                "ci95": float(1.96 * arr.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0,
                "n": n,
            }
        summ["tier_share"] = tiers
        summ["tier_time_share"] = tsteps
        summ["n_params"] = float(np.mean(param_counts))
        results[label] = summ

    # ---- 结果表 ----
    print("\n" + "=" * 78)
    print(" 结果汇总")
    print("=" * 78)
    print(f"\n  {'策略':<22}{'电费元/天':>12}{'不达标':>10}"
          f"{'谷段占比':>10}{'峰段占比':>10}{'参数量':>12}")
    print("  " + "-" * 74)
    for name, r in results.items():
        print(f"  {name:<22}{r['energy_cost_yuan']['mean']:>12.3f}"
              f"{r['discomfort_ratio']['mean']:>10.2%}"
              f"{r['tier_share']['valley']:>10.1%}"
              f"{r['tier_share']['peak']:>10.1%}"
              f"{r.get('n_params', 0):>12,.0f}")
    print("  " + "-" * 74)
    ts = results["B1 规则控制"]["tier_time_share"]
    print(f"  {'（时间占比基准）':<22}{'':>12}{'':>10}"
          f"{ts['valley']:>10.1%}{ts['peak']:>10.1%}")

    # ---- 关键判断 ----
    print("\n" + "=" * 78)
    print(" 关键判断：循环网络是否解决了错峰问题")
    print("=" * 78)

    b2 = results.get("B2 普通 PPO")
    b3 = results.get("B3 RecurrentPPO")
    b1r = results["B1 规则控制"]

    if b2 and b3:
        v2 = b2["tier_share"]["valley"]
        v3 = b3["tier_share"]["valley"]
        c2 = b2["energy_cost_yuan"]["mean"]
        c3 = b3["energy_cost_yuan"]["mean"]
        d2 = b2["discomfort_ratio"]["mean"]
        d3 = b3["discomfort_ratio"]["mean"]

        se_c = np.sqrt(b2["energy_cost_yuan"]["ci95"] ** 2
                       + b3["energy_cost_yuan"]["ci95"] ** 2)
        sig = abs(c3 - c2) > se_c

        print(f"\n  谷段用电占比（越高说明错峰越有效）：")
        print(f"    时间占比基准      : {ts['valley']:.1%}")
        print(f"    B2 普通 PPO       : {v2:.1%}")
        print(f"    B3 RecurrentPPO   : {v3:.1%}")
        print(f"    规则控制          : {b1r['tier_share']['valley']:.1%}")
        print(f"\n  电费：")
        print(f"    B2 普通 PPO       : {c2:.3f} 元/天")
        print(f"    B3 RecurrentPPO   : {c3:.3f} 元/天  "
              f"({(c3-c2)/c2:+.1%})  {'显著' if sig else '不显著'}")
        print(f"    规则控制          : {b1r['energy_cost_yuan']['mean']:.3f} 元/天")
        print(f"\n  不达标率：")
        print(f"    B2 普通 PPO       : {d2:.2%}")
        print(f"    B3 RecurrentPPO   : {d3:.2%}")

        print()
        improved_tier = v3 > v2 + 0.03
        cheaper = c3 < c2
        if improved_tier and cheaper:
            print("  [结论] 循环网络提升了谷段用电占比并降低了电费，")
            print("         '无记忆导致学不会错峰'这一假设得到支持。")
        elif improved_tier:
            print("  [结论] 循环网络提升了谷段用电占比（错峰行为改善），")
            print("         但电费未同步下降，说明调度仍有优化空间。")
        elif cheaper:
            print("  [结论] 循环网络降低了电费，但谷段占比未明显提升，")
            print("         改善可能来自其他方面（如总体用电下降）。")
        else:
            print("  [结论] 循环网络未能改善错峰行为与电费。")
            print("         这说明'缺少记忆'不是错峰失败的唯一原因；")
            print("         可能还需要：显式储能建模、更长的训练、")
            print("         或把电价预期直接写入奖励。")

        if b3["energy_cost_yuan"]["mean"] < b1r["energy_cost_yuan"]["mean"]:
            print(f"\n  [重要] RecurrentPPO 的电费已低于人工规则，")
            print(f"         这将是本项目首个在 TOU 场景下超过规则的策略。")
        else:
            print(f"\n  [如实记录] RecurrentPPO 电费仍高于人工规则")
            print(f"             （{c3:.3f} vs {b1r['energy_cost_yuan']['mean']:.3f} 元/天），"
                  f"规则在该场景下仍占优。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---- 画图 ----
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    names = list(results)
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]

    ax = axes[0]
    vals = [results[n]["energy_cost_yuan"]["mean"] for n in names]
    errs = [results[n]["energy_cost_yuan"]["ci95"] for n in names]
    bars = ax.bar(names, vals, yerr=errs, capsize=5, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}",
                ha="center", fontsize=9)
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("电费对比（含 95% 置信区间）", fontsize=12)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    x = np.arange(len(names))
    w = 0.35
    valley = [results[n]["tier_share"]["valley"] * 100 for n in names]
    peak = [results[n]["tier_share"]["peak"] * 100 for n in names]
    ax.bar(x - w / 2, valley, w, label="谷段用电占比", color="#2ca02c")
    ax.bar(x + w / 2, peak, w, label="峰段用电占比", color="#d62728")
    ax.axhline(ts["valley"] * 100, color="#2ca02c", linestyle="--",
               linewidth=1, alpha=0.6)
    ax.axhline(ts["peak"] * 100, color="#d62728", linestyle="--",
               linewidth=1, alpha=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylabel("用电占比 (%)")
    ax.set_title("分时段用电结构（虚线为时间占比基准）", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[2]
    vals = [results[n]["discomfort_ratio"]["mean"] * 100 for n in names]
    errs = [results[n]["discomfort_ratio"]["ci95"] * 100 for n in names]
    bars = ax.bar(names, vals, yerr=errs, capsize=5, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.3, f"{v:.1f}%",
                ha="center", fontsize=9)
    ax.set_ylabel("舒适度不达标占比 (%)")
    ax.set_title("舒适度对比", fontsize=12)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("循环网络对比实验：RecurrentPPO 能否学会错峰用电", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "recurrent_compare.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    # ---- 保存结果 ----
    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "results": results},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/recurrent/results.json")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
