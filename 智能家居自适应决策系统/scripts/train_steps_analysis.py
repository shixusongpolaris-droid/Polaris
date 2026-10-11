r"""
训练步数影响分析 —— 澄清"动作空间细化"是否只是收敛不足

背景：
    v2 实验发现：把空调档位从 3 档（24/26/28°C）细化为 5 档
    （25/26/27/28/29°C）、动作数从 24 增至 36 后，
    在相同训练步数（40 万步）下 PPO 的绝对性能**反而下降**：

        v1（24 动作，40 万步）: 不达标 4.32%，能耗 3.899 kWh/天
        v2（36 动作，40 万步）: 不达标 11.73%，能耗 4.472 kWh/天

两种可能解释：
    (a) 动作空间变大导致学得更慢，需要更多训练步数才能达到同等水平
        —— 若如此，是本项目的实现取舍问题，不是方法问题
    (b) 更大的动作空间本身使优化变难，即使训练更久也难以恢复
        —— 若如此，说明动作空间设计需要重新考虑

本脚本：
    评估在不同训练步数下训练出的模型，绘制"性能 vs 训练步数"曲线，
    从而区分上述两种解释。

前置条件：
    需先运行过以下命令生成模型（见 make 说明）：
        python scripts\train.py --timesteps 800000  --seed 42 --tag steps_800000_s42  --skip-baselines
        python scripts\train.py --timesteps 800000  --seed 66 --tag steps_800000_s66  --skip-baselines
        python scripts\train.py --timesteps 1200000 --seed 42 --tag steps_1200000_s42 --skip-baselines
        python scripts\train.py --timesteps 1200000 --seed 66 --tag steps_1200000_s66 --skip-baselines
    以及原有的 40 万步模型：
        python scripts\train.py --timesteps 400000 --seed 42 --tag rl2_notou --skip-baselines

运行方式：
    python scripts\train_steps_analysis.py
"""

import json
import sys
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

from src.env import RewardConfig, SmartHomeEnv, encode_action

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
OUT_DIR = PROJECT_ROOT / "models" / "train_steps"
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


