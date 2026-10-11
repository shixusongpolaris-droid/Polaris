r"""
关键实验 —— 等舒适度比较 + 参数敏感性分析

本脚本回答两个关键问题（用于支撑论文结论）：

    问题 1（等舒适度比较）：
        把能耗权重 λ_e 逐步调大，让 PPO 达到"接近 0% 不舒适"，
        此时它的能耗是多少？若低于"全程开空调"的能耗，
        就可以得出无可争议的结论："同等舒适度下，本方法节能 X%"。
        这比"用 2.2% 不舒适换 14.7% 能耗"的说法更有说服力。

    问题 2（参数敏感性）：
        主结论是否依赖于模型参数的取值？做法是扫描关键参数，
        观察"本方法相对基线的优势"是否在所有取值下都稳定存在。
        若结论随参数翻转，则结论不可信。

统计口径（吸取上一轮教训）：
    - 每个配置使用多个随机种子重复训练与评估
    - 每个种子评估多个回合
    - 报告均值 ± 标准差，并给出相对基线的变化率
    上一轮消融实验仅用 5 个评估回合、单一随机种子，
    组间差异与随机波动同量级，无法支撑结论。

运行方式：
    cd /d D:\Dev\Projects\SmartHome
    .venv\Scripts\activate

    # 快速试跑
    python scripts\key_experiments.py --quick

    # 正式运行（默认）
    python scripts\key_experiments.py
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

from src.env import SimConfig, RewardConfig, SmartHomeEnv, encode_action

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = PROJECT_ROOT / "models" / "key_experiments"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 基线策略
# ============================================================
def policy_always_ac(env):
    return encode_action(1, 0, 0, env.act_cfg)


def policy_occ_only(env):
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(1, light, 0, env.act_cfg)


def policy_rule(env):
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    t = obs["temp_in"]
    if t > 25.0:
        ac = 1
    elif t < 23.0:
        ac = 0
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


# ============================================================
# 评估
# ============================================================
METRIC_KEYS = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
               "switch_per_day", "response_delay_mean"]


def run_episodes(env_maker, action_fn, n_episodes, seed0):
    """跑 n_episodes 个回合，返回各指标列表。"""
    out = {k: [] for k in METRIC_KEYS}
    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            if term or trunc:
                m = info["episode_metrics"]
                for k in METRIC_KEYS:
                    out[k].append(float(m.get(k, 0.0)))
                break
        env.close()
    return out


def evaluate_baseline(policy_fn, n_seeds, n_episodes, sim_cfg=None, rew_cfg=None):
    """多随机种子评估基线。"""
    acc = {k: [] for k in METRIC_KEYS}
    for s in range(n_seeds):
        maker = lambda: SmartHomeEnv(sim_cfg=sim_cfg, rew_cfg=rew_cfg,
                                     seed=None)
        r = run_episodes(maker, policy_fn, n_episodes, seed0=20000 + s * 1000)
        for k in METRIC_KEYS:
            acc[k].extend(r[k])
    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def train_and_eval(sim_cfg, rew_cfg, n_seeds, n_episodes, timesteps,
                   n_envs=4, verbose=True):
    """训练 PPO（多个随机种子）并评估。"""
    acc = {k: [] for k in METRIC_KEYS}
    rewards_end = []

    for si in range(n_seeds):
        seed = 100 + si * 7
        vec = DummyVecEnv([
            (lambda i=i: Monitor(SmartHomeEnv(sim_cfg=sim_cfg,
                                              rew_cfg=rew_cfg,
                                              seed=seed + i)))
            for i in range(n_envs)])
        model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                    n_steps=1024, batch_size=256, learning_rate=3e-4,
                    ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
        model.learn(total_timesteps=timesteps)

        # 评估：用训练时的配置，但换不同的评估种子
        r = run_episodes(
            lambda: SmartHomeEnv(sim_cfg=sim_cfg, rew_cfg=rew_cfg),
            lambda env: int(model.predict(
                np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32), deterministic=True)[0]),
            n_episodes, seed0=30000 + si * 1000)

        for k in METRIC_KEYS:
            acc[k].extend(r[k])
        if verbose:
            print(f"      种子 {seed}: 能耗 {np.mean(r['energy_per_day_kwh']):.3f} "
                  f"kWh/天, 不达标 {np.mean(r['discomfort_ratio']):.1%}")
        vec.close()

    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def fmt(res, key):
    m, s = res[key]
    return f"{m:.3f}±{s:.3f}"


# ============================================================
# 实验 1：等舒适度比较
# ============================================================
def experiment_equal_comfort(args):
    print("\n" + "=" * 76)
    print(" 实验 1：等舒适度比较")
    print("=" * 76)
    print("""
 思路：逐步调大能耗权重 λ_e，使 PPO 的舒适度逼近"全程开空调"的水平
       （0% 不达标），再看此时能耗是多少。
       若低于全程开空调，则结论为："同等舒适度下，本方法节能 X%"。
