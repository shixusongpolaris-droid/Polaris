r"""
训练脚本 —— 用强化学习训练控制策略

本脚本做的事：
    1. 定义与基线同口径的评估流程（复用环境的三类指标接口）
    2. 用 PPO 训练智能体
    3. 把训练好的策略与多种基线在同一评估流程下对比
    4. 输出训练曲线与对比图

为什么评估流程要单独写：
    公平对比的前提是所有策略走完全相同的评估路径
    （相同种子、相同回合长度、相同指标口径）。
    因此这里不重新实现指标，直接调用 env 的 get_episode_metrics()。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    # 快速验证流程（约 1 分钟）
    python scripts\train.py --timesteps 20000 --eval-episodes 2

    # 正式训练
    python scripts\train.py --timesteps 200000 --eval-episodes 5

    # 消融实验：固定权重基线
    python scripts\train.py --weight-mode fixed --tag fixed_weight

输出：
    models\<tag>\            模型与训练日志
    models\figures\         对比图
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
from stable_baselines3.common.callbacks import BaseCallback
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


# ============================================================
# 基线策略（与 evaluate_all.py 保持同一实现口径）
# ============================================================
# 空调档位：0=关闭, 1=25°C, 2=26°C, 3=27°C, 4=28°C, 5=29°C
AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5


def _ac_idx_for(target_c, act_cfg):
    """把目标温度映射为最接近的空调档位索引。"""
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def _thermostat(env, target_c):
    """带滞环的恒温器，返回空调档位索引。"""
    t = env.get_obs_dict()["temp_in"]
    if t > target_c + 0.5:
        return _ac_idx_for(target_c, env.act_cfg)
    if t < target_c - 0.5:
        return AC_OFF
    return env.sim.state.ac_idx


def policy_always_ac(env):
    """全程开空调 25°C。"""
    return encode_action(AC_25C, 0, 0, env.act_cfg)


def policy_occ_only(env):
    """有人开、无人关，固定 25°C。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(AC_25C, light, 0, env.act_cfg)


