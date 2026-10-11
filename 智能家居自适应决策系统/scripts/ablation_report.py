r"""
消融实验对比 —— 验证时间场景化权重机制是否真正有效

背景（对应 MDP 文档第 5.2 节）：
    要证明"时间场景化权重"这一机制有效，只比较"本方法 vs 固定权重"是不够的。
    因为"让权重变化"这件事本身可能带来探索多样性，而与"按时间场景变"无关。
    因此必须加入第三个对照组：

        A1  固定权重        —— 基线：权重恒定
        A2  场景化权重      —— 本方法
        A3  随机变化权重    —— 关键对照：权重随机变，但与时间场景无关

    只有 A2 优于 A1 且优于 A3，才能说明"按时间场景调整权重"确实起了作用。

运行方式：
    # 先分别训练三组（若尚未训练）
    python scripts\train.py --weight-mode scenario --tag ablate_scenario
    python scripts\train.py --weight-mode fixed    --tag ablate_fixed
    python scripts\train.py --weight-mode random   --tag ablate_random

    # 再汇总对比
    python scripts\ablation_report.py

本脚本会：
    1. 读取各组训练结果 json
    2. 输出对比表并给出结论
    3. 生成消融对比图
"""

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

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# 组别定义：(标签, 目录名, 是否为本方法)
GROUPS = [
    ("A1 固定权重", "ablate_fixed", False),
    ("A2 场景化权重（本方法）", "ppo_scenario", True),
    ("A3 随机变化权重（对照）", "ablate_random", False),
]