def policy_rule(env):
    """规则控制（有人维持 25°C，无人关闭）。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    t = obs["temp_in"]
    if t > 25.5:
        ac = _ac_idx_for(25.0, env.act_cfg)
    elif t < 24.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def evaluate_model(model, n_episodes=10, seed0=600000):
    """评价模型，返回指标。"""
    rew = RewardConfig(weight_mode="scenario", tou_enabled=False)
    keys = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
            "switch_per_day"]
    acc = {k: [] for k in keys}

    for ep in range(n_episodes):
        env = SmartHomeEnv(rew_cfg=rew)
        env.reset(seed=seed0 + ep)
        while True:
            obs = np.array(env.sim.to_vector(env.get_obs_dict()),
                           dtype=np.float32)
            a = int(model.predict(obs, deterministic=True)[0])
            _, _, term, trunc, info = env.step(a)
            if term or trunc:
                m = info["episode_metrics"]
                for k in keys:
                    acc[k].append(float(m.get(k, 0.0)))
                break
        env.close()
    return {k: (float(np.mean(v)), float(np.std(v, ddof=1)) if len(v) > 1 else 0.0)
            for k, v in acc.items()}


def evaluate_rule(n_episodes=10, seed0=600000):
    rew = RewardConfig(weight_mode="scenario", tou_enabled=False)
    keys = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
            "switch_per_day"]
    acc = {k: [] for k in keys}
    for ep in range(n_episodes):
        env = SmartHomeEnv(rew_cfg=rew)
        env.reset(seed=seed0 + ep)
        while True:
            _, _, term, trunc, info = env.step(policy_rule(env))
            if term or trunc:
                m = info["episode_metrics"]
                for k in keys:
                    acc[k].append(float(m.get(k, 0.0)))
                break
        env.close()
    return {k: (float(np.mean(v)), 0.0) for k, v in acc.items()}


def main():
    print("=" * 76)
    print(" 训练步数影响分析 —— 动作空间细化是否只是收敛不足")
    print("=" * 76)

    # 要评估的模型：步数 -> [(tag, 描述)]
    specs = [
        (400000, [("rl2_notou", "种子 42")]),
        (800000, [("steps_800000_s42", "种子 42"),
                  ("steps_800000_s66", "种子 66")]),
        (1200000, [("steps_1200000_s42", "种子 42"),
                   ("steps_1200000_s66", "种子 66")]),
    ]

    print("\n  说明：")
    print("    v1 参考（24 动作，40 万步）：不达标 4.32%，能耗 3.899 kWh/天")
    print("    v2 参考（36 动作，40 万步）：不达标 11.73%，能耗 4.472 kWh/天")
    print("    本分析检验：增加步数能否把 v2 性能拉回 v1 水平\n")

    print(f"  {'训练步数':>10}{'模型':<22}{'能耗kWh/天':>13}"
          f"{'不达标':>10}{'RMSE':>9}{'切换':>8}")
    print("  " + "-" * 72)

    results = {}
    for steps, tags in specs:
        for tag, desc in tags:
            path = PROJECT_ROOT / "models" / tag / "final_model.zip"
            if not path.exists():
                print(f"  {steps:>10,}{tag:<22}  模型不存在，跳过")
                continue
            model = PPO.load(str(path))
            r = evaluate_model(model)
            results.setdefault(steps, []).append((tag, r))
            print(f"  {steps:>10,}{tag:<22}"
                  f"{r['energy_per_day_kwh'][0]:>13.3f}"
                  f"{r['discomfort_ratio'][0]:>10.2%}"
                  f"{r['temp_dev_rmse'][0]:>9.3f}"
                  f"{r['switch_per_day'][0]:>8.1f}")

    # 规则基线
    rb = evaluate_rule()
    print("  " + "-" * 72)
    print(f"  {'（规则控制）':>10}{'':<22}"
          f"{rb['energy_per_day_kwh'][0]:>13.3f}"
          f"{rb['discomfort_ratio'][0]:>10.2%}"
          f"{rb['temp_dev_rmse'][0]:>9.3f}"
          f"{rb['switch_per_day'][0]:>8.1f}")

    if not results:
        print("\n  [错误] 没有可评估的模型。请先运行 train.py 训练对应步数的模型。")
        print("  参考命令：")
        print("    python scripts\\train.py --timesteps 800000 --seed 42 "
              "--tag steps_800000_s42 --skip-baselines")
        return 1

    # ---- 分析 ----
    print("\n" + "=" * 76)
    print(" 分析")
    print("=" * 76)

    steps_list = sorted(results)
    means = {}
    for s in steps_list:
        d = [r["discomfort_ratio"][0] for _, r in results[s]]
        e = [r["energy_per_day_kwh"][0] for _, r in results[s]]
        means[s] = {"disc": float(np.mean(d)), "disc_sd": float(np.std(d)),
                    "energy": float(np.mean(e)), "n": len(d)}

    print(f"\n  {'训练步数':>12}{'不达标(均值)':>14}{'能耗(均值)':>14}{'种子数':>8}")
    print("  " + "-" * 50)
    for s in steps_list:
        m = means[s]
        print(f"  {s:>12,}{m['disc']:>13.2%}{m['energy']:>14.3f}{m['n']:>8}")
    print(f"  {'(规则基线)':>12}{rb['discomfort_ratio'][0]:>13.2%}"
          f"{rb['energy_per_day_kwh'][0]:>14.3f}")

    # 是否随步数改善
    if len(steps_list) >= 2:
        first, last = means[steps_list[0]], means[steps_list[-1]]
        d_imp = first["disc"] - last["disc"]
        e_imp = first["energy"] - last["energy"]
        print(f"\n  从 {steps_list[0]:,} 步增至 {steps_list[-1]:,} 步：")
        print(f"    不达标率变化 : {first['disc']:.2%} -> {last['disc']:.2%} "
              f"({'改善' if d_imp > 0 else '恶化'} {abs(d_imp):.2%})")
        print(f"    能耗变化     : {first['energy']:.3f} -> {last['energy']:.3f} "
              f"kWh/天 ({'下降' if e_imp > 0 else '上升'} {abs(e_imp):.3f})")

        print()
        v1_disc, v1_energy = 0.0432, 3.899
        if last["disc"] <= v1_disc * 1.5 and last["energy"] <= v1_energy * 1.1:
            print("  [结论] 增加训练步数后性能接近 v1 水平，")
            print("         说明动作空间细化只是**收敛更慢**，")
            print("         属于实现取舍而非方法问题。")
        elif d_imp > 0.01 or e_imp > 0.1:
            print("  [结论] 增加训练步数确实改善了性能，但**尚未恢复** v1 水平")
            print(f"         （v1 参考：不达标 {v1_disc:.2%}，能耗 {v1_energy:.3f}）")
            print("         说明需要更多步数，或更大的动作空间确实增加了优化难度。")
        else:
            print("  [结论] 增加训练步数**未带来改善**，")
            print("         说明 36 动作空间的优化难度本身就高于 24 动作空间，")
            print("         这不是单纯收敛慢的问题。")

    # ---- 画图 ----
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    xs = [s / 1e4 for s in steps_list]      # 万步

    ax = axes[0]
    ys = [means[s]["disc"] * 100 for s in steps_list]
    es = [means[s]["disc_sd"] * 100 for s in steps_list]
    ax.errorbar(xs, ys, yerr=es, fmt="o-", color="#d62728",
                markersize=9, capsize=5, label="PPO（36 动作）")
    ax.axhline(4.32, color="#2ca02c", linestyle="--", linewidth=1.5,
               label="v1 参考（24 动作，40 万步）")
    ax.axhline(rb["discomfort_ratio"][0] * 100, color="#1f77b4",
               linestyle=":", linewidth=1.5, label="规则控制")
    ax.set_xlabel("训练步数（万步）")
    ax.set_ylabel("舒适度不达标占比 (%)")
    ax.set_title("不达标率 vs 训练步数", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    ys = [means[s]["energy"] for s in steps_list]
    ax.errorbar(xs, ys, fmt="s-", color="#d62728", markersize=9,
                capsize=5, label="PPO（36 动作）")
    ax.axhline(3.899, color="#2ca02c", linestyle="--", linewidth=1.5,
               label="v1 参考（24 动作，40 万步）")
    ax.axhline(rb["energy_per_day_kwh"][0], color="#1f77b4",
               linestyle=":", linewidth=1.5, label="规则控制")
    ax.set_xlabel("训练步数（万步）")
    ax.set_ylabel("日均能耗 (kWh)")
    ax.set_title("能耗 vs 训练步数", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    fig.suptitle("训练步数影响分析：动作空间细化是否只是收敛不足", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "train_steps_analysis.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({
            "by_steps": {str(s): [{"tag": t, "metrics": m}
                                  for t, m in results[s]] for s in steps_list},
            "means": {str(s): means[s] for s in steps_list},
            "rule_baseline": rb,
            "v1_reference": {"discomfort": 0.0432, "energy": 3.899},
        }, f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/train_steps/results.json")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
