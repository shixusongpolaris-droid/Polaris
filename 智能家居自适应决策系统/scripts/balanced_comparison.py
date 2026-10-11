r"""
配平比较 —— 让规则与强化学习在相同的奖励标准下对决

动机（承接 reward_rebalance 的诊断）：
    量化诊断发现奖励函数存在严重的量纲失衡：
        超出舒适带 1°C 的代价 = 1.0 × temp_scale = 3.0
        峰段一步的能耗代价   ≈ 0.468
      => 舒适度代价是能耗代价的 **6 倍**
    这使强化学习**在结构上被禁止**采用规则那种"让温度飘高以省电"的做法。

    但此前所有比较都有一个隐含的不对称：
      · 强化学习在"舒适度被重罚"的奖励下训练
      · 规则是人工硬编码的，**不受任何奖励函数约束**
    因此"规则更省电"部分来自于它不受惩罚约束，而非策略更优。

本脚本的做法：
    给规则也定义一个奖励函数（同样的舒适度项与能耗项），
    然后**用同一套权重标准**比较两者：
      · 规则：在它的策略族（不同峰段设定温度）中，选奖励最高者
      · RL  ：在其训练配置（不同 temp_scale）中，选奖励最高者
    若两者在"同等奖励口径"下 RL 的实物指标（电费、不达标率）不差，
    则说明此前的差距主要来自评价不对称，而非策略质量。

运行方式：
    python scripts\balanced_comparison.py
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
OUT_DIR = PROJECT_ROOT / "models" / "balanced"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 与仿真一致的舒适度区间（有人、非睡眠时段）
T_LOW, T_HIGH = 22.0, 26.0
ALPHA = 1.0


def load_traces():
    """从已保存结果里取出各策略的 (不达标率, 电费, 能耗, 标签)。"""
    rule, rl = [], []

    p = PROJECT_ROOT / "models" / "reward_rebalance" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for c in d.get("rule_curve", []):
            m = c.get("metrics", {})
            rule.append({"d": c["discomfort"], "cost": c["cost"],
                         "energy": m.get("energy"),
                         "label": f"规则/峰{c['peak_target']:.0f}°C"})
        for r in d.get("ppo_results", []):
            m = r["metrics"]
            rl.append({"d": m["discomfort"], "cost": m["cost"],
                       "energy": m.get("energy"),
                       "label": f"PPO/ts={r['temp_scale']}"})

    p = PROJECT_ROOT / "models" / "tou_equal_comfort" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for item in d.get("rule_points", []):
            if isinstance(item, list) and len(item) == 2:
                pk, m = item
                if isinstance(m, dict) and "cost" in m:
                    rule.append({"d": m["discomfort"]["mean"],
                                 "cost": m["cost"]["mean"],
                                 "energy": m.get("energy", {}).get("mean"),
                                 "label": f"规则/峰{pk:.0f}°C"})
        for lab, pts in (d.get("algo_results") or {}).items():
            for ts, m in pts:
                try:
                    rl.append({"d": m["discomfort"]["mean"],
                               "cost": m["cost"]["mean"],
                               "energy": m.get("energy", {}).get("mean"),
                               "label": f"{lab}/ts={ts}"})
                except (KeyError, TypeError):
                    continue
    return rule, rl


def implied_reward(d, cost, energy, temp_scale, energy_scale=10.0,
                   price_avg=0.60):
    """按给定权重计算策略隐含的"平均每步奖励"（越大越好）。

    用与仿真同构的形式近似：
        cost_step = temp_scale × (平均偏离量)
        energy_step = energy_scale × price_avg × (每步能耗)
    其中平均偏离量用不达标率与典型偏离幅度近似。

    说明：这只是量级近似，用于在同一口径下排序各策略，
    不追求与仿真奖励数值完全一致。
    """
    if energy is None:
        return None
    steps_per_day = 96.0                     # 15 分钟步长
    e_step = energy / steps_per_day
    # 近似：不达标时平均偏离约 1.5°C（实测典型值）
    avg_dev = d * 1.5
    c_comfort = temp_scale * avg_dev
    c_energy = energy_scale * price_avg * e_step
    return -(c_comfort + c_energy)


def main():
    print("=" * 90)
    print(" 配平比较 —— 让规则与强化学习在相同奖励标准下对决")
    print("=" * 90)
    print()
    print("  问题：此前比较存在隐含的不对称——")
    print("    · 强化学习在'舒适度被重罚'的奖励下训练（temp_scale=3.0）")
    print("    · 规则是硬编码的，不受任何奖励约束")
    print("    因此'规则更省电'可能部分来自它不受惩罚约束。")
    print()
    print("  本分析：给规则也套上用同一套权重的奖励，")
    print("          在相同口径下比较两者的实物指标。")

    rule, rl = load_traces()
    print(f"\n  加载到：规则 {len(rule)} 个配置，RL {len(rl)} 个配置")
    if not rule or not rl:
        print("\n  [错误] 数据不足，请先运行 reward_rebalance.py")
        return 1

    print("\n" + "-" * 90)
    print(" 按'同一套奖励权重'给两边打分（权重越严，越倾向舒适）")
    print("-" * 90)

    print(f"\n  {'权重方案':<22}{'规则最优配置':<20}{'规则奖励':>10}"
          f"{'RL最优配置':<20}{'RL奖励':>10}{'胜者':>8}")
    print("  " + "-" * 86)

    results = []
    for ts in [0.2, 0.5, 1.0, 2.0, 3.0, 5.0]:
        # 规则用同一 temp_scale 打分
        r_scores = [(implied_reward(r["d"], r["cost"], r["energy"], ts), r)
                    for r in rule if r.get("energy") is not None]
        p_scores = [(implied_reward(r["d"], r["cost"], r["energy"], ts), r)
                    for r in rl if r.get("energy") is not None]
        if not r_scores or not p_scores:
            continue
        r_best_score, r_best = max(r_scores, key=lambda x: x[0])
        p_best_score, p_best = max(p_scores, key=lambda x: x[0])
        winner = "规则" if r_best_score > p_best_score else "RL"

        results.append({
            "temp_scale": ts,
            "rule_best": {**r_best, "score": r_best_score},
            "rl_best": {**p_best, "score": p_best_score},
            "winner": winner,
        })

        print(f"  {'temp_scale=' + str(ts):<22}"
              f"{r_best['label']:<20}{r_best_score:>10.4f}"
              f"{p_best['label']:<20}{p_best_score:>10.4f}{winner:>8}")

    # ---- 实物指标对比（这才是关键）----
    print("\n" + "=" * 90)
    print(" 关键：在'各自最优'配置下的实物指标对比")
    print("=" * 90)
    print()
    print("  说明：奖励是代理指标，最终要看电费与舒适度这两个实物指标。")
    print()
    print(f"  {'temp_scale':>11}{'规则电费':>10}{'规则不达':>10}"
          f"{'RL电费':>10}{'RL不达':>9}{'电费差':>10}{'舒适差':>10}")
    print("  " + "-" * 74)

    better_comfort = 0
    for r in results:
        rb, pb = r["rule_best"], r["rl_best"]
        dc = (pb["cost"] - rb["cost"]) / rb["cost"]
        dd = pb["d"] - rb["d"]
        if dd < 0:
            better_comfort += 1
        print(f"  {r['temp_scale']:>11.1f}{rb['cost']:>10.3f}{rb['d']:>10.2%}"
              f"{pb['cost']:>10.3f}{pb['d']:>9.2%}"
              f"{dc:>+10.1%}{dd:>+10.2%}")

    print("  " + "-" * 74)
    print(f"\n  RL 在舒适度上更优的次数: {better_comfort}/{len(results)}")
    print(f"  RL 在电费上更优的次数:   "
          f"{sum(1 for r in results if r['rl_best']['cost'] < r['rule_best']['cost'])}"
          f"/{len(results)}")

    # ---- 结论 ----
    print("\n" + "=" * 90)
    print(" 结论")
    print("=" * 90)

    all_rule = all(r["winner"] == "规则" for r in results)
    print()
    if all_rule:
        print("  [结论] 在所有配平方案下，规则的隐含奖励都更高。")
        print("         说明'规则更省电'并非仅仅来自评价不对称——")
        print("         即使在同等奖励口径下，规则仍是更优的策略。")
        print()
        print("         这进一步支持：**在分时电价场景下，嵌入电价知识的")
        print("         简单规则难以被当前强化学习方案超越。**")
    else:
        print("  [结论] 配平后强化学习在部分方案下胜出，")
        print("         说明此前的差距部分来自评价口径不对称。")

    # 关键洞察
    print("\n" + "-" * 90)
    print(" 一个稳健的正面发现")
    print("-" * 90)
    rl_d_min = min(p["d"] for p in rl)
    rule_d_min = min(r["d"] for r in rule)
    print(f"\n  规则可达的最低不达标率 : {rule_d_min:.2%}")
    print(f"  RL 可达的最低不达标率   : {rl_d_min:.2%}")
    print(f"\n  即：**规则的舒适度能力存在硬上限（约 {rule_d_min:.0%}），")
    print(f"      而强化学习可以做到 {rl_d_min:.1%}。**")
    print(f"  这是强化学习在该场景下的可靠优势——它能在规则无法达到的")
    print(f"  舒适度水平上工作，代价是电费更高。")

    # ---- 画图 ----
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))

    ax = axes[0]
    ax.scatter([r["d"] * 100 for r in rule], [r["cost"] for r in rule],
               s=70, color="#1f77b4", marker="s", label="规则控制", zorder=3)
    ax.scatter([r["d"] * 100 for r in rl], [r["cost"] for r in rl],
               s=70, color="#d62728", marker="o", label="强化学习", zorder=3)
    ax.axvspan(rule_d_min * 100,
               min(r["d"] for r in rl) * 100, alpha=0.1, color="orange",
               label="规则做不到的舒适度区间")
    ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
    ax.set_ylabel("日均电费 (元)")
    ax.set_title("能力区间对比：规则存在舒适度硬上限", fontsize=12)
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(alpha=0.3, linestyle=":")

    ax = axes[1]
    ts_list = [r["temp_scale"] for r in results]
    x = np.arange(len(ts_list))
    w = 0.35
    ax.bar(x - w / 2, [r["rule_best"]["score"] for r in results], w,
           label="规则（最优配置）", color="#1f77b4")
    ax.bar(x + w / 2, [r["rl_best"]["score"] for r in results], w,
           label="强化学习（最优配置）", color="#d62728")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{t:.1f}" for t in ts_list])
    ax.set_xlabel("temp_scale（舒适度权重，越大越严）")
    ax.set_ylabel("隐含平均每步奖励（越大越好）")
    ax.set_title("同一奖励口径下各自最优配置的奖励对比", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    fig.suptitle("配平比较：给规则也套上奖励函数后的公平对决", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "balanced_comparison.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"results": results,
                   "rule_d_min": rule_d_min, "rl_d_min": rl_d_min},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/balanced/results.json")
    print("=" * 90)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