""")
    print(f" 配置：{args.seeds} 个随机种子 × 每种 {args.episodes} 个评估回合")
    print(f"       每组训练 {args.timesteps:,} 步\n")

    # 基线
    print(" 评估基线...")
    base_always = evaluate_baseline(policy_always_ac, args.seeds, args.episodes)
    base_rule = evaluate_baseline(policy_rule, args.seeds, args.episodes)
    base_occ = evaluate_baseline(policy_occ_only, args.seeds, args.episodes)

    print(f"   全程开空调  : 能耗 {fmt(base_always, 'energy_per_day_kwh')} kWh/天, "
          f"不达标 {base_always['discomfort_ratio'][0]:.2%}")
    print(f"   规则控制    : 能耗 {fmt(base_rule, 'energy_per_day_kwh')} kWh/天, "
          f"不达标 {base_rule['discomfort_ratio'][0]:.2%}")
    print(f"   有人开无人关: 能耗 {fmt(base_occ, 'energy_per_day_kwh')} kWh/天, "
          f"不达标 {base_occ['discomfort_ratio'][0]:.2%}")

    # λ_e 扫描
    lambdas = args.lambdas
    rows = []
    print(f"\n 扫描能耗权重 λ_e（固定权重模式，便于定位等舒适度点）")
    print(f"   {'λ_e':>6}{'日均能耗':>18}{'不达标占比':>16}{'偏差RMSE':>14}")
    print("   " + "-" * 58)

    for lam in lambdas:
        rew = RewardConfig(weight_mode="fixed", lambda_energy=lam)
        res = train_and_eval(None, rew, args.seeds, args.episodes,
                             args.timesteps, verbose=False)
        rows.append((lam, res))
        print(f"   {lam:>6.1f}{fmt(res, 'energy_per_day_kwh'):>18}"
              f"{res['discomfort_ratio'][0]:>15.2%}"
              f"{res['temp_dev_rmse'][0]:>14.3f}")

    print("   " + "-" * 58)

    # 找出满足"不达标率 ≤ 基线 + 容差"的最小能耗配置
    tol = args.comfort_tol
    threshold = base_always["discomfort_ratio"][0] + tol
    candidates = [(lam, r) for lam, r in rows
                  if r["discomfort_ratio"][0] <= threshold]

    print(f"\n 等舒适度判据：不达标率 ≤ {threshold:.2%}"
          f"（全程开空调 {base_always['discomfort_ratio'][0]:.2%} + 容差 {tol:.2%}）")

    conclusion = None
    if candidates:
        best_lam, best = min(candidates, key=lambda x: x[1]["energy_per_day_kwh"][0])
        e_p = best["energy_per_day_kwh"][0]
        e_b = base_always["energy_per_day_kwh"][0]
        saving = (e_b - e_p) / e_b
        print(f"   找到满足条件的配置：λ_e = {best_lam}")
        print(f"     本方法能耗  : {e_p:.3f} kWh/天 "
              f"(不达标 {best['discomfort_ratio'][0]:.2%})")
        print(f"     全程开空调  : {e_b:.3f} kWh/天 "
              f"(不达标 {base_always['discomfort_ratio'][0]:.2%})")
        print(f"     相对节能    : {saving:+.1%}")
        conclusion = {"lambda": best_lam, "ppo_energy": e_p,
                      "baseline_energy": e_b, "saving": saving,
                      "ppo_discomfort": best["discomfort_ratio"][0],
                      "baseline_discomfort": base_always["discomfort_ratio"][0]}
        if saving > 0:
            print(f"\n   [结论] 在同等舒适度下，本方法相对'全程开空调'"
                  f"节能 {saving:.1%}。")
        else:
            print(f"\n   [结论] 在同等舒适度下，本方法未能在能耗上超过最强基线。")
    else:
        print(f"   未找到满足等舒适度判据的配置。")
        print(f"   说明：在给定 λ_e 范围内，PPO 的舒适度始终低于'全程开空调'。")
        print(f"   这本身是一条有价值的结论：**恒温运行在该场景下是舒适度的上界**，")
        print(f"   任何间歇控制都会带来一定的不舒适。")
        conclusion = {"no_candidate": True,
                      "best_lambda": rows[-1][0],
                      "baseline_energy": base_always["energy_per_day_kwh"][0]}

    return {
        "baselines": {"always_ac": base_always, "rule": base_rule,
                      "occ_only": base_occ},
        "sweep": [(lam, r) for lam, r in rows],
        "conclusion": conclusion,
    }


# ============================================================
# 实验 2：参数敏感性分析
# ============================================================
def experiment_sensitivity(args):
    print("\n" + "=" * 76)
    print(" 实验 2：参数敏感性分析")
    print("=" * 76)
    print("""
 思路：扫描关键模型参数，观察"本方法相对基线的优势"是否稳定存在。
       若结论随参数翻转，说明结论不可信；
       若在所有取值下都同向，说明结论稳健。