def policy_rule(env):
    """规则控制：滞环恒温器（有人维持 25°C）。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    ac = _thermostat(env, 25.0)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


BASELINES = {
    "全程开空调25°C": policy_always_ac,
    "有人开无人关": policy_occ_only,
    "规则控制(滞环)": policy_rule,
}


# ============================================================
# 统一评估流程
# ============================================================
def evaluate_policy_fn(env_maker, action_fn, n_episodes=5, seed0=9000):
    """用统一的评估流程评价任意"动作函数"。

    返回：各指标的均值与标准差（跨回合）
    """
    keys = ["total_energy_kwh", "energy_per_day_kwh", "discomfort_ratio",
            "temp_dev_rmse", "response_delay_mean", "recovery_time_mean",
            "switch_per_day", "mean_reward"]
    collected = {k: [] for k in keys}

    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        while True:
            action = action_fn(env)
            _, _, term, trunc, info = env.step(action)
            if term or trunc:
                m = info["episode_metrics"]
                for k in keys:
                    collected[k].append(float(m.get(k, 0.0)))
                break
        env.close()

    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in collected.items()}


def evaluate_model(model, env_maker, n_episodes=5, seed0=9000, deterministic=True):
    """用统一评估流程评价训练好的模型。"""
    def fn(env):
        obs = env._to_array(env.get_obs_dict()) if hasattr(env, "_to_array") \
            else np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        action, _ = model.predict(obs, deterministic=deterministic)
        return int(action)
    return evaluate_policy_fn(env_maker, fn, n_episodes, seed0)


# ============================================================
# 训练过程记录
# ============================================================
class RewardLogCallback(BaseCallback):
    """记录每一步的回合奖励，用于画训练曲线。"""

    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.episode_rewards = []
        self.episode_lengths = []
        self.timesteps = []

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        for info in infos:
            if "episode" in info:
                self.episode_rewards.append(float(info["episode"]["r"]))
                self.episode_lengths.append(int(info["episode"]["l"]))
                self.timesteps.append(int(self.num_timesteps))
        return True


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="训练智能家居控制策略")
    ap.add_argument("--timesteps", type=int, default=200000,
                    help="总训练步数")
    ap.add_argument("--eval-episodes", type=int, default=5,
                    help="评估回合数")
    ap.add_argument("--weight-mode", type=str, default="scenario",
                    choices=["scenario", "fixed", "random"],
                    help="能耗权重模式：scenario=本方法，fixed=固定权重基线，random=随机对照")
    ap.add_argument("--tou", action="store_true",
                    help="启用分时电价（TOU）")
    ap.add_argument("--energy-scale", type=float, default=None,
                    help="能耗项权重倍数（默认用 RewardConfig 的设定值）")
    ap.add_argument("--temp-scale", type=float, default=None,
                    help="温度惩罚强度（默认用 RewardConfig 的设定值）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    ap.add_argument("--tag", type=str, default=None, help="输出目录名")
    ap.add_argument("--n-envs", type=int, default=4, help="并行环境数")
    ap.add_argument("--gamma", type=float, default=0.99, help="折扣因子")
    ap.add_argument("--skip-baselines", action="store_true",
                    help="跳过基线评估（加速调试）")
    args = ap.parse_args()

    tag = args.tag or f"ppo_{args.weight_mode}"
    out_dir = PROJECT_ROOT / "models" / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 奖励配置 ----------
    rew_cfg = RewardConfig(weight_mode=args.weight_mode,
                           tou_enabled=args.tou)
    if args.energy_scale is not None:
        rew_cfg.energy_scale = args.energy_scale
    if args.temp_scale is not None:
        rew_cfg.temp_scale = args.temp_scale

    def make_env(seed=0):
        env = SmartHomeEnv(rew_cfg=rew_cfg, seed=seed)
        return Monitor(env)

    print("=" * 72)
    print(" 强化学习训练 —— 智能家居自适应决策系统")
    print("=" * 72)
    print(f"  能耗权重模式 : {args.weight_mode}")
    print(f"  总训练步数   : {args.timesteps:,}")
    print(f"  并行环境数   : {args.n_envs}")
    print(f"  折扣因子 γ   : {args.gamma}")
    print(f"  随机种子     : {args.seed}")
    print(f"  输出目录     : models/{tag}")
    print(f"  回合长度     : {SmartHomeEnv().episode_steps} 步 "
          f"({SmartHomeEnv().episode_steps / 96:.0f} 天)")

    # ---------- 基线评估 ----------
    baseline_results = {}
    if not args.skip_baselines:
        print("\n" + "-" * 72)
        print(" 步骤 1/3：评估基线策略（统一评估流程，各 "
              f"{args.eval_episodes} 回合）")
        print("-" * 72)
        for name, fn in BASELINES.items():
            res = evaluate_policy_fn(lambda: SmartHomeEnv(rew_cfg=rew_cfg),
                                     fn, args.eval_episodes)
            baseline_results[name] = res
            print(f"  {name:<16} 能耗 {res['energy_per_day_kwh'][0]:6.3f} kWh/天   "
                  f"不达标 {res['discomfort_ratio'][0]:6.1%}   "
                  f"切换 {res['switch_per_day'][0]:6.1f} 次/天")

    # ---------- 训练 ----------
    print("\n" + "-" * 72)
    print(" 步骤 2/3：训练 PPO")
    print("-" * 72)

    vec_env = DummyVecEnv([lambda i=i: make_env(args.seed + i)
                           for i in range(args.n_envs)])

    model = PPO(
        "MlpPolicy",
        vec_env,
        verbose=0,
        seed=args.seed,
        gamma=args.gamma,
        n_steps=1024,
        batch_size=256,
        learning_rate=3e-4,
        ent_coef=0.01,          # 略加熵奖励，鼓励探索
        policy_kwargs=dict(net_arch=[128, 128]),
        # 不使用 tensorboard 日志：训练曲线由 RewardLogCallback 记录，
        # 避免引入 tensorboard 依赖
    )

    n_params = sum(p.numel() for p in model.policy.parameters())
    print(f"  策略网络参数量: {n_params:,}")
    print(f"  开始训练（{args.timesteps:,} 步）...")

    cb = RewardLogCallback()
    t0 = time.time()
    model.learn(total_timesteps=args.timesteps, callback=cb,
                progress_bar=False)
    train_time = time.time() - t0

    model.save(str(out_dir / "final_model"))
    print(f"  训练完成，耗时 {train_time / 60:.1f} 分钟")
    if cb.episode_rewards:
        # 用最后 20% 回合的平均奖励作为收敛指标
        k = max(1, len(cb.episode_rewards) // 5)
        early = np.mean(cb.episode_rewards[:k])
        late = np.mean(cb.episode_rewards[-k:])
        print(f"  回合奖励：训练初期 {early:.1f}  ->  训练后期 {late:.1f}")
        print(f"  共完成 {len(cb.episode_rewards)} 个回合")

    # ---------- 评估训练结果 ----------
    print("\n" + "-" * 72)
    print(" 步骤 3/3：评估训练后的策略")
    print("-" * 72)
    rl_res = evaluate_model(model, lambda: SmartHomeEnv(rew_cfg=rew_cfg),
                            args.eval_episodes, deterministic=True)
    print(f"  PPO（本方法）    能耗 {rl_res['energy_per_day_kwh'][0]:6.3f} kWh/天   "
          f"不达标 {rl_res['discomfort_ratio'][0]:6.1%}   "
          f"切换 {rl_res['switch_per_day'][0]:6.1f} 次/天")

    # ---------- 对比汇总 ----------
    all_results = dict(baseline_results)
    all_results["PPO(本方法)"] = rl_res

    print("\n" + "=" * 72)
    print(" 策略对比汇总")
    print("=" * 72)
    hdr = (f"  {'策略':<18}{'日均能耗':>11}{'不达标占比':>12}"
           f"{'偏差RMSE':>11}{'响应延迟':>11}{'日均切换':>11}")
    print(hdr)
    print("  " + "-" * 70)
    rows = []
    for name, r in all_results.items():
        e, _ = r["energy_per_day_kwh"]
        d, _ = r["discomfort_ratio"]
        rmse, _ = r["temp_dev_rmse"]
        delay, _ = r["response_delay_mean"]
        sw, _ = r["switch_per_day"]
        rows.append((name, e, d, rmse, delay, sw))
        print(f"  {name:<18}{e:>11.3f}{d:>12.1%}{rmse:>11.3f}"
              f"{delay:>11.2f}{sw:>11.1f}")
    print("  " + "-" * 70)

    # ---------- 结论判断 ----------
    print("\n  判断：")

    def pareto_front(points):
        """找出帕累托前沿的点（能耗越低越好，不达标率越低越好）。

        points: list of (name, energy, discomfort)
        返回：前沿上的点（不含被支配的点）
        """
        front = []
        for i, (n_i, e_i, d_i) in enumerate(points):
            dominated = False
            for j, (n_j, e_j, d_j) in enumerate(points):
                if i == j:
                    continue
                # 若存在另一个点"能耗不更高 且 不达标率不更高，且至少一项更低"
                if (e_j <= e_i + 1e-9 and d_j <= d_i + 1e-9
                        and (e_j < e_i - 1e-9 or d_j < d_i - 1e-9)):
                    dominated = True
                    break
            if not dominated:
                front.append((n_i, e_i, d_i))
        return front

    pts = [(n, r["energy_per_day_kwh"][0], r["discomfort_ratio"][0])
           for n, r in all_results.items()]
    front = pareto_front(pts)
    rl_pt = next((p for p in pts if "PPO" in p[0]), None)

    print(f"    帕累托前沿上的策略（能耗与舒适度均不被其他策略同时超越）：")
    for n, e, d in front:
        mark = "  <-- 本方法" if "PPO" in n else ""
        print(f"      {n:<18} 能耗 {e:6.3f} kWh/天   不达标 {d:6.1%}{mark}")

    if rl_pt:
        on_front = any("PPO" in n for n, _, _ in front)
        if on_front:
            print(f"\n    [结论] 本方法位于帕累托前沿上，说明它在"
                  f"'舒适度—能耗'的权衡中不劣于任何基线。")
            print(f"           可进一步分析：引入时间场景化权重后，")
            print(f"           相比固定权重是否带来了前沿的改善（见消融实验）。")
        else:
            # 找出支配本方法的基线
            dominators = [(n, e, d) for n, e, d in pts
                          if "PPO" not in n
                          and e <= rl_pt[1] + 1e-9 and d <= rl_pt[2] + 1e-9
                          and (e < rl_pt[1] - 1e-9 or d < rl_pt[2] - 1e-9)]
            print(f"\n    [结论] 本方法未进入帕累托前沿，被以下基线支配：")
            for n, e, d in dominators:
                print(f"           {n}: 能耗 {e:.3f} kWh/天, 不达标 {d:.1%}")
            print(f"\n           这是有价值的诚实结论。可能原因与后续方向：")
            print(f"             1) 训练不充分 —— 增加步数或调整超参数")
            print(f"             2) 当前场景过于简单 —— 强化学习的优势体现在")
            print(f"                复杂场景（分时电价、多房间、多设备耦合）")
            print(f"             3) 奖励权重需要整定 —— 用参数扫描确定合适的 λ_e")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))

    # 图 1：训练曲线
    ax = axes[0]
    if cb.episode_rewards:
        r = np.array(cb.episode_rewards)
        ax.plot(r, alpha=0.35, color="#1f77b4", label="每回合奖励")
        if len(r) >= 10:
            w = max(3, len(r) // 20)
            ma = np.convolve(r, np.ones(w) / w, mode="valid")
            ax.plot(range(w - 1, len(r)), ma, color="#d62728",
                    linewidth=2, label=f"滑动平均(窗口{w})")
        ax.set_xlabel("回合序号")
        ax.set_ylabel("回合累计奖励")
        ax.set_title("训练曲线", fontsize=12)
        ax.legend(fontsize=9)
    else:
        ax.text(0.5, 0.5, "回合数不足，未产生曲线",
                ha="center", va="center", transform=ax.transAxes)
    ax.grid(alpha=0.3, linestyle=":")

    # 图 2：能耗对比
    ax = axes[1]
    names = [r[0] for r in rows]
    energies = [r[1] for r in rows]
    colors = ["#2ca02c" if "PPO" in n else "#1f77b4" for n in names]
    bars = ax.barh(names, energies, color=colors)
    for b, e in zip(bars, energies):
        ax.text(e + 0.05, b.get_y() + b.get_height() / 2,
                f"{e:.2f}", va="center", fontsize=9)
    ax.set_xlabel("日均能耗 (kWh)")
    ax.set_title("能耗对比（越短越好）", fontsize=12)
    ax.invert_yaxis()
    ax.grid(alpha=0.3, axis="x", linestyle=":")

    # 图 3：舒适度对比
    ax = axes[2]
    discom = [r[2] * 100 for r in rows]
    bars = ax.barh(names, discom, color=colors)
    for b, d in zip(bars, discom):
        ax.text(d + 0.6, b.get_y() + b.get_height() / 2,
                f"{d:.1f}%", va="center", fontsize=9)
    ax.set_xlabel("舒适度不达标占比 (%)")
    ax.set_title("舒适度对比（越短越好）", fontsize=12)
    ax.invert_yaxis()
    ax.grid(alpha=0.3, axis="x", linestyle=":")

    fig.suptitle(f"强化学习 vs 基线策略（权重模式：{args.weight_mode}，"
                 f"{args.timesteps:,} 步训练）", fontsize=13)
    fig.tight_layout()
    fig_path = FIG_DIR / f"train_compare_{tag}.png"
    fig.savefig(fig_path, dpi=120)
    plt.close(fig)
    print(f"\n  对比图已保存: {fig_path}")

    # ---------- 保存结果 ----------
    result = {
        "config": vars(args),
        "train_time_sec": train_time,
        "n_episodes_trained": len(cb.episode_rewards),
        "policy_params": n_params,
        "results": {k: {kk: list(vv) for kk, vv in v.items()}
                    for k, v in all_results.items()},
    }
    with open(out_dir / "train_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  结果数据已保存: models/{tag}/train_result.json")

    # 训练曲线数据
    if cb.episode_rewards:
        np.savez(out_dir / "training_curve.npz",
                 rewards=np.array(cb.episode_rewards),
                 lengths=np.array(cb.episode_lengths),
                 timesteps=np.array(cb.timesteps))
        print(f"  训练曲线数据已保存: models/{tag}/training_curve.npz")

    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
