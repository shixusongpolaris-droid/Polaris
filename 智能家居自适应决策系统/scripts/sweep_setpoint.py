r"""
设定温度扫描 —— 检查"舒适度 vs 能耗"是否存在真实权衡

为什么需要这个检查：
    如果"全程开空调"这一策略在能耗和舒适度两方面都优于其他策略，
    说明它处于帕累托最优点，那么强化学习将没有可改进的空间，
    整个课题的优化目标就不成立。

    因此必须先确认：存在一个策略集合，使得
        - 提高舒适度必须以增加能耗为代价
        - 降低能耗必须以牺牲舒适度为代价
    这就是帕累托前沿（Pareto front）。

做法：
    固定"有人开、无人关"的控制结构，只改变空调设定温度，
    观察能耗与舒适度如何随之变化。

运行方式：
    python scripts\sweep_setpoint.py
"""

import sys
from pathlib import Path
from dataclasses import replace

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

from src.env import SmartHomeEnv, RewardConfig, encode_action

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


def run_rule_with_setpoint(ac_setpoint_c: float, seed: int = 2026):
    """用"有人开、无人关，固定设定温度"的策略跑一个回合。

    参数 ac_setpoint_c 是目标温度，通过修改舒适区间来实现：
    策略逻辑保持不变（有人就开最低档），但把空调档位映射到目标温度。
    """
    env = SmartHomeEnv(seed=seed)

    # 构造一个自定义档位：把 24°C 档替换为目标温度
    env.act_cfg.ac_setpoints = [ac_setpoint_c, ac_setpoint_c, ac_setpoint_c]
    env.sim.act_cfg = env.act_cfg

    env.reset(seed=seed)
    while True:
        obs = env.get_obs_dict()
        occupied = bool(obs["occ_a"] or obs["occ_b"])
        if occupied:
            ac_idx = 1                      # 有人即开（使用自定义设定温度）
            light_idx = 1 if obs["light_in"] < 300 else 0
        else:
            ac_idx, light_idx = 0, 0
        action = encode_action(ac_idx, light_idx, 0, env.act_cfg)
        obs_v, _, term, trunc, info = env.step(action)
        if term or trunc:
            m = info["episode_metrics"]
            break
    env.close()
    return m


def main():
    print("=" * 70)
    print(" 设定温度扫描 —— 检验舒适度与能耗的权衡关系")
    print("=" * 70)
    print("\n 策略结构固定为：有人开空调 / 无人关闭 / 光照不足开灯")
    print(" 唯一变量：空调设定温度\n")

    setpoints = [22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0]
    rows = []

    header = (f"  {'设定温度':>8}{'日均能耗kWh':>13}{'不达标占比':>12}"
              f"{'偏差RMSE':>11}{'日均启动':>10}")
    print(header)
    print("  " + "-" * 64)

    for sp in setpoints:
        m = run_rule_with_setpoint(sp)
        rows.append((sp, m["energy_per_day_kwh"], m["discomfort_ratio"],
                     m["temp_dev_rmse"], m.get("ac_start_per_day", 0)))
        print(f"  {sp:>7.0f}°C{m['energy_per_day_kwh']:>13.3f}"
              f"{m['discomfort_ratio']:>12.1%}"
              f"{m['temp_dev_rmse']:>11.3f}"
              f"{m.get('ac_start_per_day', 0):>10.1f}")
    print("  " + "-" * 64)

    # ---------- 判断是否存在权衡 ----------
    print("\n" + "=" * 70)
    print(" 权衡关系检验")
    print("=" * 70)

    energies = np.array([r[1] for r in rows])
    discomfort = np.array([r[2] for r in rows])

    # 单调性检查：能耗是否随设定温度升高而下降，不达标是否随之上升
    e_decreasing = all(energies[i] >= energies[i + 1] - 1e-9
                       for i in range(len(energies) - 1))
    d_increasing = all(discomfort[i] <= discomfort[i + 1] + 1e-9
                       for i in range(len(discomfort) - 1))

    print(f"\n  能耗随设定温度升高而下降: {'是' if e_decreasing else '否'}")
    print(f"  不达标率随设定温度升高而上升: {'是' if d_increasing else '否'}")

    # 计算两端差异
    e_span = energies.max() - energies.min()
    d_span = discomfort.max() - discomfort.min()
    print(f"\n  能耗跨度: {energies.min():.3f} ~ {energies.max():.3f} kWh/天 "
          f"(相差 {e_span:.3f})")
    print(f"  不达标跨度: {discomfort.min():.1%} ~ {discomfort.max():.1%} "
          f"(相差 {d_span:.1%})")

    if e_span > 0.05 and d_span > 0.02:
        print(f"\n  [结论] 存在真实的舒适度—能耗权衡关系。")
        print(f"         强化学习有明确的优化空间：在帕累托前沿上",
              f"寻找比固定设定温度更优的工作点。")
    else:
        print(f"\n  [结论] 权衡关系不明显，需检查模型参数。")

    # ---------- 画帕累托图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    ax = axes[0]
    ax.plot(energies, discomfort * 100, "o-", color="#1f77b4",
            markersize=8, linewidth=2)
    for sp, e, d, _, _ in rows:
        ax.annotate(f"{sp:.0f}°C", (e, d * 100), textcoords="offset points",
                    xytext=(6, 6), fontsize=9)
    ax.set_xlabel("日均能耗 (kWh)  —— 越左越好")
    ax.set_ylabel("舒适度不达标占比 (%)  —— 越下越好")
    ax.set_title("舒适度—能耗权衡关系（帕累托图）", fontsize=12)
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    sps = [r[0] for r in rows]
    ax2 = ax.twinx()
    l1, = ax.plot(sps, energies, "o-", color="#d62728", label="日均能耗")
    l2, = ax2.plot(sps, discomfort * 100, "s--", color="#2ca02c",
                   label="不达标占比")
    ax.set_xlabel("空调设定温度 (°C)")
    ax.set_ylabel("日均能耗 (kWh)", color="#d62728")
    ax2.set_ylabel("不达标占比 (%)", color="#2ca02c")
    ax.set_title("设定温度对两个目标的影响", fontsize=12)
    ax.grid(alpha=0.3, linestyle=":")
    ax.legend(handles=[l1, l2], loc="center right", fontsize=9)

    fig.suptitle("设定温度扫描：验证多目标优化问题的存在性", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "setpoint_sweep.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
