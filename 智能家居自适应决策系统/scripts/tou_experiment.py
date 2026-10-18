r"""
分时电价实验 —— 检验强化学习在时序能量调度上的优势

为什么做这个实验（背景）：
    在"无分时电价"的场景下，实测表明恒温运行近似最优，
    本方法相对理想恒温控制仅节能约 7%，且该结论对模型参数敏感
    （见 key_experiments.py 的敏感性分析）。
    这说明原场景中强化学习**缺少可优化的时序决策空间**。

    引入分时电价（TOU）后，"何时用电"成为需要权衡的问题：
        谷段电价低，适合提前制冷（蓄冷）；
        峰段电价高，应尽量减少用电；
        同时又要保证人员在室时的舒适度。
    这是典型的序贯决策问题——规则控制难以处理，而强化学习天然适合。

对比设计（四组）：
    B1  规则控制（定温）        —— 不含电价信息的常规控制
    B2  规则控制（按时段调温）  —— 人工设计的电价响应规则
    B3  RL（不含电价信息）      —— 状态中无 elec_price，验证"知道电价"的价值
    B4  RL（含电价信息，本方法）—— 完整方案

    其中 B2 vs B4 是关键对比：人工规则 vs 学习到的策略。
    B3 vs B4 用于说明电价信息本身的作用。

运行方式：
    python scripts\tou_experiment.py --seeds 3 --episodes 12
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
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = PROJECT_ROOT / "models" / "tou_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

METRIC_KEYS = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
               "switch_per_day", "response_delay_mean"]


def ac_idx_for(target_c, cfg):
    """把目标温度映射为最接近的空调档位索引（0 = 关闭）。

    必须动态推导，不能写死 ac_idx。
    2026-10-17 空调档位由 [25..29]（ac_idx 1..5）扩展为
    [23..29]（ac_idx 1..7），原先写死的 `ac_idx=1/2/3` 已分别
    变成 23/24/25 °C，与本文档声明的 24/25/27 °C 完全不符。
    （由团队审计成员 script-auditor 发现）
    """
    best, best_d = 0, 1e18
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


# ============================================================
# 基线策略
# ============================================================
def policy_rule_fixed(env):
    """B1：常规规则控制（不感知电价）。有人则维持 25°C 附近，无人关闭。

    修正（2026-10-17，由团队复核成员 fix-reviewer 指出）：
    原实现用写死的档位 `ac = 2 if t > 26 else (1 if t > 25 else ...)`，
    本意是"高于 26 设 26 °C、高于 25 设 25 °C"。但空调档位由
    [25..29] 扩展为 [23..29] 后，ac_idx=2 已变成 24 °C、1 变成 23 °C，
    实测下发分布为 {23.0: 67, 24.0: 41}，与声明的"维持 25 °C"不符。
    现改为按目标温度动态推导，保留原有的三级结构与 24 °C 关断阈值。
    """
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    t = obs["temp_in"]
    if t > 26.0:
        ac = ac_idx_for(26.0, env.act_cfg)
    elif t > 25.0:
        ac = ac_idx_for(25.0, env.act_cfg)
    else:
        ac = env.sim.state.ac_idx
    if t < 24.0:
        ac = 0
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def policy_rule_tou(env):
    """B2：按时段调温的规则控制（人工设计的电价响应策略）。

    规则逻辑（这是"人工能想到的"电价响应方案）：
        无人              -> 全部关闭
        谷段（22-6 时）    -> 设定 24°C（提前制冷，为峰段蓄冷）
        峰段（8-10,18-21） -> 设定 27°C（尽量少用电，容忍更高的室温）
        平段              -> 设定 25°C
        光照不足且有人     -> 开灯
    """
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)

    tier = obs["price_tier"]          # 0=谷 1=平 2=峰
    t = obs["temp_in"]

    # 修正（2026-10-17）：原代码用写死的 (target_idx, target_t) 二元组，
    # 且两者互相矛盾——注释写峰段 27 °C、平段 25 °C，代码的 target_t 却是
    # 28 / 26，而 target_idx=1/2/3 在档位扩展后更变成 23/24/25 °C。
    # 现改为"以目标温度为唯一真值，索引由 ac_idx_for 动态推导"，
    # 数值与本文档声明保持一致：谷 24 / 平 25 / 峰 27 °C。
    if tier == 0.0:                   # 谷段：提前制冷，为峰段蓄冷
        target_t = 24.0
    elif tier == 2.0:                 # 峰段：容忍更高室温，尽量少用电
        target_t = 27.0
    else:                             # 平段
        target_t = 25.0

    # 滞环：高于目标 +0.5 开，低于目标 -0.5 关
    if t > target_t + 0.5:
        ac = ac_idx_for(target_t, env.act_cfg)
    elif t < target_t - 0.5:
        ac = 0
    else:
        ac = env.sim.state.ac_idx

    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


# ============================================================
# 评估与训练
# ============================================================
def run_episodes(env_maker, action_fn, n_episodes, seed0):
    out = {k: [] for k in METRIC_KEYS}
    out["energy_cost_yuan"] = []
    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                for k in METRIC_KEYS:
                    out[k].append(float(m.get(k, 0.0)))
                out["energy_cost_yuan"].append(cost)
                break
        env.close()
    return out


def eval_baseline(policy_fn, rew_cfg, n_seeds, n_episodes):
    acc = {k: [] for k in METRIC_KEYS}
    acc["energy_cost_yuan"] = []
    for s in range(n_seeds):
        r = run_episodes(lambda: SmartHomeEnv(rew_cfg=rew_cfg),
                         policy_fn, n_episodes, seed0=40000 + s * 1000)
        for k in acc:
            acc[k].extend(r[k])
    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def train_and_eval(rew_cfg, state_has_price: bool, n_seeds, n_episodes,
                   timesteps, n_envs=4):
    """训练并评估 PPO。

    state_has_price=False 时，把观测中的电价特征置零，
    用于验证"电价信息本身"的作用（B3 对照）。
    """
    acc = {k: [] for k in METRIC_KEYS}
    acc["energy_cost_yuan"] = []

    for si in range(n_seeds):
        seed = 200 + si * 11
        vec = DummyVecEnv([
            (lambda i=i: Monitor(SmartHomeEnv(rew_cfg=rew_cfg,
                                              seed=seed + i)))
            for i in range(n_envs)])
        model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                    n_steps=1024, batch_size=256, learning_rate=3e-4,
                    ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
        model.learn(total_timesteps=timesteps)

        def act(env):
            v = np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32)
            if not state_has_price:
                # B3：屏蔽电价信息（置零），其余状态保留。
                # ⚠️ 2026-10-18 两处修正：
                # 1) 观测扩到 27 维后，负索引会切到 gen7 的两个新特征，
                #    必须按名称定位。
                # 2) 原先的 v[-2:] 在 25 维下只覆盖 price_tier 与
                #    elec_price_forward，**漏掉了 elec_price 本身**，
                #    与"屏蔽电价信息"的实验意图不符；现一并置零。
                names = env.sim.active_obs_names()
                for nm in ("elec_price", "price_tier", "elec_price_forward"):
                    if nm in names:
                        v[names.index(nm)] = 0.0
            return int(model.predict(v, deterministic=True)[0])

        r = run_episodes(lambda: SmartHomeEnv(rew_cfg=rew_cfg), act,
                         n_episodes, seed0=50000 + si * 1000)
        for k in acc:
            acc[k].extend(r[k])
        vec.close()

    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def main():
    ap = argparse.ArgumentParser(description="分时电价实验")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--episodes", type=int, default=12)
    ap.add_argument("--timesteps", type=int, default=150000)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 4, 50000

    t0 = time.time()
    print("=" * 78)
    print(" 分时电价实验 —— 检验强化学习在时序能量调度上的优势")
    print("=" * 78)
    print(f"  随机种子 {args.seeds} 个，每种子评估 {args.episodes} 回合"
          f"（合计 {args.seeds * args.episodes} 回合/配置）")
    print(f"  每组训练 {args.timesteps:,} 步")

    rew_tou = RewardConfig(weight_mode="scenario", tou_enabled=True)
    print(f"\n  电价设置：谷 {rew_tou.price_valley} / 平 {rew_tou.price_flat} / "
          f"峰 {rew_tou.price_peak} 元每 kWh")
    print(f"    峰段时段: {rew_tou.peak_hours[0]}-{rew_tou.peak_hours[1]} 时, "
          f"{rew_tou.peak_hours[2]}-{rew_tou.peak_hours[3]} 时")
    print(f"    谷段时段: {rew_tou.valley_hours[0]} 时至次日 "
          f"{rew_tou.valley_hours[1]} 时")
    print(f"    峰谷电价差: {rew_tou.price_peak / rew_tou.price_valley:.1f} 倍")

    results = {}

    print("\n" + "-" * 78)
    print(" B1/B2：规则控制基线")
    print("-" * 78)
    results["B1 规则控制(定温)"] = eval_baseline(
        policy_rule_fixed, rew_tou, args.seeds, args.episodes)
    print(f"  B1 完成")
    results["B2 规则控制(按时段调温)"] = eval_baseline(
        policy_rule_tou, rew_tou, args.seeds, args.episodes)
    print(f"  B2 完成")

    print("\n" + "-" * 78)
    print(" B3/B4：强化学习")
    print("-" * 78)
    print("  训练 B3（状态中不含电价信息）...")
    results["B3 RL(不含电价)"] = train_and_eval(
        rew_tou, state_has_price=False, n_seeds=args.seeds,
        n_episodes=args.episodes, timesteps=args.timesteps)
    print("  B3 完成")
    print("  训练 B4（状态含电价信息，本方法）...")
    results["B4 RL(含电价,本方法)"] = train_and_eval(
        rew_tou, state_has_price=True, n_seeds=args.seeds,
        n_episodes=args.episodes, timesteps=args.timesteps)
    print("  B4 完成")

    # ---------- 对比表 ----------
    print("\n" + "=" * 78)
    print(" 对比结果")
    print("=" * 78)
    hdr = (f"  {'策略':<26}{'日均电量':>10}{'日均电费':>11}"
           f"{'不达标':>10}{'偏差RMSE':>10}{'日均切换':>10}")
    print(hdr)
    print("  " + "-" * 74)
    rows = []
    for name, r in results.items():
        e = r["energy_per_day_kwh"][0]
        cost = r["energy_cost_yuan"][0]
        d = r["discomfort_ratio"][0]
        rmse = r["temp_dev_rmse"][0]
        sw = r["switch_per_day"][0]
        rows.append((name, e, cost, d, rmse, sw))
        print(f"  {name:<26}{e:>10.3f}{cost:>11.3f}{d:>10.2%}"
              f"{rmse:>10.3f}{sw:>10.1f}")
    print("  " + "-" * 74)
    print("  说明：日均电量为 kWh，日均电费为元（按分时电价计算）")
    print("        电费比电量更能反映真实的用电成本，是 TOU 场景的核心指标")

    # ---------- 关键对比 ----------
    print("\n" + "=" * 78)
    print(" 关键对比")
    print("=" * 78)

    b1 = results["B1 规则控制(定温)"]
    b2 = results["B2 规则控制(按时段调温)"]
    b3 = results["B3 RL(不含电价)"]
    b4 = results["B4 RL(含电价,本方法)"]

    def cmp(name_a, a, name_b, b, key, label, better_low=True):
        va, sa = a[key]
        vb, sb = b[key]
        diff = (vb - va) / abs(va) if abs(va) > 1e-12 else 0.0
        arrow = "下降" if diff < 0 else "上升"
        good = (diff < 0) if better_low else (diff > 0)
        mark = "[优]" if good else "[劣]"
        print(f"\n  {label}")
        print(f"    {name_a:<24} {va:>9.3f} ± {sa:<7.3f}")
        print(f"    {name_b:<24} {vb:>9.3f} ± {sb:<7.3f}")
        print(f"    -> {name_b} 相对 {arrow} {abs(diff):.1%}  {mark}")
        return diff, good

    print("\n  对比 1：人工电价响应规则 vs 学习到的策略（核心对比）")
    cmp("B2 规则控制(按时段调温)", b2, "B4 RL(含电价,本方法)", b4,
        "energy_cost_yuan", "电费对比（核心指标）：")

    print("\n  对比 2：电价信息的作用")
    cmp("B3 RL(不含电价)", b3, "B4 RL(含电价,本方法)", b4,
        "energy_cost_yuan", "电费对比：")

    print("\n  对比 3：相对常规规则控制的整体提升")
    cmp("B1 规则控制(定温)", b1, "B4 RL(含电价,本方法)", b4,
        "energy_cost_yuan", "电费对比：")

    # ---------- 结论 ----------
    print("\n" + "=" * 78)
    print(" 结论")
    print("=" * 78)
    cost_improve_rule = (b2["energy_cost_yuan"][0] - b4["energy_cost_yuan"][0]) \
        / b2["energy_cost_yuan"][0]
    cost_improve_noprice = (b3["energy_cost_yuan"][0]
                            - b4["energy_cost_yuan"][0]) \
        / b3["energy_cost_yuan"][0]
    comfort_ok = b4["discomfort_ratio"][0] <= b2["discomfort_ratio"][0] + 0.05

    print(f"\n  1) 本方法相对人工电价规则（B2）电费变化: {cost_improve_rule:+.1%}")
    print(f"  2) 电价信息带来的电费改善（B3->B4）    : {cost_improve_noprice:+.1%}")
    print(f"  3) 舒适度是否未变差                    : "
          f"{'是' if comfort_ok else '否'}")

    if cost_improve_rule > 0 and comfort_ok:
        print(f"\n  [结论] 在分时电价场景下，本方法在保持舒适度的前提下，")
        print(f"         电费低于人工设计的电价响应规则 {cost_improve_rule:.1%}，")
        print(f"         说明强化学习能够学到比人工规则更优的时序用电策略。")
    elif cost_improve_rule > 0:
        print(f"\n  [结论] 本方法电费更低，但舒适度有所下降，属于权衡结果。")
    else:
        print(f"\n  [结论] 本方法未能在电费上超过人工电价规则。")
        print(f"         需检查：训练步数是否充分、奖励权重是否合适。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    names = [r[0] for r in rows]
    colors = ["#7f7f7f", "#1f77b4", "#ff7f0e", "#2ca02c"]

    ax = axes[0]
    vals = [r[2] for r in rows]
    errs = [results[n]["energy_cost_yuan"][1] for n in names]
    bars = ax.bar(range(len(names)), vals, yerr=errs, capsize=5, color=colors)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([n.replace(" ", "\n", 1) for n in names], fontsize=8)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}",
                ha="center", fontsize=9)
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("电费对比（核心指标，越短越好）", fontsize=11)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    vals = [r[3] * 100 for r in rows]
    errs = [results[n]["discomfort_ratio"][1] * 100 for n in names]
    bars = ax.bar(range(len(names)), vals, yerr=errs, capsize=5, color=colors)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([n.replace(" ", "\n", 1) for n in names], fontsize=8)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.3, f"{v:.1f}%",
                ha="center", fontsize=9)
    ax.set_ylabel("舒适度不达标占比 (%)")
    ax.set_title("舒适度对比（越短越好）", fontsize=11)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[2]
    for n, c in zip(names, colors):
        r = results[n]
        ax.errorbar(r["energy_per_day_kwh"][0], r["energy_cost_yuan"][0],
                    xerr=r["energy_per_day_kwh"][1],
                    yerr=r["energy_cost_yuan"][1],
                    fmt="o", color=c, markersize=10, capsize=4)
        ax.annotate(n, (r["energy_per_day_kwh"][0], r["energy_cost_yuan"][0]),
                    textcoords="offset points", xytext=(8, 6), fontsize=8)
    ax.set_xlabel("日均电量 (kWh)")
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("电量—电费关系\n（分时电价下，省电≠省钱）", fontsize=11)
    ax.grid(alpha=0.3, linestyle=":")

    fig.suptitle("分时电价实验：强化学习 vs 规则控制的时序能量调度",
                 fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "tou_experiment.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({n: {k: list(v) for k, v in r.items()}
                   for n, r in results.items()},
                  f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: models/tou_experiment/results.json")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
