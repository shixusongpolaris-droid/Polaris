r"""
显式储能对比实验 —— 检验"储能能否让强化学习超过规则"

背景（v3 实验结论）：
    在分时电价场景下，人工规则的电费显著低于强化学习，
    且帕累托前沿显示规则在重叠区间全面占优（+39.5%~+55.4%）。
    进一步分析把原因定位到**问题的物理结构限制**：
    空调的制冷必须即时交付，无法把谷段的冷量存到峰段使用。

    因此提出假设：**若能显式储能，跨时段调度就有了物理载体，
    强化学习应当能够学会错峰并超过规则。**

本实验：
    在环境中引入电池储能（`RewardConfig.battery_enabled`），
    储能由电价分档规则调度（谷段充电、峰段放电抵扣空调负载），
    然后比较四种配置：

        A  规则控制（无储能）        —— 基线
        B  规则控制 + 储能           —— 规则 + 储能的增益
        C  PPO（无储能）             —— 已有结论
        D  PPO + 储能                —— 本实验关注点

    判据：D 的电费是否低于 B（即"同样有储能时，学习是否比规则更好"）。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\storage_experiment.py --quick
    python scripts\storage_experiment.py --seeds 2 --episodes 8
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
OUT_DIR = PROJECT_ROOT / "models" / "storage"
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
# 评估（含储能明细）
# ============================================================
def evaluate(env_maker, action_fn, n_episodes, seed0):
    costs, disc, energy, valleys, peaks = [], [], [], [], []
    ch_total, dis_total, soc_min = [], [], []
    ac_only_total = []

    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        te = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
        ch_sum = dis_sum = 0.0
        socs = []
        ac_sum = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            te[c["price_tier"]] += c["energy_kwh"]
            ch = c.get("battery_charge_kwh", 0.0) or 0.0
            dis = c.get("battery_discharge_kwh", 0.0) or 0.0
            ch_sum += ch
            dis_sum += dis
            # 空调单独耗电 = 本步总耗电 − 充电 + 放电
            ac_sum += (c["energy_kwh"] - ch + dis)
            if getattr(env.rew_cfg, "battery_enabled", False):
                socs.append(env.sim.state.battery_soc_kwh)
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                costs.append(cost / days)
                disc.append(float(m["discomfort_ratio"]))
                energy.append(float(m["energy_per_day_kwh"]))
                ac_only_total.append(ac_sum / days)
                tot = sum(te.values())
                valleys.append(te["valley"] / tot if tot > 0 else 0.0)
                peaks.append(te["peak"] / tot if tot > 0 else 0.0)
                ch_total.append(ch_sum / days)
                dis_total.append(dis_sum / days)
                soc_min.append(min(socs) if socs else 0.0)
                break
        env.close()

    n = len(costs)
    return {
        "cost": float(np.mean(costs)),
        "cost_sd": float(np.std(costs, ddof=1)) if n > 1 else 0.0,
        "discomfort": float(np.mean(disc)),
        "energy": float(np.mean(energy)),
        "ac_energy": float(np.mean(ac_only_total)),
        "valley_share": float(np.mean(valleys)),
        "peak_share": float(np.mean(peaks)),
        "charge_kwh_day": float(np.mean(ch_total)),
        "discharge_kwh_day": float(np.mean(dis_total)),
        "soc_min": float(np.mean(soc_min)),
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


def main():
    ap = argparse.ArgumentParser(description="显式储能对比实验")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--capacity", type=float, nargs="+", default=[4.0])
    ap.add_argument("--temp-scale", type=float, default=1.0)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 5, 120000

    t0 = time.time()
    print("=" * 92)
    print(" 显式储能对比实验 —— 储能能否让强化学习超过规则")
    print("=" * 92)
    print(f"  电池容量: {args.capacity} kWh，温度权重 temp_scale={args.temp_scale}")
    print(f"  种子 {args.seeds}，每种子评估 {args.episodes} 回合，")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  四种配置：")
    print("    A 规则控制（无储能）")
    print("    B 规则控制 + 储能")
    print("    C PPO（无储能）")
    print("    D PPO + 储能        <- 关注点")

    results = {}

    # ---------- A / B：规则控制 ----------
    for label, cap in [("A 规则控制（无储能）", 0.0)] + \
                      [(f"B 规则+储能({c:.0f}kWh)", c) for c in args.capacity]:
        rew = RewardConfig(weight_mode="scenario", tou_enabled=True,
                           battery_enabled=(cap > 0),
                           battery_capacity_kwh=max(cap, 4.0))
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew), policy_rule_tou,
                     args.episodes * args.seeds, 100000)
        results[label] = r
        print(f"\n  {label}")
        print(f"    电费 {r['cost']:.3f} 元/天，能耗 {r['energy']:.3f} kWh/天，"
              f"不达标 {r['discomfort']:.2%}")
        print(f"    谷段 {r['valley_share']:.1%}，峰段 {r['peak_share']:.1%}，"
              f"充 {r['charge_kwh_day']:.2f} / 放 {r['discharge_kwh_day']:.2f} kWh/天")

    # ---------- C / D：PPO ----------
    for label, cap in [("C PPO（无储能）", 0.0)] + \
                      [(f"D PPO+储能({c:.0f}kWh)", c) for c in args.capacity]:
        rew = RewardConfig(weight_mode="scenario", tou_enabled=True,
                           battery_enabled=(cap > 0),
                           battery_capacity_kwh=max(cap, 4.0),
                           temp_scale=args.temp_scale)
        print(f"\n  {label}：训练中…")
        ms = []
        for si in range(args.seeds):
            seed = 5000 + si * 61
            model = train_ppo(rew, seed, args.timesteps)
            ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                               make_model_fn(model), args.episodes,
                               seed0=750000 + si * 1000))
            print(f"    种子 {seed}: 电费 {ms[-1]['cost']:.3f} 元/天，"
                  f"不达标 {ms[-1]['discomfort']:.2%}")
        avg = {k: float(np.mean([m[k] for m in ms])) for k in ms[0]}
        results[label] = avg
        print(f"    平均: 电费 {avg['cost']:.3f} 元/天，"
              f"能耗 {avg['energy']:.3f} kWh/天，"
              f"不达标 {avg['discomfort']:.2%}")
        print(f"    谷段 {avg['valley_share']:.1%}，"
              f"充 {avg['charge_kwh_day']:.2f} / 放 {avg['discharge_kwh_day']:.2f}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    print(" 结果汇总")
    print("=" * 92)
    print(f"\n  {'配置':<24}{'电费元/天':>11}{'能耗kWh/天':>12}"
          f"{'不达标':>9}{'谷段':>8}{'峰段':>8}{'充kWh':>8}{'放kWh':>8}")
    print("  " + "-" * 88)
    for label, r in results.items():
        print(f"  {label:<24}{r['cost']:>11.3f}{r['energy']:>12.3f}"
              f"{r['discomfort']:>9.2%}{r['valley_share']:>8.1%}"
              f"{r['peak_share']:>8.1%}{r['charge_kwh_day']:>8.2f}"
              f"{r['discharge_kwh_day']:>8.2f}")

    # ---------- 关键判据 ----------
    print("\n" + "=" * 92)
    print(" 关键判据")
    print("=" * 92)

    a = results.get("A 规则控制（无储能）")
    c = results.get("C PPO（无储能）")

    print(f"\n  【1】储能给规则带来的增益")
    for cap in args.capacity:
        b = results.get(f"B 规则+储能({cap:.0f}kWh)")
        if a and b:
            d = (b["cost"] - a["cost"]) / a["cost"]
            print(f"    容量 {cap:.0f} kWh: 电费 {a['cost']:.3f} -> {b['cost']:.3f}"
                  f" 元/天（{d:+.1%}）")
            print(f"      谷段占比 {a['valley_share']:.1%} -> "
                  f"{b['valley_share']:.1%}")

    print(f"\n  【2】储能给 PPO 带来的增益")
    for cap in args.capacity:
        dd = results.get(f"D PPO+储能({cap:.0f}kWh)")
        if c and dd:
            d = (dd["cost"] - c["cost"]) / c["cost"]
            print(f"    容量 {cap:.0f} kWh: 电费 {c['cost']:.3f} -> "
                  f"{dd['cost']:.3f} 元/天（{d:+.1%}）")

    print(f"\n  【3】同等储能条件下，PPO 与规则的对决（核心问题）")
    wins = 0
    for cap in args.capacity:
        b = results.get(f"B 规则+储能({cap:.0f}kWh)")
        dd = results.get(f"D PPO+储能({cap:.0f}kWh)")
        if b and dd:
            d = (dd["cost"] - b["cost"]) / b["cost"]
            who = "PPO 更省" if d < 0 else "规则更省"
            if d < 0:
                wins += 1
            print(f"    容量 {cap:.0f} kWh: PPO {dd['cost']:.3f} vs "
                  f"规则 {b['cost']:.3f} 元/天（{d:+.1%}）-> {who}")
            print(f"      舒适度: PPO 不达标 {dd['discomfort']:.2%} vs "
                  f"规则 {b['discomfort']:.2%}")

    print("\n" + "-" * 92)
    if wins == len(args.capacity):
        print("  ✅ [结论] 在同等储能条件下，强化学习的电费低于规则！")
        print("     这验证了此前的假设：**储能提供了跨时段调度的物理载体**，")
        print("     使强化学习能够学会错峰并超越人工规则。")
    elif wins > 0:
        print("  ⚠️ [结论] 在部分容量下强化学习胜出。")
    else:
        print("  [结论] 即使有储能，强化学习的电费仍高于规则。")
        print("     说明规则的电价分档策略本身已经接近最优，")
        print("     或储能调度需要由策略自行优化（当前由规则固定调度）。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    labels = list(results)
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728"]

    ax = axes[0]
    vals = [results[l]["cost"] for l in labels]
    bars = ax.bar(range(len(labels)), vals,
                  color=colors[:len(labels)])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}",
                ha="center", fontsize=9)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("电费对比", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2, [results[l]["valley_share"] * 100 for l in labels], w,
           label="谷段", color="#2ca02c")
    ax.bar(x + w / 2, [results[l]["peak_share"] * 100 for l in labels], w,
           label="峰段", color="#d62728")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("用电占比 (%)")
    ax.set_title("分时段用电结构", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[2]
    vals = [results[l]["discomfort"] * 100 for l in labels]
    ax.bar(range(len(labels)), vals, color=colors[:len(labels)])
    for i, v in enumerate(vals):
        ax.text(i, v + 0.3, f"{v:.1f}%", ha="center", fontsize=9)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("舒适度不达标占比 (%)")
    ax.set_title("舒适度对比", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("显式储能对比实验：储能能否让强化学习超过规则", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "storage_experiment.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "results": results},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/storage/results.json")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
