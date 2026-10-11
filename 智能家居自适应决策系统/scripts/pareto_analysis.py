r"""
帕累托前沿分析 —— 汇总所有实验数据，公平比较"规则 vs 强化学习"

背景：
    v3 系列实验产生了大量"电费—舒适度"数据点（不同 temp_scale、
    不同参数配置、不同算法），但它们分散在各脚本的输出里，
    且**口径不一致**导致无法直接比较：

      · 规则的不达标率集中在 33~35%（单一机制，区间窄）
      · PPO 的不达标率随 temp_scale 变化，落点分散且不稳定
        （实测 0.10→43%、0.15→38%、0.20→29%、0.25→24%）
      · 因此"等舒适度逐点比较"经常因落点对不上而失效

    正确做法是**帕累托前沿分析**：不需要逐点匹配舒适度，
    而是分别构造两条前沿曲线，比较**在每一段成本上谁的不达标率更低**
    （或等价地：每一段不达标率上谁的成本更低）。

本脚本：
    1. 汇总所有已保存的实验结果（多处 results.json）
    2. 对规则与 PPO 分别计算帕累托前沿（成本越低越好、不达标率越低越好）
    3. 比较两条前沿：找出规则占优区间、PPO 占优区间、以及交叉点
    4. 输出对比图

运行方式：
    python scripts\pareto_analysis.py
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
OUT_DIR = PROJECT_ROOT / "models" / "pareto"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 收集数据点：(不达标率, 电费, 来源标签, 场景)
# ============================================================
# 重要：必须区分场景！
#   "无分时电价"与"有分时电价"的电价结构不同，电费不可跨场景比较。
#   最初的实现把两个场景的数据混在一起，导致帕累托前沿无效
#   （例如规则在无 TOU 场景的 4.30 元/天被当成有 TOU 场景的数据点）。
#   这里为每个数据点标注场景，分析时分开处理。
# ============================================================
SCEN_TOU = "有分时电价"
SCEN_NOTOU = "无分时电价"


def collect_points():
    """返回 (rule_points, ppo_points)，每项 (discomfort, cost, label, scen)。"""
    rule_points, ppo_points = [], []

    # ---- 来源 1：reward_rebalance（本次权重重标定，全部为 TOU 场景）----
    p = PROJECT_ROOT / "models" / "reward_rebalance" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for c in d.get("rule_curve", []):
            rule_points.append((c["discomfort"], c["cost"],
                                f"规则/峰{c['peak_target']:.0f}°C", SCEN_TOU))
        for r in d.get("ppo_results", []):
            m = r["metrics"]
            ppo_points.append((m["discomfort"], m["cost"],
                               f"PPO/ts={r['temp_scale']}", SCEN_TOU))

    # ---- 来源 2：tou_equal_comfort（v3 等舒适度实验，TOU 场景）----
    p = PROJECT_ROOT / "models" / "tou_equal_comfort" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for item in d.get("rule_points", []):
            if isinstance(item, list) and len(item) == 2:
                pk, m = item
                if isinstance(m, dict) and "cost" in m:
                    rule_points.append((m["discomfort"]["mean"],
                                        m["cost"]["mean"],
                                        f"规则2/峰{pk:.0f}°C", SCEN_TOU))
        for lab, pts in (d.get("algo_results") or {}).items():
            for ts, m in pts:
                try:
                    ppo_points.append((m["discomfort"]["mean"],
                                       m["cost"]["mean"],
                                       f"{lab}/ts={ts}", SCEN_TOU))
                except (KeyError, TypeError):
                    continue

    # ---- 来源 3：recurrent（循环网络实验，TOU 场景）----
    p = PROJECT_ROOT / "models" / "recurrent" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for lab, m in (d.get("results") or {}).items():
            try:
                disc = m["discomfort_ratio"]["mean"]
                cost = m["energy_cost_yuan"]["mean"]
                if "规则" in lab:
                    rule_points.append((disc, cost, lab, SCEN_TOU))
                elif "PPO" in lab:
                    ppo_points.append((disc, cost, lab, SCEN_TOU))
            except (KeyError, TypeError):
                continue

    # ---- 来源 4：param_sweep（参数扫描，分场景标注）----
    p = PROJECT_ROOT / "models" / "param_sweep" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        for scen, rows in (d.get("results") or {}).items():
            # 场景名："无分时电价" / "有分时电价"
            if "无" in scen:
                sc = SCEN_NOTOU
            elif "有" in scen:
                sc = SCEN_TOU
            else:
                continue
            # 注意：param_sweep 的 check_ignore/规则是"有人维持25°C"的规则，
            # 与 TOU 规则不同源，但同属规则类基线，场景一致即可比较
            for r in rows:
                try:
                    ppo_points.append((r["ppo"]["discomfort_ratio"],
                                       r["ppo"]["cost"],
                                       f"扫描/{scen}", sc))
                    rule_points.append((r["rule"]["discomfort_ratio"],
                                        r["rule"]["cost"],
                                        f"规则扫描/{scen}", sc))
                except (KeyError, TypeError):
                    continue

    return rule_points, ppo_points


# ============================================================
# 帕累托前沿
# ============================================================
def pareto_front(points):
    """返回帕累托前沿（不达标率越低越好、成本越低越好）。

    输入 [(discomfort, cost, label), ...]
    输出前沿点列表，按 discomfort 升序
    """
    pts = sorted(points, key=lambda x: (x[0], x[1]))
    front = []
    best_cost = float("inf")
    for d, c, lab in pts:
        if c < best_cost - 1e-9:
            front.append((d, c, lab))
            best_cost = c
    return front


def interp_front(front, target_d):
    """在前沿曲线上按不达标率插值成本。"""
    xs = [f[0] for f in front]
    ys = [f[1] for f in front]
    if not xs:
        return None
    if target_d < xs[0] or target_d > xs[-1]:
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
    print("=" * 88)
    print(" 帕累托前沿分析 —— 公平比较规则与强化学习（按场景分开）")
    print("=" * 88)
    print()
    print("  为什么用帕累托分析：")
    print("    '等舒适度逐点比较'要求两条曲线的舒适度落点能够对应，")
    print("    但实测中规则固定在 33~35%，PPO 随权重变化落点分散，")
    print("    经常出现无法匹配的断档。帕累托前沿不需要逐点匹配，")
    print("    而是比较整条曲线，是更稳健的口径。")
    print()
    print("  重要：必须按场景分开比较——无分时电价与有分时电价的")
    print("        电价结构不同，电费数值不可跨场景直接比较。")

    rule_all, ppo_all = collect_points()
    print(f"\n  收集到的数据点：规则 {len(rule_all)} 个，"
          f"RL {len(ppo_all)} 个")

    # 按场景分组
    scenarios = {}
    for pts, bucket in [(rule_all, "rule"), (ppo_all, "ppo")]:
        for d, c, lab, sc in pts:
            scenarios.setdefault(sc, {"rule": [], "ppo": []})
            scenarios[sc][bucket].append((d, c, lab))

    if not scenarios:
        print("\n  [错误] 数据不足，请先运行相关实验脚本。")
        return 1

    summary = {}
    fig, axes = plt.subplots(1, len(scenarios), figsize=(8.5 * len(scenarios), 6.5))
    if len(scenarios) == 1:
        axes = [axes]

    for ax, (scen, data) in zip(axes, sorted(scenarios.items())):
        print("\n" + "=" * 88)
        print(f" 场景：{scen}")
        print("=" * 88)

        if not data["rule"] or not data["ppo"]:
            print(f"  数据不足（规则 {len(data['rule'])} 个，"
                  f"RL {len(data['ppo'])} 个），跳过")
            continue

        rule_front = pareto_front(data["rule"])
        ppo_front = pareto_front(data["ppo"])

        print("\n  规则控制前沿：")
        print(f"    {'不达标率':>10}{'电费元/天':>12}   来源")
        for d, c, lab in rule_front:
            print(f"    {d:>10.2%}{c:>12.3f}   {lab}")
        print("\n  强化学习前沿：")
        print(f"    {'不达标率':>10}{'电费元/天':>12}   来源")
        for d, c, lab in ppo_front:
            print(f"    {d:>10.2%}{c:>12.3f}   {lab}")

        lo = max(rule_front[0][0], ppo_front[0][0])
        hi = min(rule_front[-1][0], ppo_front[-1][0])

        res = {"rule_front": rule_front, "ppo_front": ppo_front,
               "overlap": None, "rows": [], "cross": None}

        if lo >= hi:
            print(f"\n  两条前沿的不达标率区间不重叠：")
            print(f"    规则 {rule_front[0][0]:.1%} ~ {rule_front[-1][0]:.1%}")
            print(f"    RL   {ppo_front[0][0]:.1%} ~ {ppo_front[-1][0]:.1%}")
        else:
            print(f"\n  重叠区间: {lo:.1%} ~ {hi:.1%}")
            print(f"\n    {'不达标率':>10}{'规则电费':>11}{'RL电费':>11}"
                  f"{'差异':>10}{'占优方':>10}")
            print("    " + "-" * 54)
            wins_ppo = wins_rule = 0
            rows = []
            for d in np.linspace(lo, hi, 9):
                rc = interp_front(rule_front, d)
                pc = interp_front(ppo_front, d)
                if rc is None or pc is None:
                    continue
                diff = (pc - rc) / rc
                who = "RL 更优" if diff < 0 else "规则更优"
                if diff < 0:
                    wins_ppo += 1
                else:
                    wins_rule += 1
                rows.append((float(d), rc, pc, float(diff), who))
                print(f"    {d:>10.2%}{rc:>11.3f}{pc:>11.3f}"
                      f"{diff:>+10.1%}{who:>10}")
            print("    " + "-" * 54)

            cross = None
            for i in range(len(rows) - 1):
                if (rows[i][3] < 0) != (rows[i + 1][3] < 0):
                    cross = (rows[i][0] + rows[i + 1][0]) / 2
                    break
            res.update({"overlap": [float(lo), float(hi)], "rows": rows,
                        "cross": cross, "wins_ppo": wins_ppo,
                        "wins_rule": wins_rule})

            if cross is not None:
                first_is_ppo = rows[0][3] < 0
                print(f"\n  **两条前沿在 不达标率 ≈ {cross:.1%} 处交叉**")
                print(f"    低于该水平：{'RL 更省' if first_is_ppo else '规则更省'}")
                print(f"    高于该水平：{'规则更省' if first_is_ppo else 'RL 更省'}")
            else:
                print(f"\n  [结论] 重叠区间内 "
                      f"{'RL 整体更优' if wins_ppo > wins_rule else '规则整体更优'}")

        summary[scen] = res

        # 画图
        rx = [f[0] * 100 for f in rule_front]
        ry = [f[1] for f in rule_front]
        px = [f[0] * 100 for f in ppo_front]
        py = [f[1] for f in ppo_front]
        ax.plot(rx, ry, "s-", color="#1f77b4", markersize=9, linewidth=2,
                label="规则控制（帕累托前沿）")
        ax.plot(px, py, "o--", color="#d62728", markersize=9, linewidth=2,
                label="强化学习（帕累托前沿）")
        ax.scatter([p[0] * 100 for p in data["rule"]],
                   [p[1] for p in data["rule"]], s=16, alpha=0.22,
                   color="#1f77b4")
        ax.scatter([p[0] * 100 for p in data["ppo"]],
                   [p[1] for p in data["ppo"]], s=16, alpha=0.22,
                   color="#d62728")
        if lo < hi:
            ax.axvspan(lo * 100, hi * 100, alpha=0.08, color="green",
                       label=f"可比区间 {lo:.0%}~{hi:.0%}")
        if res.get("cross"):
            ax.axvline(res["cross"] * 100, color="black", linestyle=":",
                       linewidth=1.5,
                       label=f"交叉点 {res['cross']:.0%}")
        ax.set_xlabel("舒适度不达标占比 (%)  —— 越左越好")
        ax.set_ylabel("日均电费 (元)  —— 越下越好")
        ax.set_title(f"{scen}：帕累托前沿比较", fontsize=12)
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3, linestyle=":")

    fig.suptitle("规则控制 vs 强化学习：帕累托前沿比较（按场景分开）",
                 fontsize=14)
    fig.tight_layout()
    out = FIG_DIR / "pareto_analysis.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/pareto/results.json")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
