r"""
可视化脚本 —— 直观检查环境行为，并对比几种控制策略

本脚本做的事：
    1. 定义四种控制策略（含规则控制基线 B1，后续对比实验要用）
    2. 在同一组天气与占用序列下各跑一个完整回合（7 天）
    3. 画出温度曲线、舒适区间、占用情况、空调动作
    4. 输出各策略的对比指标表

运行方式：
    cd /d D:\Dev\Projects\SmartHome
    .venv\Scripts\activate
    python scripts\visualize_env.py

输出：
    models\figures\env_overview.png   （四联图）
    models\figures\env_context.png    （室外环境与人员占用）
"""

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

from src.env import SmartHomeEnv, encode_action

# ---------- 中文字体设置 ----------
_available = {f.name for f in font_manager.fontManager.ttflist}
for _cand in ["Microsoft YaHei", "SimHei", "DengXian", "SimSun"]:
    if _cand in _available:
        plt.rcParams["font.sans-serif"] = [_cand]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 策略定义
# ============================================================
# 空调档位：0=关闭, 1=25°C, 2=26°C, 3=27°C, 4=28°C, 5=29°C
AC_OFF = 0
AC_25C, AC_26C, AC_27C, AC_28C, AC_29C = 1, 2, 3, 4, 5


def _ac_idx_for(target_c, act_cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def policy_never_ac(env):
    """策略 0：永不开空调（能耗下界参照）。"""
    return encode_action(AC_OFF, 0, 0, env.act_cfg)


def policy_always_ac(env):
    """策略 1：全程开空调 25°C（舒适上界参照，但极耗能）。"""
    return encode_action(AC_25C, 0, 0, env.act_cfg)


def policy_rule(env):
    """策略 2：规则控制（对比基线 B1）。

    规则内容（模拟常见的恒温器 + 人在感应控制）：
        无人                    -> 全部关闭
        有人 + 室温 > 25.5°C    -> 开空调 25°C 档
        有人 + 室温 < 24.5°C    -> 关空调
        有人 + 24.5~25.5°C      -> 维持现状（滞环，避免抖动）
        有人 + 光照不足         -> 开灯（弱档）
    """
    obs = env.get_obs_dict()
    cfg = env.rew_cfg
    temp = obs["temp_in"]
    occupied = bool(obs["occ_a"] or obs["occ_b"])
    light_in = obs["light_in"]

    if not occupied:
        return encode_action(AC_OFF, 0, 0, env.act_cfg)

    if temp > 25.5:
        ac_idx = AC_25C
    elif temp < 24.5:
        ac_idx = AC_OFF
    else:
        ac_idx = env.sim.state.ac_idx

    light_idx = 1 if light_in < cfg.light_min_lux else 0
    return encode_action(ac_idx, light_idx, 0, env.act_cfg)


def policy_occ_only(env):
    """策略 2b：人在感应 + 定温（基线 B2）。"""
    obs = env.get_obs_dict()
    occupied = bool(obs["occ_a"] or obs["occ_b"])
    cfg = env.rew_cfg
    light_in = obs["light_in"]

    if not occupied:
        return encode_action(AC_OFF, 0, 0, env.act_cfg)

    light_idx = 1 if light_in < cfg.light_min_lux else 0
    return encode_action(AC_25C, light_idx, 0, env.act_cfg)


def policy_random(env):
    """策略 3：随机动作（强化学习训练前的下界参照）。"""
    return env.action_space.sample()


POLICIES = [
    ("随机策略（RL 下界参照）", policy_random),
    ("规则控制（基线 B1）", policy_rule),
    ("人在感应+定温（基线 B2）", policy_occ_only),
    ("全程开空调 25°C", policy_always_ac),
    ("全程不开空调", policy_never_ac),
]


# ============================================================
# 运行一个回合，记录轨迹
# ============================================================
def run_episode(policy_fn, seed: int = 2026, episode_steps: int = None):
    env = SmartHomeEnv(seed=seed, episode_steps=episode_steps)
    env.reset(seed=seed)

    traj = {
        "temp": [], "hum": [], "light": [], "temp_out": [],
        "occ": [], "ac": [], "light_idx": [], "energy_cum": [],
        "reward": [], "lambda": [], "time": [],
        "band_low": [], "band_high": [],
        "energy_step": [],
    }

    while True:
        action = policy_fn(env)
        obs, reward, terminated, truncated, info = env.step(action)
        o = info["obs_dict"]
        c = info["components"]

        traj["temp"].append(o["temp_in"])
        traj["hum"].append(o["hum_in"])
        traj["light"].append(o["light_in"])
        traj["temp_out"].append(o["temp_out"])
        traj["occ"].append(1 if (o["occ_a"] or o["occ_b"]) else 0)
        traj["ac"].append(int(o["ac_idx"]))
        traj["light_idx"].append(int(o["light_idx"]))
        traj["energy_cum"].append(info["energy_kwh_cum"])
        traj["energy_step"].append(info["energy_kwh_step"])
        traj["reward"].append(reward)
        traj["lambda"].append(c["lambda_energy"])
        traj["time"].append(info["sim_time"])
        lo, hi = env.reward_calc.temp_band(
            bool(o["occ_a"] or o["occ_b"]), int(o["hour"]))
        traj["band_low"].append(lo)
        traj["band_high"].append(hi)

        if terminated or truncated:
            metrics = info["episode_metrics"]
            break

    env.close()
    return traj, metrics


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 68)
    print(" 环境可视化 —— 四策略对比（同一组天气与占用序列，7 天）")
    print("=" * 68)

    results = {}
    for name, fn in POLICIES:
        print(f"\n正在运行：{name} ...")
        traj, metrics = run_episode(fn, seed=2026)
        results[name] = (traj, metrics)
        print(f"  完成。日均能耗 {metrics['energy_per_day_kwh']:.3f} kWh，"
              f"不达标占比 {metrics['discomfort_ratio']:.1%}，"
              f"日均切换 {metrics['switch_per_day']:.1f} 次")

    # ---------- 指标对比表 ----------
    print("\n" + "=" * 68)
    print(" 指标对比")
    print("=" * 68)
    header = (f"  {'策略':<24}{'总能耗':>9}{'日均能耗':>10}"
              f"{'不达标':>9}{'偏差RMSE':>10}{'日均切换':>10}{'日均启动':>10}")
    print(header)
    print("  " + "-" * 76)
    for name, (_, m) in results.items():
        print(f"  {name:<24}{m['total_energy_kwh']:>9.3f}"
              f"{m['energy_per_day_kwh']:>10.3f}"
              f"{m['discomfort_ratio']:>9.1%}"
              f"{m['temp_dev_rmse']:>10.3f}"
              f"{m['switch_per_day']:>10.1f}"
              f"{m.get('ac_start_per_day', 0):>10.1f}")
    print("  " + "-" * 76)
    print("  说明：能耗为仿真模型计算值（非实测），已含压缩机启动损耗")
    print("        不达标 = 温度超出舒适区间的时长占比")
    print("        日均启动 = 空调压缩机每天启动次数（频繁启停会增加损耗与能耗）")

    # ---------- 画图 ----------
    n_pol = len(results)
    fig, axes = plt.subplots(n_pol, 1, figsize=(15, 3.2 * n_pol), sharex=True)
    colors = ["#d62728", "#1f77b4", "#9467bd", "#2ca02c", "#7f7f7f"]

    for ax, (name, (traj, m)), color in zip(axes, results.items(), colors):
        x = np.arange(len(traj["temp"]))
        temp = np.array(traj["temp"])
        lo = np.array(traj["band_low"])
        hi = np.array(traj["band_high"])
        ac = np.array(traj["ac"])
        occ = np.array(traj["occ"])

        # 舒适区间（灰色带）
        ax.fill_between(x, lo, hi, step="post", color="#cccccc",
                        alpha=0.55, label="舒适区间", zorder=1)
        # 有人在室的时段（底部浅色标记）
        ax.fill_between(x, 12, 14, where=(occ == 1), step="post",
                        color="#ffd8a8", alpha=0.9, label="有人", zorder=2)
        # 空调运行时段（底部标记）
        ax.fill_between(x, 14, 16, where=(ac > 0), step="post",
                        color="#8ecae6", alpha=0.9, label="空调运行", zorder=2)
        # 温度曲线
        ax.plot(x, temp, color=color, linewidth=1.4, label="室温", zorder=3)
        # 室外温度（虚线参考）
        ax.plot(x, traj["temp_out"], color="#999999", linewidth=0.9,
                linestyle="--", label="室外温度", zorder=3)

        ax.set_ylim(10, 38)
        ax.set_ylabel("温度 (°C)")
        ax.set_title(f"{name}    日均能耗 {m['energy_per_day_kwh']:.2f} kWh    "
                     f"不达标 {m['discomfort_ratio']:.1%}    "
                     f"日均切换 {m['switch_per_day']:.0f} 次",
                     fontsize=11, loc="left")
        ax.grid(alpha=0.25, linestyle=":")
        if ax is axes[0]:
            ax.legend(loc="upper right", ncol=5, fontsize=9, framealpha=0.9)

    # x 轴按天标注
    n = len(results[list(results)[0]][0]["temp"])
    steps_per_day = 96
    ticks = list(range(0, n, steps_per_day))
    labels = [f"第{i // steps_per_day + 1}天" for i in ticks]
    axes[-1].set_xticks(ticks)
    axes[-1].set_xticklabels(labels)
    axes[-1].set_xlabel("仿真时间（7 天，每步 15 分钟）")

    fig.suptitle("智能家居环境控制 —— 四种策略对比（相同天气与占用序列）",
                 fontsize=14, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    out1 = FIG_DIR / "env_overview.png"
    fig.savefig(out1, dpi=110)
    plt.close(fig)
    print(f"\n已保存: {out1}")

    # ---------- 第二张图：环境上下文 ----------
    fig2, axes2 = plt.subplots(4, 1, figsize=(15, 10), sharex=True)
    name0 = list(results)[0]
    traj0 = results[name0][0]
    x = np.arange(len(traj0["temp"]))

    axes2[0].plot(x, traj0["light"], color="#e8a33d", linewidth=1.0)
    axes2[0].set_ylabel("室内照度 (lx)")
    axes2[0].set_title("室内光照", loc="left", fontsize=11)

    axes2[1].plot(x, traj0["hum"], color="#2a9d8f", linewidth=1.0)
    axes2[1].axhspan(40, 60, color="#cccccc", alpha=0.5)
    axes2[1].set_ylabel("湿度 (%)")
    axes2[1].set_title("室内湿度（灰带为舒适区间 40–60%）", loc="left", fontsize=11)

    axes2[2].plot(x, traj0["energy_step"], color="#5f4b8b", linewidth=0.8)
    axes2[2].set_ylabel("单步能耗 (kWh)")
    axes2[2].set_title(f"单步能耗（策略：{name0}）", loc="left", fontsize=11)

    axes2[3].plot(x, traj0["lambda"], color="#c1121f", linewidth=1.6,
                  drawstyle="steps-post")
    axes2[3].set_ylabel("能耗权重 λe")
    axes2[3].set_title("场景化能耗权重 λe（注意周五/周日晚间的差异）",
                       loc="left", fontsize=11)
    axes2[3].set_xticks(ticks)
    axes2[3].set_xticklabels(labels)
    axes2[3].set_xlabel("仿真时间（7 天，每步 15 分钟）")

    for ax in axes2:
        ax.grid(alpha=0.25, linestyle=":")
    fig2.suptitle("环境上下文变量（天气 / 占用 / 权重）", fontsize=14, y=0.995)
    fig2.tight_layout(rect=[0, 0, 1, 0.98])
    out2 = FIG_DIR / "env_context.png"
    fig2.savefig(out2, dpi=110)
    plt.close(fig2)
    print(f"已保存: {out2}")

    # ---------- 场景化权重分布检查 ----------
    print("\n" + "=" * 68)
    print(" 场景化权重 λe 的实际取值分布（验证时间场景机制）")
    print("=" * 68)
    lam = np.array(traj0["lambda"])
    occ = np.array(traj0["occ"])
    times = traj0["time"]
    for val in sorted(set(np.round(lam, 3))):
        mask = np.isclose(lam, val)
        # 找出该权重对应的典型场景
        hours = np.array([t.hour for t in times])[mask]
        dows = np.array([t.weekday() for t in times])[mask]
        occ_r = occ[mask].mean()
        print(f"  λe = {val:<5} 出现 {mask.sum():>4} 步 "
              f"({mask.sum() / len(lam):>5.1%})   有人比例 {occ_r:>5.1%}   "
              f"工作日/周末 {np.mean(dows < 5):.0%}/{np.mean(dows >= 5):.0%}")
    print("\n  说明：不同 λe 对应不同时间场景，说明场景化机制在数据上确实生效。")
    print("=" * 68)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
