r"""
储能场景下的帕累托前沿 —— 决定性实验

背景与思路：
    v4 储能实验发现，加入储能后差距显著缩小：
        PPO+储能(ts=0.15) 电费 1.782 元/天，不达标 25.20%
        规则+储能         电费 1.616 元/天，不达标 34.52%

    注意两件事：
      (1) PPO 的舒适度**优于**规则（25.20% vs 34.52%）
      (2) 两者仍不在同一舒适度水平

    因此仍需做**帕累托前沿比较**：扫描 PPO 的 temp_scale，
    构造其"电费—舒适度"前沿，再与规则的前沿对比，
    看 PPO 能否在规则的舒适度水平上更便宜。

    这是回答"储能能否让强化学习超过规则"的决定性实验。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\storage_pareto.py --quick
    python scripts\storage_pareto.py --seeds 2 --episodes 8
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
OUT_DIR = PROJECT_ROOT / "models" / "storage_pareto"
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


def make_rule(peak_t, flat_t=26.0, valley_t=25.0):
    """按时段调温的规则策略。"""
    def policy(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        tier = obs["price_tier"]
        target = (valley_t if tier == 0.0
                  else (peak_t if tier == 2.0 else flat_t))
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
    costs, disc, energy, valleys, peaks = [], [], [], [], []
    ch_sum, dis_sum = [], []
    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        te = {"valley": 0.0, "flat": 0.0, "peak": 0.0}
        ch = dis = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            te[c["price_tier"]] += c["energy_kwh"]
            ch += c.get("battery_charge_kwh", 0.0) or 0.0
            dis += c.get("battery_discharge_kwh", 0.0) or 0.0
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                costs.append(cost / days)
                disc.append(float(m["discomfort_ratio"]))
                energy.append(float(m["energy_per_day_kwh"]))
                tot = sum(te.values())
                valleys.append(te["valley"] / tot if tot > 0 else 0.0)
                peaks.append(te["peak"] / tot if tot > 0 else 0.0)
                ch_sum.append(ch / days)
                dis_sum.append(dis / days)
                break
        env.close()
    return {
        "cost": float(np.mean(costs)),
        "cost_sd": float(np.std(costs, ddof=1)) if len(costs) > 1 else 0.0,
        "discomfort": float(np.mean(disc)),
        "energy": float(np.mean(energy)),
        "valley_share": float(np.mean(valleys)),
        "peak_share": float(np.mean(peaks)),
        "charge_kwh_day": float(np.mean(ch_sum)),
        "discharge_kwh_day": float(np.mean(dis_sum)),
        "n": len(costs),
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


def pareto_front(points):
    """(discomfort, cost) 的帕累托前沿，按 discomfort 升序。"""
    pts = sorted(points, key=lambda x: (x[0], x[1]))
    front, best = [], float("inf")
    for d, c in pts:
        if c < best - 1e-9:
            front.append((d, c))
            best = c
    return front


def interp_front(front, target_d):
    xs = [f[0] for f in front]
    ys = [f[1] for f in front]
    if not xs or target_d < xs[0] or target_d > xs[-1]:
        return None
    for i in range(len(xs) - 1):
        if xs[i] <= target_d <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            if span < 1e-12:
                return ys[i]
            w = (target_d - xs[i]) / span
            return ys[i] + w * (ys[i + 1] - ys[i])
    return ys[-1]


def main():
    ap = argparse.ArgumentParser(description="储能场景下的帕累托前沿")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--capacity", type=float, default=4.0)
    ap.add_argument("--temp-scales", type=float, nargs="+",
                    default=[0.05, 0.10, 0.15, 0.20, 0.30, 0.50])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 5, 120000
        args.temp_scales = [0.08, 0.15, 0.30]

    t0 = time.time()
    print("=" * 94)
    print(" 储能场景下的帕累托前沿 —— 决定性实验")
    print("=" * 94)
    print(f"  电池容量 {args.capacity} kWh")
    print(f"  temp_scale 扫描: {args.temp_scales}")
    print(f"  种子 {args.seeds}，每种子评估 {args.episodes} 回合，")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  核心问题：加入储能后，强化学习能否在规则的舒适度水平上更便宜？")

    cap = args.capacity
    base = dict(weight_mode="scenario", tou_enabled=True,
                battery_enabled=True, battery_capacity_kwh=cap)

    # ---------- 规则的前沿（扫描峰段设定温度）----------
    print("\n" + "-" * 94)
    print(" 规则 + 储能：前沿")
    print("-" * 94)
    print(f"  {'峰段设定':>10}{'电费元/天':>12}{'不达标':>11}{'能耗':>9}"
          f"{'谷段':>8}{'峰段':>8}{'充kWh':>8}{'放kWh':>8}")
    print("  " + "-" * 76)
    rule_pts = []
    for pk in [25.0, 26.0, 27.0, 28.0, 29.0, 30.0, 31.0]:
        rew = RewardConfig(**base)
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew), make_rule(pk),
                     args.episodes * args.seeds, 100000)
        rule_pts.append(r)
        print(f"  {pk:>10.1f}{r['cost']:>12.3f}{r['discomfort']:>11.2%}"
              f"{r['energy']:>9.3f}{r['valley_share']:>8.1%}"
              f"{r['peak_share']:>8.1%}{r['charge_kwh_day']:>8.2f}"
              f"{r['discharge_kwh_day']:>8.2f}")
    rule_front = pareto_front([(r["discomfort"], r["cost"]) for r in rule_pts])
    print(f"\n  规则前沿: " +
          "  ".join(f"({d:.1%},{c:.2f})" for d, c in rule_front))

    # ---------- PPO 的前沿 ----------
    print("\n" + "-" * 94)
    print(" PPO + 储能：前沿")
    print("-" * 94)
    print(f"  {'temp_scale':>11}{'电费元/天':>11}{'不达标':>9}{'能耗':>9}"
          f"{'谷段':>8}{'峰段':>8}{'充kWh':>8}{'放kWh':>8}")
    print("  " + "-" * 74)
    ppo_pts = []
    for ts in args.temp_scales:
        rew = RewardConfig(**base, temp_scale=ts)
        ms = []
        for si in range(args.seeds):
            seed = 6000 + si * 67
            model = train_ppo(rew, seed, args.timesteps)
            ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                               make_model_fn(model), args.episodes,
                               seed0=800000 + si * 1000))
        avg = {k: float(np.mean([m[k] for m in ms])) for k in ms[0]}
        avg["temp_scale"] = ts
        ppo_pts.append(avg)
        print(f"  {ts:>11.2f}{avg['cost']:>11.3f}{avg['discomfort']:>9.2%}"
              f"{avg['energy']:>9.3f}{avg['valley_share']:>8.1%}"
              f"{avg['peak_share']:>8.1%}{avg['charge_kwh_day']:>8.2f}"
              f"{avg['discharge_kwh_day']:>8.2f}")
    ppo_front = pareto_front([(p["discomfort"], p["cost"]) for p in ppo_pts])
    print(f"\n  PPO 前沿: " +
          "  ".join(f"({d:.1%},{c:.2f})" for d, c in ppo_front))

    # ---------- 前沿比较 ----------
    print("\n" + "=" * 94)
    print(" 前沿比较（核心结论）")
    print("=" * 94)

    lo = max(rule_front[0][0], ppo_front[0][0])
    hi = min(rule_front[-1][0], ppo_front[-1][0])

    rows, cross, wins_ppo, wins_rule = [], None, 0, 0
    if lo >= hi:
        print(f"\n  两条前沿的舒适度区间不重叠：")
        print(f"    规则 {rule_front[0][0]:.1%} ~ {rule_front[-1][0]:.1%}")
        print(f"    PPO  {ppo_front[0][0]:.1%} ~ {ppo_front[-1][0]:.1%}")
    else:
        print(f"\n  重叠区间: {lo:.1%} ~ {hi:.1%}")
        print(f"\n  {'不达标率':>10}{'规则电费':>11}{'PPO电费':>11}"
              f"{'差异':>10}{'占优方':>10}")
        print("  " + "-" * 56)
        for d in np.linspace(lo, hi, 9):
            rc = interp_front(rule_front, d)
            pc = interp_front(ppo_front, d)
            if rc is None or pc is None:
                continue
            diff = (pc - rc) / rc
            who = "PPO 更优" if diff < 0 else "规则更优"
            if diff < 0:
                wins_ppo += 1
            else:
                wins_rule += 1
            rows.append((float(d), rc, pc, float(diff), who))
            print(f"  {d:>10.2%}{rc:>11.3f}{pc:>11.3f}{diff:>+10.1%}{who:>10}")
        print("  " + "-" * 56)
        for i in range(len(rows) - 1):
            if (rows[i][3] < 0) != (rows[i + 1][3] < 0):
                cross = (rows[i][0] + rows[i + 1][0]) / 2
                break

    print("\n" + "-" * 94)
    if wins_ppo > 0:
        print(f"  ✅ [结论] 加入储能后，PPO 在 {wins_ppo} 段舒适度水平上电费更低！")
        if cross:
            print(f"     交叉点约在 不达标率 {cross:.1%} 处。")
            first_ppo = rows[0][3] < 0
            print(f"     低于该水平：{'PPO 更省' if first_ppo else '规则更省'}")
            print(f"     高于该水平：{'规则更省' if first_ppo else 'PPO 更省'}")
        print(f"\n     这支持此前的假设：**显式储能为跨时段调度提供了物理载体**，")
        print(f"     使强化学习能够超越人工规则。")
    else:
        print(f"  [结论] 即使加入储能，PPO 在重叠区间内仍未占优。")
        print(f"     说明规则的电价分档 + 储能的组合已经很难被超越。")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))

    ax = axes[0]
    for pts, lab, col, mk in [(rule_pts, "规则 + 储能", "#1f77b4", "s"),
                              (ppo_pts, "PPO + 储能", "#d62728", "o")]:
        ax.scatter([p["discomfort"] * 100 for p in pts],
                   [p["cost"] for p in pts], s=80, color=col, marker=mk,
                   label=lab, zorder=3)
    ax.plot([d * 100 for d, _ in rule_front], [c for _, c in rule_front],
            "--", color="#1f77b4", linewidth=2, alpha=0.7)
    ax.plot([d * 100 for d, _ in ppo_front], [c for _, c in ppo_front],
            "--", color="#d62728", linewidth=2, alpha=0.7)
    if cross:
        ax.axvline(cross * 100, color="black", linestyle=":",
                   label=f"交叉点 {cross:.0%}")
    ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
    ax.set_ylabel("日均电费 (元)  —— 越下越好")
    ax.set_title("储能场景下的电费—舒适度前沿对比", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    if rows:
        ax.bar([f"{r[0]:.0%}" for r in rows], [r[3] * 100 for r in rows],
               color=["#2ca02c" if r[3] < 0 else "#d62728" for r in rows])
        ax.axhline(0, color="black", linewidth=1)
        ax.set_xlabel("不达标率水平")
        ax.set_ylabel("PPO 相对规则的电费差异 (%)")
        ax.set_title("等舒适度比较：负值表示 PPO 更优", fontsize=12)
        ax.grid(alpha=0.3, axis="y", linestyle=":")
    else:
        ax.text(0.5, 0.5, "区间不重叠", ha="center", va="center",
                transform=ax.transAxes)

    fig.suptitle("决定性实验：储能能否让强化学习超过规则", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "storage_pareto.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args),
                   "rule_points": rule_pts, "ppo_points": ppo_pts,
                   "rule_front": rule_front, "ppo_front": ppo_front,
                   "rows": rows, "cross": cross},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/storage_pareto/results.json")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
