r"""
统一评估脚本 —— 所有策略在同一口径下对比（唯一可信来源）

背景与目的：
    此前多个脚本各自实现了评估逻辑，导致同一策略在不同脚本中
    给出不一致的数字。这类不一致会直接影响结论可信度。

    本脚本的目的：**只在这里实现一次评估逻辑**，所有策略
    （基线 + 训练好的 RL 模型）都走同一段代码、同一组种子，
    保证结果可比、可复现。

评估口径：
    - 固定回合长度：7 天（672 步）
    - 固定种子集：seed0 + i（i = 0..n_episodes-1）
    - 指标：日均电量、日均电费、不达标占比、偏差 RMSE、
            响应延迟、恢复时间、日均切换次数
    - 报告：均值 ± 95% 置信区间
    - 自检：核算隐含均价，确认落在电价取值范围内

运行方式：
    python scripts\evaluate_all.py                      # 无分时电价
    python scripts\evaluate_all.py --tou                # 启用分时电价
    python scripts\evaluate_all.py --models <模型路径>
"""

import argparse
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
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = PROJECT_ROOT / "models" / "evaluation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

METRIC_KEYS = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
               "response_delay_mean", "recovery_time_mean",
               "switch_per_day", "ac_start_per_day"]

# ============================================================
# 空调档位常量（与 ActionConfig.ac_setpoints 对应）
#   0 = 关闭
#   1 = 25°C, 2 = 26°C, 3 = 27°C, 4 = 28°C, 5 = 29°C
# ============================================================
AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5


def ac_idx_for(target_c: float, act_cfg) -> int:
    """把目标温度映射为最接近的空调档位索引。"""
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def thermostat(env, target_c: float) -> int:
    """带滞环的恒温器控制，返回空调档位索引。

    逻辑：室温高于 目标+0.5°C 则开启对应档位；
          低于 目标-0.5°C 则关闭；滞环区内维持现状。
    """
    t = env.get_obs_dict()["temp_in"]
    if t > target_c + 0.5:
        return ac_idx_for(target_c, env.act_cfg)
    if t < target_c - 0.5:
        return AC_OFF
    return env.sim.state.ac_idx


# ============================================================
# 策略定义（统一动作函数接口）
# ============================================================
def policy_always_ac_25(env):
    """全程开空调 25°C（舒适度上界参照）。"""
    return encode_action(AC_25C, 0, 0, env.act_cfg)


def policy_always_ac_27(env):
    """全程开空调 27°C。"""
    return encode_action(AC_27C, 0, 0, env.act_cfg)


def policy_never_ac(env):
    """全程不开空调（能耗下界参照）。"""
    return encode_action(AC_OFF, 0, 0, env.act_cfg)


def policy_rule_fixed(env):
    """常规规则控制：有人维持 25°C，无人关闭。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    ac = thermostat(env, 25.0)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def policy_rule_tou(env):
    """分时电价规则控制：谷段 25°C、平段 26°C、峰段 28°C。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    tier = obs["price_tier"]
    if tier == 0.0:
        target = 25.0
    elif tier == 2.0:
        target = 28.0
    else:
        target = 26.0
    ac = thermostat(env, target)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def make_model_policy(model_path, block_price=False):
    """把训练好的模型包装成动作函数。

    block_price=True 时把观测中的电价相关特征置零，
    用于验证"电价信息本身"的作用（对照实验）。
    """
    model = PPO.load(str(model_path))

    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        if block_price:
            v[-3:] = 0.0
        return int(model.predict(v, deterministic=True)[0])
    return fn


# ============================================================
# 唯一的评估实现
# ============================================================
def evaluate(env_maker, action_fn, n_episodes, seed0):
    """跑 n_episodes 个回合，返回逐回合指标列表。

    指标口径说明（重要）：
        所有指标统一为"日均"口径，便于跨策略比较。
        电费按当步电价逐笔累加后除以天数，而不是直接用总电费，
        否则会出现"总电费"与"日均电量"混用导致的价格失真。
    """
    data = {k: [] for k in METRIC_KEYS}
    data["energy_cost_yuan"] = []

    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost_total = 0.0
        steps = 0
        while True:
            action = action_fn(env)
            _, _, term, trunc, info = env.step(action)
            c = info["components"]
            cost_total += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                for k in METRIC_KEYS:
                    data[k].append(float(m.get(k, 0.0)))
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                data["energy_cost_yuan"].append(cost_total / days)
                break
        env.close()

    return data


def summarize(data):
    """汇总为 均值、标准差 与 95% 置信区间半宽。"""
    out = {}
    for k, v in data.items():
        arr = np.array(v, dtype=float)
        n = len(arr)
        m = float(arr.mean()) if n else 0.0
        s = float(arr.std(ddof=1)) if n > 1 else 0.0
        ci95 = 1.96 * s / np.sqrt(n) if n > 1 else 0.0
        out[k] = {"mean": m, "std": s, "ci95": ci95, "n": n}
    return out


