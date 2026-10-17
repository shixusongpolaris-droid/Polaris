r"""
奖励权重标定实验 —— 确定"舒适度 vs 电费"的合理权重比例

问题背景：
    分时电价实验中发现，强化学习把舒适度做到 0.03%（几乎完美），
    但电费（22.4 元）高于规则控制（13.7 元）。
    原因是奖励函数中舒适度与能耗的相对权重不合适：
        温度偏离 1°C 的代价 = temp_scale = 3.0（归一化单位）
        峰段 1 kWh 电的代价   = energy_scale × 1.0 元
    当前 energy_scale = 10，即停开空调 1 kWh（峰段约 0.1375 kWh/步）
    只值约 1.4 单位，而多容忍 1°C 偏离值 3.0 单位。
    结果算法选择"宁可多用电也要保舒适"，这与用户的省钱诉求不符。

    更合理的标定目标：让"牺牲 1°C 舒适度"与"节省 1 kWh 电费"的
    代价处于同一量级，使算法在两者间做出真实权衡。

本实验做法：
    扫描 energy_scale（能耗项权重倍数），对每个取值训练并评估 PPO，
    观察：
        - 电费随 energy_scale 增大的变化
        - 舒适度不达标率随 energy_scale 增大的变化
        - 是否存在一个取值使两者达到合理折中
    同时以规则控制（B2）作为参考基准。

运行方式：
    python scripts\reward_calibration.py --seeds 2 --episodes 8
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
OUT_DIR = PROJECT_ROOT / "models" / "reward_calibration"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def ac_idx_for(target_c, cfg):
    """把目标温度映射为最接近的空调档位索引（0 = 关闭）。

    必须动态推导，不能写死 ac_idx。
    2026-10-17 空调档位由 [25..29]（ac_idx 1..5）扩展为
    [23..29]（ac_idx 1..7），原先写死的 1/2/3 已分别变成
    23/24/25 °C，与声明的 24/25/27 °C 不符。
    （由团队审计成员 script-auditor 发现）
    """
    best, best_d = 0, 1e18
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def policy_rule_tou(env):
    """参考基准：按时段调温的规则控制（B2）。

    与 tou_experiment.py 的同名策略保持完全一致：
    谷段 24 °C、平段 25 °C、峰段 27 °C，无人关闭。
    """
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    tier = obs["price_tier"]
    t = obs["temp_in"]
    if tier == 0.0:
        tt = 24.0
    elif tier == 2.0:
        tt = 27.0
    else:
        tt = 25.0
    if t > tt + 0.5:
        ac = ac_idx_for(tt, env.act_cfg)
    elif t < tt - 0.5:
        ac = 0
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def run_episodes(env_maker, action_fn, n_episodes, seed0):
    out = {"energy": [], "cost": [], "discomfort": [], "rmse": [], "switch": []}
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
                out["energy"].append(m["energy_per_day_kwh"])
                out["discomfort"].append(m["discomfort_ratio"])
                out["rmse"].append(m["temp_dev_rmse"])
                out["switch"].append(m["switch_per_day"])
                out["cost"].append(cost)
                break
        env.close()
    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in out.items()}


def train_and_eval(rew_cfg, n_seeds, n_episodes, timesteps, n_envs=4):
    acc = {"energy": [], "cost": [], "discomfort": [], "rmse": [], "switch": []}
    for si in range(n_seeds):
        seed = 300 + si * 13
        vec = DummyVecEnv([
            (lambda i=i: Monitor(SmartHomeEnv(rew_cfg=rew_cfg,
                                              seed=seed + i)))
            for i in range(n_envs)])
        model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                    n_steps=1024, batch_size=256, learning_rate=3e-4,
                    ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
        model.learn(total_timesteps=timesteps)
        r = run_episodes(
            lambda: SmartHomeEnv(rew_cfg=rew_cfg),
            lambda env: int(model.predict(
                np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32), deterministic=True)[0]),
            n_episodes, seed0=60000 + si * 1000)
        for k in acc:
            acc[k].extend(r[k])
        vec.close()
    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def main():
    ap = argparse.ArgumentParser(description="奖励权重标定")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=150000)
    ap.add_argument("--scales", type=float, nargs="+",
                    default=[20.0, 40.0, 80.0])
    ap.add_argument("--temp-scales", type=float, nargs="+",
                    default=[0.5, 1.0, 2.0])
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78)
    print(" 奖励权重标定实验 —— 校准'舒适度 vs 电费'的权重比例")
    print("=" * 78)
    print(f"  扫描 energy_scale: {args.scales}")
    print(f"  {args.seeds} 种子 × {args.episodes} 回合/配置，"
          f"每组训练 {args.timesteps:,} 步")
    print("\n  说明：energy_scale 越大，算法越重视电费。")

    # 参考基准
    rew_ref = RewardConfig(weight_mode="scenario", tou_enabled=True)
    ref = {"energy": [], "cost": [], "discomfort": [], "rmse": [], "switch": []}
    for s in range(args.seeds):
        r = run_episodes(lambda: SmartHomeEnv(rew_cfg=rew_ref),
                         policy_rule_tou, args.episodes, seed0=70000 + s * 1000)
        for k in ref:
            ref[k].extend(r[k])
    ref = {k: (float(np.mean(v)), float(np.std(v))) for k, v in ref.items()}
    print(f"\n  参考基准（规则控制 B2）: 电费 {ref['cost'][0]:.3f} 元/天, "
          f"不达标 {ref['discomfort'][0]:.2%}, 电量 {ref['energy'][0]:.3f} kWh/天")

    # ---------- 扫描 ----------
    # 两个可调量：
    #   temp_scale   —— 温度偏离的惩罚强度。越小则算法越愿意容忍不舒适以省电
    #   energy_scale —— 能耗代价的权重倍数。越大则算法越重视电费
    print(f"\n  {'temp_scale':>11}{'energy_scale':>13}{'电量kWh':>10}"
          f"{'电费元':>9}{'不达标':>9}{'RMSE':>8}{'切换':>7}")
    print("  " + "-" * 68)

    rows = []
    for ts in args.temp_scales:
        for es in args.scales:
            rew = RewardConfig(weight_mode="scenario", tou_enabled=True,
                               energy_scale=es, temp_scale=ts)
            r = train_and_eval(rew, args.seeds, args.episodes, args.timesteps)
            rows.append((ts, es, r))
            print(f"  {ts:>11.2f}{es:>13.1f}{r['energy'][0]:>10.3f}"
                  f"{r['cost'][0]:>9.3f}{r['discomfort'][0]:>9.2%}"
                  f"{r['rmse'][0]:>8.3f}{r['switch'][0]:>7.1f}")
    print("  " + "-" * 68)
    print(f"  {'(规则基准)':>24}{ref['energy'][0]:>10.3f}"
          f"{ref['cost'][0]:>9.3f}{ref['discomfort'][0]:>9.2%}"
          f"{ref['rmse'][0]:>8.3f}{ref['switch'][0]:>7.1f}")

    # ---------- 分析 ----------
    print("\n" + "=" * 78)
    print(" 分析")
    print("=" * 78)

    costs = [r[2]["cost"][0] for r in rows]
    discom = [r[2]["discomfort"][0] for r in rows]

    # 找到电费低于规则基准的配置
    cheaper = [(ts, es, r) for ts, es, r in rows
               if r["cost"][0] < ref["cost"][0]]
    print(f"\n  电费低于规则基准（{ref['cost'][0]:.3f} 元）的配置:")
    if cheaper:
        for ts, es, r in cheaper:
            print(f"    temp_scale={ts:.2f}, energy_scale={es:>5.1f}: "
                  f"电费 {r['cost'][0]:.3f} 元, "
                  f"不达标 {r['discomfort'][0]:.2%}")
        best_ts, best_es, best = min(cheaper, key=lambda x: x[2]["cost"][0])
        saving = (ref["cost"][0] - best["cost"][0]) / ref["cost"][0]
        print(f"\n  最优配置: temp_scale={best_ts}, energy_scale={best_es}")
        print(f"    电费: {best['cost'][0]:.3f} 元/天  vs  规则 "
              f"{ref['cost'][0]:.3f}  -> 降低 {saving:.1%}")
        print(f"    不达标: {best['discomfort'][0]:.2%}  vs  规则 "
              f"{ref['discomfort'][0]:.2%}")
    else:
        print(f"    无。说明在当前范围内 PPO 尚未学会兼顾电费。")
        best_ts, best_es, best = min(rows, key=lambda x: x[2]["cost"][0])
        print(f"    最优（电费最低）: temp_scale={best_ts}, "
              f"energy_scale={best_es}, 电费 {best['cost'][0]:.3f} 元, "
              f"不达标 {best['discomfort'][0]:.2%}")

    # 在"舒适度可接受"的配置里找电费最低的
    tol = ref["discomfort"][0] + 0.05
    acceptable = [(ts, es, r) for ts, es, r in rows
                  if r["discomfort"][0] <= tol]
    print(f"\n  舒适度可接受（不达标 ≤ {tol:.1%}）的配置中，电费最低的:")
    if acceptable:
        b_ts, b_es, b = min(acceptable, key=lambda x: x[2]["cost"][0])
        sav = (ref["cost"][0] - b["cost"][0]) / ref["cost"][0]
        print(f"    temp_scale={b_ts}, energy_scale={b_es}")
        print(f"    电费 {b['cost'][0]:.3f} 元（规则 {ref['cost'][0]:.3f}，"
              f"变化 {sav:+.1%}），不达标 {b['discomfort'][0]:.2%}")
    else:
        print(f"    无配置满足该舒适度要求。")

    # 检查单调性
    e_mono = all(costs[i] >= costs[i + 1] - 0.05 for i in range(len(costs) - 1))
    print(f"\n  电费随参数变化是否单调: {'是' if e_mono else '否（存在波动，可能训练不足）'}")
    print(f"  电费范围: {min(costs):.3f} ~ {max(costs):.3f} 元")
    print(f"  不达标范围: {min(discom):.2%} ~ {max(discom):.2%}")

    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    # ---------- 画图：两个参数的热图 ----------
    tss = sorted(set(r[0] for r in rows))
    ess = sorted(set(r[1] for r in rows))
    cost_mat = np.full((len(tss), len(ess)), np.nan)
    disc_mat = np.full((len(tss), len(ess)), np.nan)
    for ts, es, r in rows:
        i, j = tss.index(ts), ess.index(es)
        cost_mat[i, j] = r["cost"][0]
        disc_mat[i, j] = r["discomfort"][0] * 100

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, mat, title, unit in [
            (axes[0], cost_mat, "日均电费 (元)", "元"),
            (axes[1], disc_mat, "舒适度不达标占比 (%)", "%")]:
        im = ax.imshow(mat, aspect="auto", cmap="RdYlGn_r", origin="lower")
        ax.set_xticks(range(len(ess)))
        ax.set_xticklabels([f"{e:.0f}" for e in ess])
        ax.set_yticks(range(len(tss)))
        ax.set_yticklabels([f"{t:.2f}" for t in tss])
        ax.set_xlabel("energy_scale（能耗权重倍数）")
        ax.set_ylabel("temp_scale（温度惩罚强度）")
        ax.set_title(title, fontsize=12)
        for i in range(len(tss)):
            for j in range(len(ess)):
                if not np.isnan(mat[i, j]):
                    ax.text(j, i, f"{mat[i, j]:.1f}", ha="center",
                            va="center", fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046, label=unit)
    fig.suptitle("奖励权重标定：两个参数的联合影响", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "reward_calibration.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"rows": [(ts, es, {k: list(v) for k, v in r.items()})
                            for ts, es, r in rows],
                   "reference": {k: list(v) for k, v in ref.items()},
                   "args": vars(args)}, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: models/reward_calibration/results.json")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
