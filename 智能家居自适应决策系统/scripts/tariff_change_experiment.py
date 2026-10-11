r"""
电价结构变化适应实验 —— 检验强化学习的结构性优势

## 动机

前五条技术路线（场景化权重、前视电价、循环网络、增加训练步数、显式储能）
都未能让强化学习在分时电价场景下超过人工规则。

共同的失败原因分析：
    这些尝试都在试图让强化学习在**同一个问题**上打败规则。
    但该问题是"电价结构已知且固定、只需按时段切换设定温度"，
    本质上是**阈值决策**——规则把专家知识直接写进代码，
    而强化学习要从奖励信号中反推，天然处于劣势。

    因此继续在同一问题上加码，大概率仍是否定结论。

## 本实验的思路：改变问题结构

规则的核心是"谷段 25°C、平段 26°C、峰段 28°C"这些**人工调出来的数字**。
若电价结构改变（峰谷时段平移、价差变化），规则需要**重新人工调参**，
否则就失效。

而强化学习的策略是从奖励函数学出来的——**换一套电价只需重新训练**，
不需要重新设计规则。

**因此真正的比较应该是**：
    规则（需人工适配）  vs  强化学习（自动适配）

## 实验设计（四组，公平性关键）

    场景 A 基线：电价结构1（原电价），策略：
        A1 规则（在结构1上调参）        ← 基线
        A2 PPO（在结构1上训练）         ← 基线
        A3 恒温运行（参照）

    场景 B 电价变化：电价结构2（峰谷平移 + 价差变化），策略：
        B1 规则（结构1的参数，不重新调）  ← 未适配的规则
        B2 规则（结构2上重新人工调参）    ← **公平上限：规则的最强版本**
        B3 PPO（结构1训练，直接迁移）     ← 零成本迁移
        B4 PPO（结构2上重新训练）         ← 自动适配
        B5 恒温运行（参照）

**关键判据**：
    1. B3 vs B1：迁移的 PPO 是否优于"未适配的规则"？
       （这衡量"电价变化时规则失效的代价"）
    2. B4 vs B2：重新训练的 PPO 能否接近"针对新电价人工调参的规则"？
       （这是最终判据。若接近或更好，说明 RL 的自动适配有价值；
         若差距仍大，则说明规则的人工调参优势无法被替代）
    3. B4 vs A2：PPO 换电价后需要多少代价恢复？

**报告方式**：无论结果如何都如实报告。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\tariff_change_experiment.py --quick
    python scripts\tariff_change_experiment.py --seeds 2 --episodes 8
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
OUT_DIR = PROJECT_ROOT / "models" / "tariff_change"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

AC_OFF = 0


# ============================================================
# 两套电价结构
# ============================================================
# 结构 1（原电价）：峰段 8-10 时、18-21 时；谷段 22-6 时；价差 3.33 倍
# 结构 2（变化后）：峰段整体平移 + 价差拉大
#
# 说明：结构 2 的设计目标不是"更极端"，而是**让结构 1 的人工参数失效**：
#   峰段从"上午 + 傍晚"变为"午后 + 深夜前"，谷段从"深夜"变为"清晨 + 午间"。
#   这样原本"谷段 25°C / 峰段 28°C"的规则在时间上完全错位。
TARIFF_1 = {
    "peak_periods": [(8, 10), (18, 21)],
    "valley_periods": [(22, 6)],
    "price_peak": 1.00,
    "price_flat": 0.60,
    "price_valley": 0.30,
}

TARIFF_2 = {
    "peak_periods": [(12, 15), (19, 22)],
    "valley_periods": [(2, 7), (23, 24)],
    "price_peak": 1.30,
    "price_flat": 0.60,
    "price_valley": 0.25,
}


def _ac_idx_for(target_c, act_cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def make_rule(flat_t, peak_t, valley_t, peak_periods, valley_periods):
    """按**给定电价时段**调温的规则策略。

    注意：峰值/谷值时段由外部传入，因此可以构造
    "参数针对旧电价、但运行在新电价上"的错配情形。
    """
    def in_period(hour, periods):
        for a, b in periods:
            if a <= b:
                if a <= hour < b:
                    return True
            else:                       # 跨零点
                if hour >= a or hour < b:
                    return True
        return False

    def policy(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        hour = int(obs["hour"])
        if in_period(hour, peak_periods):
            target = peak_t
        elif in_period(hour, valley_periods):
            target = valley_t
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


def make_always(temp_c_idx):
    def policy(env):
        return encode_action(temp_c_idx, 0, 0, env.act_cfg)
    return policy


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
    ap = argparse.ArgumentParser(description="电价结构变化适应实验")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 5, 120000

    t0 = time.time()
    print("=" * 96)
    print(" 电价结构变化适应实验 —— 检验强化学习的结构性优势")
    print("=" * 96)
    print()
    print("  假设：规则的电价参数是人工针对特定电价结构调出来的，")
    print("        电价结构变化后规则需要重新调参，而强化学习只需重新训练。")
    print("        因此真正的比较是「规则(需人工适配) vs RL(自动适配)」。")
    print()
    print(f"  结构1（原）：峰 {TARIFF_1['peak_periods']}，"
          f"谷 {TARIFF_1['valley_periods']}，"
          f"价 {TARIFF_1['price_valley']}/{TARIFF_1['price_flat']}"
          f"/{TARIFF_1['price_peak']}")
    print(f"  结构2（新）：峰 {TARIFF_2['peak_periods']}，"
          f"谷 {TARIFF_2['valley_periods']}，"
          f"价 {TARIFF_2['price_valley']}/{TARIFF_2['price_flat']}"
          f"/{TARIFF_2['price_peak']}")
    print(f"\"  种子 {args.seeds}，每种子评估 {args.episodes} 回合，\"")
    print(f"  每组训练 {args.timesteps:,} 步")

    results = {}

    # ============================================================
    # 场景 A：结构 1（基线）
    # ============================================================
    print("\n" + "=" * 96)
    print(" 场景 A：电价结构 1（基线）")
    print("=" * 96)

    rew1 = RewardConfig(weight_mode="scenario", tou_enabled=True, **TARIFF_1)

    # A1 规则（在结构1上调参）
    print("\n  A1 规则（在结构1上调参）")
    best_a1, best_a1_cfg = None, None
    for vt, ft, pk in [(25, 26, 28), (24, 26, 29), (25, 25, 30)]:
        pol = make_rule(ft, pk, vt, TARIFF_1["peak_periods"],
                        TARIFF_1["valley_periods"])
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew1), pol,
                     max(4, args.episodes // 2), 100000)
        print(f"    {vt}/{ft}/{pk}: 电费 {r['cost']:.3f}  "
              f"不达标 {r['discomfort']:.2%}")
        if best_a1 is None or r["cost"] < best_a1["cost"]:
            best_a1, best_a1_cfg = r, (vt, ft, pk)
    print(f"    最优: {best_a1_cfg}  电费 {best_a1['cost']:.3f}")
    results["A1 规则（结构1调参）"] = {**best_a1, "cfg": best_a1_cfg}

    # A2 PPO（在结构1上训练）
    print("\n  A2 PPO（在结构1上训练）")
    rew_ppo1 = RewardConfig(weight_mode="scenario", tou_enabled=True, **TARIFF_1)
    ms = []
    model_1 = None
    for si in range(args.seeds):
        seed = 9000 + si * 73
        m = train_ppo(rew_ppo1, seed, args.timesteps)
        if model_1 is None:
            model_1 = m
        ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew1),
                           make_model_fn(m), args.episodes,
                           seed0=900000 + si * 1000))
        print(f"    种子 {seed}: 电费 {ms[-1]['cost']:.3f}  "
              f"不达标 {ms[-1]['discomfort']:.2%}")
    a2 = {k: float(np.mean([x[k] for x in ms])) for k in ms[0]}
    print(f"    平均: 电费 {a2['cost']:.3f}  不达标 {a2['discomfort']:.2%}")
    results["A2 PPO（结构1训练）"] = a2

    # A3 恒温运行参照
    a3 = evaluate(lambda: SmartHomeEnv(rew_cfg=rew1), make_always(1),
                  args.episodes, 950000)
    results["A3 恒温运行"] = a3
    print(f"\n  A3 恒温运行（25°C）: 电费 {a3['cost']:.3f}  "
          f"不达标 {a3['discomfort']:.2%}")

    # ============================================================
    # 场景 B：结构 2（电价变化）
    # ============================================================
    print("\n" + "=" * 96)
    print(" 场景 B：电价结构 2（电价发生变化）")
    print("=" * 96)

    rew2 = RewardConfig(weight_mode="scenario", tou_enabled=True, **TARIFF_2)

    # B1 规则（沿用结构1的参数，不重新调）
    print("\n  B1 规则（沿用结构1参数，未适配）")
    vt, ft, pk = best_a1_cfg
    pol_b1_mismatch = make_rule(ft, pk, vt,
                                TARIFF_1["peak_periods"],
                                TARIFF_1["valley_periods"])
    b1 = evaluate(lambda: SmartHomeEnv(rew_cfg=rew2), pol_b1_mismatch,
                  args.episodes * args.seeds, 960000)
    results["B1 规则（未适配）"] = b1
    print(f"    电费 {b1['cost']:.3f}  不达标 {b1['discomfort']:.2%}  "
          f"谷段 {b1['valley_share']:.1%}")

    # B2 规则（在结构2上重新人工调参）—— 公平上限
    print("\n  B2 规则（在结构2上重新人工调参）—— 规则的最强版本")
    best_b2, best_b2_cfg = None, None
    for vt2, ft2, pk2 in [(25, 26, 28), (24, 26, 29), (25, 25, 30),
                          (23, 25, 30)]:
        pol = make_rule(ft2, pk2, vt2, TARIFF_2["peak_periods"],
                        TARIFF_2["valley_periods"])
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew2), pol,
                     max(4, args.episodes // 2), 970000)
        print(f"    {vt2}/{ft2}/{pk2}: 电费 {r['cost']:.3f}  "
              f"不达标 {r['discomfort']:.2%}  谷段 {r['valley_share']:.1%}")
        if best_b2 is None or r["cost"] < best_b2["cost"]:
            best_b2, best_b2_cfg = r, (vt2, ft2, pk2)
    print(f"    最优: {best_b2_cfg}  电费 {best_b2['cost']:.3f}")
    results["B2 规则（结构2重新调参）"] = {**best_b2, "cfg": best_b2_cfg}

    # B3 PPO（结构1训练，直接迁移到结构2）
    print("\n  B3 PPO（结构1训练，直接迁移）")
    b3 = evaluate(lambda: SmartHomeEnv(rew_cfg=rew2), make_model_fn(model_1),
                  args.episodes, 980000)
    results["B3 PPO（直接迁移）"] = b3
    print(f"    电费 {b3['cost']:.3f}  不达标 {b3['discomfort']:.2%}  "
          f"谷段 {b3['valley_share']:.1%}")

    # B4 PPO（在结构2上重新训练）
    print("\n  B4 PPO（在结构2上重新训练）")
    ms = []
    for si in range(args.seeds):
        seed = 9100 + si * 79
        m = train_ppo(rew2, seed, args.timesteps)
        ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew2),
                           make_model_fn(m), args.episodes,
                           seed0=990000 + si * 1000))
        print(f"    种子 {seed}: 电费 {ms[-1]['cost']:.3f}  "
              f"不达标 {ms[-1]['discomfort']:.2%}")
    b4 = {k: float(np.mean([x[k] for x in ms])) for k in ms[0]}
    print(f"    平均: 电费 {b4['cost']:.3f}  不达标 {b4['discomfort']:.2%}")
    results["B4 PPO（结构2重新训练）"] = b4

    # B5 恒温运行参照
    b5 = evaluate(lambda: SmartHomeEnv(rew_cfg=rew2), make_always(1),
                  args.episodes, 999000)
    results["B5 恒温运行"] = b5

    # ============================================================
    # 汇总与判据
    # ============================================================
    print("\n" + "=" * 96)
    print(" 结果汇总")
    print("=" * 96)
    print(f"\n  {'配置':<28}{'电费元/天':>11}{'不达标':>10}{'能耗':>9}"
          f"{'谷段':>8}{'峰段':>8}")
    print("  " + "-" * 76)
    for name, r in results.items():
        print(f"  {name:<28}{r['cost']:>11.3f}{r['discomfort']:>10.2%}"
              f"{r['energy']:>9.3f}{r['valley_share']:>8.1%}"
              f"{r['peak_share']:>8.1%}")

    print("\n" + "=" * 96)
    print(" 关键判据")
    print("=" * 96)

    print("\n  【判据1】电价变化后，规则的失效代价（B1 vs B2）")
    d12 = (b1["cost"] - best_b2["cost"]) / best_b2["cost"]
    print(f"    未适配规则 {b1['cost']:.3f}  vs  "
          f"重新调参规则 {best_b2['cost']:.3f} 元/天")
    print(f"    -> 规则未适配的代价: {d12:+.1%}")
    print(f"    （这说明电价变化对规则的影响程度）")

    print("\n  【判据2】迁移能力（B3 vs B1、B3 vs B2）")
    d31 = (b3["cost"] - b1["cost"]) / b1["cost"]
    d32 = (b3["cost"] - best_b2["cost"]) / best_b2["cost"]
    print(f"    直接迁移的 PPO {b3['cost']:.3f} 元/天")
    print(f"      vs 未适配规则: {d31:+.1%}  "
          f"({'PPO 更好' if d31 < 0 else '规则更好'})")
    print(f"      vs 重新调参规则: {d32:+.1%}  "
          f"({'PPO 更好' if d32 < 0 else '规则更好'})")

    print("\n  【判据3】自动适配能力（B4 vs B2）—— 核心判据")
    d42 = (b4["cost"] - best_b2["cost"]) / best_b2["cost"]
    print(f"    重新训练的 PPO {b4['cost']:.3f}  vs  "
          f"重新调参的规则 {best_b2['cost']:.3f} 元/天")
    print(f"    -> 差异 {d42:+.1%}  "
          f"({'PPO 更好' if d42 < 0 else '规则更好'})")
    print(f"    舒适度: PPO 不达标 {b4['discomfort']:.2%}  vs  "
          f"规则 {best_b2['discomfort']:.2%}")

    print("\n  【判据4】PPO 自身的适配代价（B4 vs A2）")
    d42a = (b4["cost"] - a2["cost"]) / a2["cost"]
    print(f"    结构1上 PPO {a2['cost']:.3f}  ->  结构2上 PPO "
          f"{b4['cost']:.3f} 元/天（{d42a:+.1%}）")

    print("\n" + "-" * 96)
    if d42 < 0:
        print("  ✅ [结论] 在电价结构变化后，重新训练的 PPO 超过了")
        print("     针对新电价人工调参的规则。")
        print("     **这验证了强化学习的结构性优势：自动适配新电价，")
        print("       无需人工重新设计规则。**")
    elif d42 < 0.10:
        print("  ⚠️ [结论] 重新训练的 PPO 接近但未超过人工调参的规则")
        print(f"     （差异 {d42:+.1%}）。这说明 RL 的自动适配能力")
        print("     已接近人工调参的水平，但仍略逊。")
    else:
        print(f"  [结论] 即使电价变化，重新训练的 PPO 仍显著差于")
        print(f"     人工调参的规则（{d42:+.1%}）。")
        print("     说明规则的人工调参优势无法被自动学习替代。")

    if d31 < 0:
        print(f"\n  【补充】直接迁移的 PPO 优于未适配的规则（{d31:+.1%}），")
        print(f"         说明「规则不重新调参就会失效」这一点是成立的。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))

    names_a = ["A1 规则\n(结构1调参)", "A2 PPO\n(结构1训练)", "A3 恒温"]
    vals_a = [results["A1 规则（结构1调参）"]["cost"],
              results["A2 PPO（结构1训练）"]["cost"],
              results["A3 恒温运行"]["cost"]]
    names_b = ["B1 规则\n(未适配)", "B2 规则\n(重新调参)", "B3 PPO\n(迁移)",
               "B4 PPO\n(重新训练)", "B5 恒温"]
    vals_b = [results["B1 规则（未适配）"]["cost"],
              results["B2 规则（结构2重新调参）"]["cost"],
              results["B3 PPO（直接迁移）"]["cost"],
              results["B4 PPO（结构2重新训练）"]["cost"],
              results["B5 恒温运行"]["cost"]]

    ax = axes[0]
    ax.bar(names_a, vals_a, color=["#1f77b4", "#d62728", "#7f7f7f"])
    for i, v in enumerate(vals_a):
        ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("场景 A：电价结构 1（基线）", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    cols = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#7f7f7f"]
    ax.bar(names_b, vals_b, color=cols)
    for i, v in enumerate(vals_b):
        ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("场景 B：电价结构 2（电价变化后）", fontsize=12)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("电价结构变化适应实验：规则需人工适配 vs 强化学习自动适配",
                 fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "tariff_change_experiment.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "tariff_1": TARIFF_1,
                   "tariff_2": TARIFF_2, "results": results,
                   "criteria": {"d12": d12, "d31": d31, "d32": d32,
                                "d42": d42, "d42a": d42a}},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/tariff_change/results.json")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