""")

    results = {}

    # ---- 参数 1：空调最低运行功率比例 ----
    print("\n 参数 1：空调最低运行功率比例 (ac_min_power_ratio)")
    print("   含义：空调开启后即使不需制冷也持续消耗的功率占比。")
    print("   取值越大，'全程开空调'越不划算。\n")
    print(f"   {'取值':>8}{'PPO能耗':>16}{'基线能耗':>16}{'相对节能':>12}"
          f"{'PPO不达标':>12}")
    print("   " + "-" * 66)

    ratio_rows = []
    for ratio in args.power_ratios:
        rew = RewardConfig(weight_mode="scenario",
                           ac_min_power_ratio=ratio)
        # 基线：全程开空调（使用同一功率参数）
        b = evaluate_baseline(policy_always_ac, args.seeds, args.episodes,
                              rew_cfg=rew)
        r = train_and_eval(None, rew, args.seeds, args.episodes,
                           args.timesteps, verbose=False)
        e_p = r["energy_per_day_kwh"][0]
        e_b = b["energy_per_day_kwh"][0]
        saving = (e_b - e_p) / e_b
        ratio_rows.append((ratio, e_p, e_b, saving,
                           r["discomfort_ratio"][0], r["discomfort_ratio"][1]))
        print(f"   {ratio:>8.2f}{e_p:>16.3f}{e_b:>16.3f}"
              f"{saving:>11.1%}{r['discomfort_ratio'][0]:>11.2%}")
    print("   " + "-" * 66)

    savings_r = [x[3] for x in ratio_rows]
    stable_r = all(s > 0 for s in savings_r)
    print(f"   相对节能范围: {min(savings_r):+.1%} ~ {max(savings_r):+.1%}")
    print(f"   是否在所有取值下均为正: {'是（结论稳健）' if stable_r else '否（结论敏感）'}")
    results["power_ratio"] = {
        "rows": ratio_rows, "stable": stable_r,
        "min_saving": min(savings_r), "max_saving": max(savings_r),
    }

    # ---- 参数 2：围护结构传热系数 ----
    print("\n 参数 2：围护结构传热系数 (ua_coef)")
    print("   含义：保温性能。取值越大，房子散热/吸热越快，空调负荷越高。\n")
    print(f"   {'取值':>8}{'PPO能耗':>16}{'基线能耗':>16}{'相对节能':>12}"
          f"{'PPO不达标':>12}")
    print("   " + "-" * 66)

    ua_rows = []
    for ua in args.ua_values:
        sim = SimConfig(ua_coef=ua)
        rew = RewardConfig(weight_mode="scenario")
        b = evaluate_baseline(policy_always_ac, args.seeds, args.episodes,
                              sim_cfg=sim, rew_cfg=rew)
        r = train_and_eval(sim, rew, args.seeds, args.episodes,
                           args.timesteps, verbose=False)
        e_p = r["energy_per_day_kwh"][0]
        e_b = b["energy_per_day_kwh"][0]
        saving = (e_b - e_p) / e_b
        ua_rows.append((ua, e_p, e_b, saving,
                        r["discomfort_ratio"][0], r["discomfort_ratio"][1]))
        print(f"   {ua:>8.3f}{e_p:>16.3f}{e_b:>16.3f}"
              f"{saving:>11.1%}{r['discomfort_ratio'][0]:>11.2%}")
    print("   " + "-" * 66)

    savings_u = [x[3] for x in ua_rows]
    stable_u = all(s > 0 for s in savings_u)
    print(f"   相对节能范围: {min(savings_u):+.1%} ~ {max(savings_u):+.1%}")
    print(f"   是否在所有取值下均为正: {'是（结论稳健）' if stable_u else '否（结论敏感）'}")
    results["ua_coef"] = {
        "rows": ua_rows, "stable": stable_u,
        "min_saving": min(savings_u), "max_saving": max(savings_u),
    }

    return results


# ============================================================
# 画图
# ============================================================
def plot_results(exp1, exp2):
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))

    # 图 1：等舒适度比较（λ_e 扫描）
    ax = axes[0]
    lams = [r[0] for r in exp1["sweep"]]
    energies = [r[1]["energy_per_day_kwh"][0] for r in exp1["sweep"]]
    energies_sd = [r[1]["energy_per_day_kwh"][1] for r in exp1["sweep"]]
    discom = [r[1]["discomfort_ratio"][0] * 100 for r in exp1["sweep"]]

    ax2 = ax.twinx()
    l1 = ax.errorbar(lams, energies, yerr=energies_sd, fmt="o-",
                     color="#d62728", capsize=4, label="PPO 日均能耗")
    l2 = ax2.plot(lams, discom, "s--", color="#2ca02c", label="PPO 不达标占比")
    b_e = exp1["baselines"]["always_ac"]["energy_per_day_kwh"][0]
    ax.axhline(b_e, color="#1f77b4", linestyle=":", linewidth=2,
               label=f"全程开空调 {b_e:.2f} kWh")
    ax.axhline(0.5 * b_e, color="#999999", linestyle="--", linewidth=1.5,
               label=f"节能50%目标线 {0.5 * b_e:.2f} kWh")
    ax.set_xlabel("能耗权重 λe")
    ax.set_ylabel("日均能耗 (kWh)", color="#d62728")
    ax2.set_ylabel("不达标占比 (%)", color="#2ca02c")
    ax.set_title("实验1：λe 扫描（寻找等舒适度点）", fontsize=11)
    ax.grid(alpha=0.3, linestyle=":")
    lines = [l1, l2[0],
             plt.Line2D([], [], color="#1f77b4", linestyle=":"),
             plt.Line2D([], [], color="#999999", linestyle="--")]
    ax.legend(lines, ["PPO 日均能耗", "PPO 不达标占比",
                      f"全程开空调 {b_e:.2f}", f"节能50% {0.5 * b_e:.2f}"],
              fontsize=8, loc="center right")

    # 图 2：功率比例敏感性
    ax = axes[1]
    xs = [r[0] for r in exp2["power_ratio"]["rows"]]
    sv = [r[3] * 100 for r in exp2["power_ratio"]["rows"]]
    bars = ax.bar([str(x) for x in xs], sv,
                  color=["#2ca02c" if s > 0 else "#d62728" for s in sv])
    for b, s in zip(bars, sv):
        ax.text(b.get_x() + b.get_width() / 2,
                s + (1.5 if s >= 0 else -3.5), f"{s:+.1f}%",
                ha="center", fontsize=9)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("空调最低运行功率比例")
    ax.set_ylabel("本方法相对基线的节能率 (%)")
    ax.set_title("实验2a：功率参数敏感性", fontsize=11)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    # 图 3：传热系数敏感性
    ax = axes[2]
    xs = [r[0] for r in exp2["ua_coef"]["rows"]]
    sv = [r[3] * 100 for r in exp2["ua_coef"]["rows"]]
    bars = ax.bar([f"{x:.2f}" for x in xs], sv,
                  color=["#2ca02c" if s > 0 else "#d62728" for s in sv])
    for b, s in zip(bars, sv):
        ax.text(b.get_x() + b.get_width() / 2,
                s + (1.5 if s >= 0 else -3.5), f"{s:+.1f}%",
                ha="center", fontsize=9)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("围护结构传热系数 ua_coef")
    ax.set_ylabel("本方法相对基线的节能率 (%)")
    ax.set_title("实验2b：建筑保温参数敏感性", fontsize=11)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("关键实验：等舒适度比较与参数敏感性分析", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "key_experiments.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n 关键实验图已保存: {out}")


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="关键实验：等舒适度比较与敏感性分析")
    ap.add_argument("--quick", action="store_true", help="快速试跑")
    ap.add_argument("--seeds", type=int, default=3, help="随机种子数")
    ap.add_argument("--episodes", type=int, default=10, help="每种子评估回合数")
    ap.add_argument("--timesteps", type=int, default=150000, help="每组训练步数")
    ap.add_argument("--comfort-tol", type=float, default=0.02,
                    help="等舒适度判据的容差")
    ap.add_argument("--lambdas", type=float, nargs="+",
                    default=[0.5, 1.0, 2.0, 3.0, 5.0],
                    help="实验1 扫描的 λ_e 取值")
    ap.add_argument("--power-ratios", type=float, nargs="+",
                    default=[0.10, 0.20, 0.30, 0.40],
                    help="实验2 扫描的最低功率比例")
    ap.add_argument("--ua-values", type=float, nargs="+",
                    default=[0.03, 0.04, 0.06, 0.08],
                    help="实验2 扫描的传热系数")
    args = ap.parse_args()

    if args.quick:
        args.seeds = 1
        args.episodes = 3
        args.timesteps = 40000
        args.lambdas = [0.5, 2.0]
        args.power_ratios = [0.20, 0.40]
        args.ua_values = [0.04, 0.08]

    t0 = time.time()
    print("=" * 76)
    print(" 关键实验：等舒适度比较 + 参数敏感性分析")
    print("=" * 76)
    print(f"  随机种子数     : {args.seeds}")
    print(f"  评估回合数     : 每种子 {args.episodes} 回合"
          f"（合计 {args.seeds * args.episodes} 回合/配置）")
    print(f"  每组训练步数   : {args.timesteps:,}")

    exp1 = experiment_equal_comfort(args)
    exp2 = experiment_sensitivity(args)

    plot_results(exp1, exp2)

    # ---------- 总结 ----------
    print("\n" + "=" * 76)
    print(" 总结")
    print("=" * 76)

    c = exp1["conclusion"]
    if "no_candidate" not in c:
        print(f"\n  【实验 1 结论】")
        print(f"    在等舒适度条件下（λ_e = {c['lambda']}），")
        print(f"    本方法能耗 {c['ppo_energy']:.3f} kWh/天 vs "
              f"基线 {c['baseline_energy']:.3f} kWh/天，")
        print(f"    相对节能 {c['saving']:+.1%}。")
    else:
        print(f"\n  【实验 1 结论】")
        print(f"    在扫描的 λ_e 范围内，未找到与'全程开空调'同等舒适的配置。")
        print(f"    结论：恒温运行是本场景下舒适度的上界，间歇控制必然带来")
        print(f"          一定不舒适；本方法的价值在于以很小的舒适度代价")
        print(f"          换取可观的能耗下降。")

    print(f"\n  【实验 2 结论】")
    for pname, label in [("power_ratio", "空调最低功率比例"),
                         ("ua_coef", "围护传热系数")]:
        r = exp2[pname]
        s = "稳健" if r["stable"] else "敏感（结论依赖参数取值）"
        print(f"    {label}: 节能率范围 {r['min_saving']:+.1%} ~ "
              f"{r['max_saving']:+.1%}  ->  {s}")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # 保存
    def enc(obj):
        if isinstance(obj, (np.floating, np.integer)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(str(type(obj)))

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"exp1": exp1, "exp2": exp2, "args": vars(args)},
                  f, ensure_ascii=False, indent=2, default=enc)
    print(f"  结果已保存: models/key_experiments/results.json")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