def load_result(tag: str):
    p = PROJECT_ROOT / "models" / tag / "train_result.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    print("=" * 74)
    print(" 消融实验对比 —— 时间场景化权重机制的有效性验证")
    print("=" * 74)
    print("\n 实验设计（MDP 文档 5.2 节）：")
    print("   A1 固定权重       —— 权重恒定，作为基线")
    print("   A2 场景化权重     —— 本方法（权重随小时/星期/占用变化）")
    print("   A3 随机变化权重   —— 关键对照（权重随机变，与时间场景无关）")
    print("\n 判据：A2 需同时优于 A1 与 A3，才能说明机制有效。")

    data = {}
    for label, tag, is_ours in GROUPS:
        r = load_result(tag)
        if r is None:
            print(f"\n  缺少结果文件：models/{tag}/train_result.json")
            continue
        res = r["results"]["PPO(本方法)"]
        data[label] = {
            "energy": res["energy_per_day_kwh"][0],
            "energy_std": res["energy_per_day_kwh"][1],
            "discomfort": res["discomfort_ratio"][0],
            "discomfort_std": res["discomfort_ratio"][1],
            "rmse": res["temp_dev_rmse"][0],
            "switch": res["switch_per_day"][0],
            "delay": res["response_delay_mean"][0],
            "is_ours": is_ours,
            "n_episodes": len(r.get("results", {}) and
                              r["results"]["PPO(本方法)"]["energy_per_day_kwh"]),
        }

    if len(data) < 3:
        print("\n  组数不足 3 组，无法完成消融对比。")
        print("  请先运行三组训练（见本脚本开头的命令说明）。")
        return 1

    # ---------- 对比表 ----------
    print("\n" + "=" * 74)
    print(" 对比结果（评估回合数：各 5 回合）")
    print("=" * 74)
    hdr = (f"  {'组别':<24}{'日均能耗':>16}{'不达标占比':>16}{'偏差RMSE':>10}"
           f"{'日均切换':>10}")
    print(hdr)
    print("  " + "-" * 72)
    for label in data:
        d = data[label]
        e_str = f"{d['energy']:.3f}±{d['energy_std']:.3f}"
        c_str = f"{d['discomfort']:.1%}±{d['discomfort_std']:.1%}"
        mark = " *" if d["is_ours"] else ""
        print(f"  {label + mark:<24}{e_str:>16}{c_str:>16}"
              f"{d['rmse']:>10.3f}{d['switch']:>10.1f}")
    print("  " + "-" * 72)
    print("  （* 表示本方法；能耗与不达标率以 均值±标准差 表示）")

    # ---------- 结论判断 ----------
    a1 = data["A1 固定权重"]
    a2 = data["A2 场景化权重（本方法）"]
    a3 = data["A3 随机变化权重（对照）"]

    # 判断标准：以"不达标率"为主要舒适度指标（越低越好），
    # 同时看能耗是否未显著变差
    def better_comfort(x, y):
        """舒适度是否显著更优（均值差超过两者标准差之和的一半）。"""
        thresh = 0.5 * (x["discomfort_std"] + y["discomfort_std"])
        return x["discomfort"] < y["discomfort"] - max(thresh, 1e-9)

    def energy_not_worse(x, y, tol=0.05):
        """能耗是否未显著变差（允许 5% 容差）。"""
        return x["energy"] <= y["energy"] * (1 + tol)

    print("\n" + "=" * 74)
    print(" 机制有效性判定")
    print("=" * 74)

    print(f"\n  A2 vs A1（场景化 vs 固定）：")
    print(f"    不达标率 : {a1['discomfort']:.1%} -> {a2['discomfort']:.1%}  "
          f"({a2['discomfort'] - a1['discomfort']:+.1%})")
    print(f"    日均能耗 : {a1['energy']:.3f} -> {a2['energy']:.3f} kWh/天  "
          f"({(a2['energy'] - a1['energy']) / a1['energy']:+.1%})")
    c_better_1 = better_comfort(a2, a1)
    e_ok_1 = energy_not_worse(a2, a1)
    print(f"    舒适度更优: {'是' if c_better_1 else '否'}    "
          f"能耗未变差: {'是' if e_ok_1 else '否'}")

    print(f"\n  A2 vs A3（场景化 vs 随机变化）：")
    print(f"    不达标率 : {a3['discomfort']:.1%} -> {a2['discomfort']:.1%}  "
          f"({a2['discomfort'] - a3['discomfort']:+.1%})")
    print(f"    日均能耗 : {a3['energy']:.3f} -> {a2['energy']:.3f} kWh/天  "
          f"({(a2['energy'] - a3['energy']) / a3['energy']:+.1%})")
    c_better_3 = better_comfort(a2, a3)
    e_ok_3 = energy_not_worse(a2, a3)
    print(f"    舒适度更优: {'是' if c_better_3 else '否'}    "
          f"能耗未变差: {'是' if e_ok_3 else '否'}")

    print("\n  " + "=" * 70)
    if c_better_1 and e_ok_1 and c_better_3 and e_ok_3:
        print("  [结论] 满足判据：A2 在舒适度上显著优于 A1 与 A3，")
        print("         且能耗没有变差。说明'按时间场景调整权重'这一机制有效。")
    elif c_better_1 and c_better_3:
        print("  [结论] A2 在舒适度上优于两个对照，机制有效；")
        print("         但能耗存在一定代价，属于正常的权衡。")
    else:
        print("  [结论] 未完全满足判据。可能原因：")
        print("         · 评估回合数不足（当前各 5 回合），差异可能被随机性掩盖")
        print("         · 训练未收敛，需要增加步数或调整超参数")
        print("         · 当前场景下权重机制的影响本身较小（需要更复杂场景）")
        print("         建议：增加评估回合数至 20+，并用多个随机种子重复实验。")

    print("\n  重要说明：以上结论基于各 5 个评估回合。")
    print("            若要在论文中作为结论，需要多随机种子重复并给出置信区间。")
    print("=" * 74)

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    labels = list(data)
    short = ["A1\n固定", "A2\n场景化\n(本方法)", "A3\n随机变化"]
    colors = ["#1f77b4" if not data[k]["is_ours"] else "#2ca02c"
              for k in labels]

    ax = axes[0]
    vals = [data[k]["energy"] for k in labels]
    errs = [data[k]["energy_std"] for k in labels]
    bars = ax.bar(short, vals, yerr=errs, capsize=5, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.08, f"{v:.3f}",
                ha="center", fontsize=9)
    ax.set_ylabel("日均能耗 (kWh)")
    ax.set_title("能耗对比", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[1]
    vals = [data[k]["discomfort"] * 100 for k in labels]
    errs = [data[k]["discomfort_std"] * 100 for k in labels]
    bars = ax.bar(short, vals, yerr=errs, capsize=5, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.3, f"{v:.1f}%",
                ha="center", fontsize=9)
    ax.set_ylabel("舒适度不达标占比 (%)")
    ax.set_title("舒适度对比（越短越好）", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    ax = axes[2]
    for k, c in zip(labels, colors):
        d = data[k]
        ax.errorbar(d["energy"], d["discomfort"] * 100,
                    xerr=d["energy_std"], yerr=d["discomfort_std"] * 100,
                    fmt="o", color=c, markersize=9, capsize=4)
        ax.annotate(k, (d["energy"], d["discomfort"] * 100),
                    textcoords="offset points", xytext=(8, 8), fontsize=9)
    ax.set_xlabel("日均能耗 (kWh)  —— 越左越好")
    ax.set_ylabel("不达标占比 (%)  —— 越下越好")
    ax.set_title("消融实验：帕累托位置", fontsize=12)
    ax.grid(alpha=0.3, linestyle=":")

    fig.suptitle("消融实验：时间场景化权重机制的有效性验证", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "ablation_compare.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  消融对比图已保存: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
