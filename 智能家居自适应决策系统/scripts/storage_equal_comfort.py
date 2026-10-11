r"""
储能场景下的等舒适度比较 —— 规则舒适度可调版的公平对决

动机：
    此前的比较存在一个不公平之处：**规则的舒适度是固定的**。
    规则用"峰段设定温度"这一个旋钮，只能把不达标率调到 24%~35%，
    而 PPO 的舒适度随 temp_scale 变化，可覆盖 11%~43%。

    因此当 PPO 在低不达标率（如 11%）工作时，
    规则根本达不到那个水平，两者无法比较——这是"能力区间不同"。

    但如果我们给规则**更多的旋钮**，它的舒适度就可以被推低：
      · 环境温度设定   25~31°C   （影响整体舒适度）
      · 谷段设定温度   23~25°C   （影响夜间预冷强度）
    两个旋钮组合后，规则的不达标率应能覆盖更宽的范围。

本脚本：
    1. 用**两个旋钮**扫描规则 + 储能，构造更宽的前沿
    2. 用 temp_scale 扫描 PPO + 储能，构造其前沿
    3. 在两者**重叠的舒适度区间**内比较电费
    4. 判定：是否存在 PPO 更优的区间

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\storage_equal_comfort.py --quick
    python scripts\storage_equal_comfort.py --seeds 2 --episodes 8
"""

import argparse
import itertools
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
OUT_DIR = PROJECT_ROOT / "models" / "storage_equal"
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