def main():
    ap = argparse.ArgumentParser(description="统一评估所有策略")
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--seed0", type=int, default=100000)
    ap.add_argument("--tou", action="store_true", help="启用分时电价")
    ap.add_argument("--models", type=str, nargs="*", default=[],
                    help="额外评估的模型路径（.zip）")
    ap.add_argument("--tag", type=str, default="default")
    args = ap.parse_args()

    rew = RewardConfig(weight_mode="scenario", tou_enabled=args.tou)

    print("=" * 82)
    print(" 统一评估 —— 所有策略在同一口径下对比")
    print("=" * 82)
    print(f"  回合数     : {args.episodes}（每回合 7 天 = 672 步）")
    print(f"  种子范围   : {args.seed0} ~ {args.seed0 + args.episodes - 1}")
    print(f"  分时电价   : {'启用' if args.tou else '未启用'}")
    if args.tou:
        print(f"    谷 {rew.price_valley} / 平 {rew.price_flat} / "
              f"峰 {rew.price_peak} 元每 kWh")
        print(f"    前视电价: 未来 {rew.lookahead_hours} 小时平均，"
              f"权重 {rew.lookahead_weight}")
    env_probe = SmartHomeEnv(rew_cfg=rew)
    print(f"  状态维度   : {env_probe.observation_space.shape[0]}")
    print(f"  动作数量   : {env_probe.action_space.n}"
          f"（空调 {env_probe.act_cfg.ac_choices} 档 × 照明 "
          f"{env_probe.act_cfg.light_choices} × 通风 "
          f"{env_probe.act_cfg.vent_choices}）")
    print()

    policies = [
        ("全程开空调25°C", policy_always_ac_25),
        ("全程开空调27°C", policy_always_ac_27),
        ("规则控制(定温)", policy_rule_fixed),
        ("规则控制(按时段)", policy_rule_tou),
        ("全程不开空调", policy_never_ac),
    ]
    for mp in args.models:
        p = Path(mp)
        if p.exists():
            policies.append((f"PPO:{p.parent.name}", make_model_policy(p)))
        else:
            print(f"  [警告] 模型不存在，已跳过: {p}")

    results = {}
    print("-" * 82)
    print(f"  {'策略':<22}{'日均电量':>10}{'日均电费':>10}"
          f"{'不达标':>10}{'RMSE':>9}{'日均切换':>10}")
    print("-" * 82)

    for name, fn in policies:
        data = evaluate(lambda: SmartHomeEnv(rew_cfg=rew), fn,
                        args.episodes, args.seed0)
        summ = summarize(data)
        results[name] = summ
        print(f"  {name:<22}{summ['energy_per_day_kwh']['mean']:>10.3f}"
              f"{summ['energy_cost_yuan']['mean']:>10.3f}"
              f"{summ['discomfort_ratio']['mean']:>10.2%}"
              f"{summ['temp_dev_rmse']['mean']:>9.3f}"
              f"{summ['switch_per_day']['mean']:>10.1f}")

    print("-" * 82)
    print("  说明：电量单位 kWh/天，电费单位 元/天（均为日均口径）")

    # ---- 一致性自检：隐含均价应落在电价的可能取值范围内 ----
    possible = {rew.price_valley, rew.price_flat, rew.price_peak}
    if not args.tou:
        possible = {1.0}
    lo, hi = min(possible), max(possible)
    print("\n  一致性自检（隐含均价 = 日均电费 / 日均电量）:")
    all_ok = True
    for name, summ in results.items():
        e = summ["energy_per_day_kwh"]["mean"]
        c = summ["energy_cost_yuan"]["mean"]
        if e > 1e-6:
            implied = c / e
            ok = (lo - 1e-6) <= implied <= (hi + 1e-6)
            all_ok = all_ok and ok
            print(f"    {name:<22}{implied:>7.3f} 元/kWh  "
                  f"[电价范围 {lo:.2f}~{hi:.2f}]  {'OK' if ok else '异常'}")
        else:
            print(f"    {name:<22} 电量过低（{e:.4f} kWh），无法核算")
    if all_ok:
        print("    全部通过：口径一致，数字可信。")
    else:
        print("    存在异常：请检查评估口径！")

    print(f"\n  {'策略':<22}{'±95%CI(电量)':>15}{'±95%CI(电费)':>15}"
          f"{'±95%CI(不达标)':>17}")
    print("  " + "-" * 78)
    for name, summ in results.items():
        print(f"  {name:<22}{summ['energy_per_day_kwh']['ci95']:>15.3f}"
              f"{summ['energy_cost_yuan']['ci95']:>15.3f}"
              f"{summ['discomfort_ratio']['ci95']:>17.2%}")

    # ---------- 帕累托分析 ----------
    print("\n" + "=" * 82)
    print(" 帕累托分析（目标：电费越低越好，不达标率越低越好）")
    print("=" * 82)

    pts = [(n, s["energy_cost_yuan"]["mean"], s["discomfort_ratio"]["mean"],
            s["energy_cost_yuan"]["ci95"], s["discomfort_ratio"]["ci95"])
           for n, s in results.items()]

    front = []
    for i, (n_i, c_i, d_i, _, _) in enumerate(pts):
        dominated = False
        for j, (n_j, c_j, d_j, _, _) in enumerate(pts):
            if i == j:
                continue
            if (c_j <= c_i + 1e-9 and d_j <= d_i + 1e-9
                    and (c_j < c_i - 1e-9 or d_j < d_i - 1e-9)):
                dominated = True
                break
        if not dominated:
            front.append((n_i, c_i, d_i))

    print("\n  帕累托前沿上的策略（不被其他策略同时超越）：")
    for n, c, d in sorted(front, key=lambda x: x[1]):
        mark = "  <-- 本方法" if n.startswith("PPO") else ""
        print(f"    {n:<22} 电费 {c:6.3f} 元/天   不达标 {d:6.2%}{mark}")

    rl_on_front = [n for n, _, _ in front if n.startswith("PPO")]
    if rl_on_front:
        print("\n  [结论] 本方法位于帕累托前沿上。")
    else:
        print("\n  [结论] 本方法未进入帕累托前沿。")

    # ---------- 关键对比 ----------
    print("\n" + "=" * 82)
    print(" 关键对比（电费）")
    print("=" * 82)

    def compare(name_a, name_b):
        if name_a not in results or name_b not in results:
            return
        a, b = results[name_a], results[name_b]
        va, vb = a["energy_cost_yuan"]["mean"], b["energy_cost_yuan"]["mean"]
        da, db = a["discomfort_ratio"]["mean"], b["discomfort_ratio"]["mean"]
        diff = (vb - va) / abs(va) if abs(va) > 1e-12 else 0.0
        se = np.sqrt(a["energy_cost_yuan"]["ci95"] ** 2
                     + b["energy_cost_yuan"]["ci95"] ** 2)
        sig = abs(vb - va) > se
        print(f"\n    {name_a}  ->  {name_b}")
        print(f"      电费    : {va:.3f} -> {vb:.3f}  ({diff:+.1%})"
              f"  {'显著' if sig else '不显著'}")
        print(f"      不达标率: {da:.2%} -> {db:.2%}")

    rl_names = [n for n in results if n.startswith("PPO")]
    for rl in rl_names:
        for base in ["规则控制(定温)", "规则控制(按时段)", "全程开空调25°C"]:
            if base in results:
                compare(base, rl)

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(16, 5.5))
    names = list(results)
    colors = ["#2ca02c" if n.startswith("PPO") else "#1f77b4" for n in names]

    ax = axes[0]
    vals = [results[n]["energy_cost_yuan"]["mean"] for n in names]
    errs = [results[n]["energy_cost_yuan"]["ci95"] for n in names]
    bars = ax.barh(names, vals, xerr=errs, capsize=4, color=colors)
    for b, v in zip(bars, vals):
        ax.text(v + 0.06, b.get_y() + b.get_height() / 2, f"{v:.2f}",
                va="center", fontsize=9)
    ax.set_xlabel("日均电费 (元)")
    ax.set_title("电费对比（含 95% 置信区间）", fontsize=12)
    ax.invert_yaxis()
    ax.grid(alpha=0.3, axis="x", linestyle=":")

    ax = axes[1]
    for (n, c, d, cci, dci), col in zip(pts, colors):
        ax.errorbar(c, d * 100, xerr=cci, yerr=dci * 100, fmt="o",
                    color=col, markersize=9, capsize=4)
        ax.annotate(n, (c, d * 100), textcoords="offset points",
                    xytext=(8, 6), fontsize=8)
    if front:
        fr = sorted(front, key=lambda x: x[1])
        ax.plot([p[1] for p in fr], [p[2] * 100 for p in fr],
                color="#d62728", linestyle="--", linewidth=1.2,
                alpha=0.7, label="帕累托前沿")
        ax.legend(fontsize=9)
    ax.set_xlabel("日均电费 (元)  —— 越左越好")
    ax.set_ylabel("舒适度不达标占比 (%)  —— 越下越好")
    ax.set_title(f"帕累托图（分时电价{'启用' if args.tou else '未启用'}）",
                 fontsize=12)
    ax.grid(alpha=0.3, linestyle=":")

    fig.suptitle("统一评估：所有策略在同一口径下的对比", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / f"evaluate_all_{args.tag}.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")

    def clean(d):
        return {k: {kk: float(vv) for kk, vv in v.items()} for k, v in d.items()}

    with open(OUT_DIR / f"results_{args.tag}.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args),
                   "results": {n: clean(s) for n, s in results.items()},
                   "pareto_front": [list(x) for x in front]},
                  f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: models/evaluation/results_{args.tag}.json")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