def make_rule(flat_t, peak_t, valley_t):
    """规则策略：三个时段各自的设定温度，共三个旋钮。

    平段设定 flat_t 主要决定整体舒适度（因为平段占时长最多），
    峰段 peak_t 影响错峰强度，谷段 valley_t 影响预冷强度。
    """
    def policy(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        tier = obs["price_tier"]
        if tier == 0.0:
            target = valley_t
        elif tier == 2.0:
            target = peak_t
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
    return {
        "cost": float(np.mean(costs)),
        "cost_sd": float(np.std(costs, ddof=1)) if len(costs) > 1 else 0.0,
        "discomfort": float(np.mean(disc)),
        "energy": float(np.mean(energy)),
        "valley_share": float(np.mean(valleys)),
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
    ap = argparse.ArgumentParser(description="储能场景下的等舒适度比较")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--capacity", type=float, default=4.0)
    ap.add_argument("--temp-scales", type=float, nargs="+",
                    default=[0.05, 0.10, 0.15, 0.25, 0.50])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 4, 100000
        args.temp_scales = [0.08, 0.20]

    t0 = time.time()
    print("=" * 96)
    print(" 储能场景下的等舒适度比较 —— 规则舒适度可调版的公平对决")
    print("=" * 96)
    print(f"  电池容量 {args.capacity} kWh")
    print(f"  PPO temp_scale 扫描: {args.temp_scales}")
    print(f"  种子 {args.seeds}，每种子评估 {args.episodes} 回合")
    print()
    print("  关键改进：给规则**三个旋钮**（谷段/平段/峰段设定温度），")
    print("    使其舒适度可以下推到 PPO 的工作区间，从而实现真正的等舒适度比较。")

    base = dict(weight_mode="scenario", tou_enabled=True,
                battery_enabled=True, battery_capacity_kwh=args.capacity)

    # ---------- 规则：三旋钮扫描 ----------
    print("\n" + "-" * 96)
    print(" 规则 + 储能：三旋钮扫描（谷段 / 平段 / 峰段设定温度）")
    print("-" * 96)
    print(f"  {'谷/平/峰':>16}{'电费元/天':>12}{'不达标':>10}{'能耗':>9}"
          f"{'谷段':>8}")
    print("  " + "-" * 58)

    rule_pts = []
    # 谷段在 22~25（预冷强度），平段在 24~28（整体舒适度），
    # 峰段在 26~31（错峰强度）
    grid = list(itertools.product([22.0, 23.0, 24.0, 25.0],
                                  [24.0, 25.0, 26.0, 27.0, 28.0],
                                  [26.0, 28.0, 30.0, 31.0]))
    for vt, ft, pk in grid:
        rew = RewardConfig(**base)
        r = evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                     make_rule(ft, pk, vt),
                     max(4, args.episodes // 2), 100000)
        r.update({"valley_t": vt, "flat_t": ft, "peak_t": pk})
        rule_pts.append(r)

    # 打印前沿点（避免输出过长，只显示帕累托前沿）
    rule_front_raw = pareto_front([(r["discomfort"], r["cost"])
                                   for r in rule_pts])
    for d, c in rule_front_raw:
        # 找到对应的配置
        match = min(rule_pts, key=lambda r: abs(r["discomfort"] - d)
                    + abs(r["cost"] - c))
        print(f"  {match['valley_t']:.0f}/{match['flat_t']:.0f}/"
              f"{match['peak_t']:.0f}".rjust(16)
              + f"{c:>12.3f}{d:>10.2%}{match['energy']:>9.3f}"
              f"{match['valley_share']:>8.1%}")

    rule_xs = [p[0] for p in rule_front_raw]
    print(f"\n  规则可达的不达标率范围: {min(rule_xs):.1%} ~ {max(rule_xs):.1%}")

    # ---------- PPO ----------
    print("\n" + "-" * 96)
    print(" PPO + 储能：temp_scale 扫描")
    print("-" * 96)
    print(f"  {'temp_scale':>11}{'电费元/天':>11}{'不达标':>10}{'能耗':>9}"
          f"{'谷段':>8}")
    print("  " + "-" * 54)
    ppo_pts = []
    for ts in args.temp_scales:
        rew = RewardConfig(**base, temp_scale=ts)
        ms = []
        for si in range(args.seeds):
            seed = 7000 + si * 71
            model = train_ppo(rew, seed, args.timesteps)
            ms.append(evaluate(lambda: SmartHomeEnv(rew_cfg=rew),
                               make_model_fn(model), args.episodes,
                               seed0=850000 + si * 1000))
        avg = {k: float(np.mean([m[k] for m in ms])) for k in ms[0]}
        avg["temp_scale"] = ts
        ppo_pts.append(avg)
        print(f"  {ts:>11.2f}{avg['cost']:>11.3f}{avg['discomfort']:>10.2%}"
              f"{avg['energy']:>9.3f}{avg['valley_share']:>8.1%}")

    ppo_front_raw = pareto_front([(p["discomfort"], p["cost"])
                                  for p in ppo_pts])
    ppo_xs = [p[0] for p in ppo_front_raw]
    print(f"\n  PPO 可达的不达标率范围: {min(ppo_xs):.1%} ~ {max(ppo_xs):.1%}")

    # ---------- 比较 ----------
    print("\n" + "=" * 96)
    print(" 等舒适度比较（核心结论）")
    print("=" * 96)

    lo = max(rule_front_raw[0][0], ppo_front_raw[0][0])
    hi = min(rule_front_raw[-1][0], ppo_front_raw[-1][0])

    rows, cross, wins_ppo, wins_rule = [], None, 0, 0
    if lo >= hi:
        print(f"\n  两条前沿的舒适度区间不重叠：")
        print(f"    规则 {rule_front_raw[0][0]:.1%} ~ {rule_front_raw[-1][0]:.1%}")
        print(f"    PPO  {ppo_front_raw[0][0]:.1%} ~ {ppo_front_raw[-1][0]:.1%}")
        print(f"\n  说明：即使给规则三个旋钮，其舒适度仍无法进入 PPO 的低不达标率区间。")
    else:
        print(f"\n  重叠区间: {lo:.1%} ~ {hi:.1%}")
        print(f"\n  {'不达标率':>10}{'规则电费':>11}{'PPO电费':>11}"
              f"{'差异':>10}{'占优方':>10}")
        print("  " + "-" * 56)
        for d in np.linspace(lo, hi, 11):
            rc = interp_front(rule_front_raw, d)
            pc = interp_front(ppo_front_raw, d)
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

    print("\n" + "-" * 96)
    if wins_ppo > 0:
        print(f"  ✅ [结论] 在 {wins_ppo} 段舒适度水平上，PPO 的电费更低！")
        if cross:
            first_ppo = rows[0][3] < 0
            print(f"     交叉点约在 不达标率 {cross:.1%} 处；")
            print(f"     低于该水平：{'PPO 更省' if first_ppo else '规则更省'}")
            print(f"     高于该水平：{'规则更省' if first_ppo else 'PPO 更省'}")
    else:
        print(f"  [结论] 在重叠区间内规则仍然占优（PPO 更优 {wins_ppo} 段，"
              f"规则更优 {wins_rule} 段）。")
        if rows:
            best = min(rows, key=lambda r: r[3])
            print(f"     PPO 最接近的水平: 不达标率 {best[0]:.1%} 时"
                  f"差异 {best[3]:+.1%}")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图 ----------
    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.scatter([p["discomfort"] * 100 for p in rule_pts],
               [p["cost"] for p in rule_pts], s=25, alpha=0.3,
               color="#1f77b4", label="规则+储能（全部配置）")
    ax.plot([d * 100 for d, _ in rule_front_raw],
            [c for _, c in rule_front_raw], "s-", color="#1f77b4",
            markersize=8, linewidth=2, label="规则+储能（前沿）")
    ax.scatter([p["discomfort"] * 100 for p in ppo_pts],
               [p["cost"] for p in ppo_pts], s=90, color="#d62728",
               marker="o", zorder=3, label="PPO+储能")
    ax.plot([d * 100 for d, _ in ppo_front_raw],
            [c for _, c in ppo_front_raw], "--", color="#d62728",
            linewidth=2, alpha=0.7)
    for p in ppo_pts:
        ax.annotate(f"ts={p['temp_scale']}",
                    (p["discomfort"] * 100, p["cost"]),
                    fontsize=7, textcoords="offset points", xytext=(5, 4))
    if lo < hi:
        ax.axvspan(lo * 100, hi * 100, alpha=0.08, color="green",
                   label=f"可比区间 {lo:.0%}~{hi:.0%}")
    if cross:
        ax.axvline(cross * 100, color="black", linestyle=":",
                   label=f"交叉点 {cross:.0%}")
    ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
    ax.set_ylabel("日均电费 (元)  —— 越下越好")
    ax.set_title("储能场景下的等舒适度比较（规则三旋钮）", fontsize=13)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, linestyle=":")

    fig.tight_layout()
    out = FIG_DIR / "storage_equal_comfort.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "rule_points": rule_pts,
                   "ppo_points": ppo_pts,
                   "rule_front": rule_front_raw, "ppo_front": ppo_front_raw,
                   "rows": rows, "cross": cross},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/storage_equal/results.json")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
