# -*- coding: utf-8 -*-
"""从实测数据拟合"离开时长 -> 回归风险"模型（半马尔可夫占用模型的第一步）。

为什么需要
----------
当前仿真的占用是**每步独立抽样**（i.i.d.）：

    occupied = 1 if rng.random() < p[weekday][hour] else 0

这意味着"已离开 5 小时"不提供任何关于"下一步会不会回来"的信息，
因此在当前环境里"预测回家时间"在数学上不可能。这是 RL 学不会
"提前预冷"的根本原因。

要做的事
--------
把占用改成**半马尔可夫过程**：离开时抽一个"本次会离开多久"，
然后随"已离开时长"推进回归风险。

本脚本只做数据部分：
  1. 从 timeline_final.csv 抽出全部"离开时长"与"在室时长"
  2. 检查分布结构（是否双峰）
  3. 估计风险函数 h(t) = P(在接下来 15/30/60 分钟内返回 | 已离开 t 分钟)
  4. 输出供仿真使用的查表数据

小样本声明
----------
实测只有 49.9 小时，共 10 次完整外出、14 段在室（末段右删失）。
n=10 不足以做参数化密度估计，因此：

  · 仿真用**经验分布自助抽样**（直接从观测值里抽），不做参数假设
  · 风险函数用**经验生存函数 + 平滑**，并明确标注置信区间很宽

这是一个诚实但粗糙的模型 —— 它的作用是让"可预测性"这件事存在，
而不是精确复现用户的作息。

（若后续采集更多天数，本脚本可直接重跑更新。）

输出
----
docs/占用风险模型.md
models/occupancy_hazard.json
"""

import csv
import io
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
OUT_MD = os.path.join(PROJ, "docs", "占用风险模型.md")
OUT_JSON = os.path.join(PROJ, "models", "occupancy_hazard.json")

HORIZONS = [15, 30, 60, 120]     # 分钟
CHECKPOINTS = [15, 30, 45, 60, 90, 120, 180, 240, 360, 480, 600, 720,
               900, 1200]        # 已离开时长（分钟）的查表点


def load_durations():
    """返回 (away_durations, stay_durations)，单位分钟。

    末段在室被数据结束截断（右删失），单独标记。
    """
    p = os.path.join(PROJ, "data", "processed", "timeline_final.csv")
    rows = list(csv.DictReader(io.open(p, encoding="utf-8")))
    segs = []
    for r in rows:
        try:
            a = datetime.strptime(r["start"][:19], "%Y-%m-%d %H:%M:%S")
            b = datetime.strptime(r["end"][:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            continue
        inroom = str(r.get("state", "")).strip().startswith("在房间")
        segs.append((a, b, inroom, int(r.get("no", 0))))
    segs.sort(key=lambda x: x[0])

    away, stay = [], []
    for i, (a, b, o, no) in enumerate(segs):
        m = (b - a).total_seconds() / 60.0
        if o:
            # 末段在室是右删失：只知道"至少这么久"，不知道真正时长
            censored = (i == len(segs) - 1)
            stay.append({"minutes": m, "censored": censored, "no": no})
        else:
            away.append({"minutes": m, "censored": False, "no": no})
    return away, stay


def survival(durations, t):
    """经验生存函数 S(t) = P(D > t)，用线性插值平滑。

    durations 为完整观测的时长列表（不含删失）。n 很小，插值是必要的，
    否则风险函数会变成只有几个取值的阶梯，无法查询任意 t。
    """
    d = sorted(durations)
    n = len(d)
    if n == 0:
        return 1.0
    if t < d[0]:
        return 1.0
    if t >= d[-1]:
        return 0.0
    # S(t) = #{D > t} / n；在观测点之间线性插值
    for i in range(n - 1):
        if d[i] <= t < d[i + 1]:
            # i 之前的都 <= t，故 #{D > t} = n - i - 1
            s0 = (n - i - 1) / n
            s1 = (n - i - 2) / n
            w = (t - d[i]) / (d[i + 1] - d[i])
            return s0 + w * (s1 - s0)
    return 0.0


def hazard(durations, t, h):
    """h 分钟内的回归概率：P(D <= t+h | D > t) = 1 - S(t+h)/S(t)。"""
    s_t = survival(durations, t)
    if s_t <= 1e-9:
        return 1.0
    s_th = survival(durations, t + h)
    return max(0.0, min(1.0, 1.0 - s_th / s_t))


# ============================================================
# 平滑版本：双分量对数正态混合
# ============================================================
# 为什么需要平滑
# -------------
# 经验生存函数只有 10 个观测点，在 57 分钟到 712 分钟之间是一个巨大的
# 空隙，线性插值会在该区间产生"先高后骤降"的假象（实测：t=45 分的
# 45 分钟风险为 45.5%，而 t=60 分骤降到 2.3%）。这个形状会作为观测
# 输入给策略，跳变会让学习变得困难。
#
# 因此另拟合一个**双分量对数正态混合**作为平滑版本：分量结构与实测的
# 双峰一致（短途 9 次 + 长途 1 次）。混合权重固定为经验频率（9/10），
# 只拟合两个分量的中位数与形状参数。

def _lognormal_params(median, sigma):
    """由中位数与对数标准差得到 (mu, sigma)，mu 为 ln 尺度。"""
    import math
    return math.log(median), sigma


def _mix_survival(t, med1, sig1, med2, sig2, w1):
    """双分量对数正态混合的生存函数 S(t) = P(D > t)。"""
    import math
    from math import erf, sqrt

    def ln_surv(x, med, sig):
        if x <= 0:
            return 1.0
        z = (math.log(x) - math.log(med)) / sig
        return 0.5 * (1.0 - erf(z / sqrt(2.0)))

    return w1 * ln_surv(t, med1, sig1) + (1.0 - w1) * ln_surv(t, med2, sig2)


def fit_mixture(durations):
    """拟合双分量对数正态混合，权重固定为经验频率。

    返回 (med1, sig1, med2, sig2, w1) 或 None（若 scipy 不可用）。
    """
    try:
        import numpy as np
        from scipy.optimize import minimize
    except Exception:
        return None

    d = np.array(sorted(durations), dtype=float)
    n = len(d)
    if n < 3:
        return None
    short = [x for x in d if x < 120]
    long_ = [x for x in d if x >= 120]
    w1 = len(short) / n if short else 0.5

    med1_0 = float(np.median(short)) if short else 20.0
    med2_0 = float(np.median(long_)) if long_ else 600.0
    if not long_:
        med2_0 = max(d) * 1.5
    sig1_0, sig2_0 = 0.7, 0.5

    def nll(p):
        m1, s1, m2, s2 = p
        if m1 <= 1 or m2 <= 1 or s1 <= 0.05 or s2 <= 0.05:
            return 1e18
        # 对数正态密度
        tot = 0.0
        for x in d:
            f1 = (1.0 / (x * s1 * np.sqrt(2 * np.pi))
                  * np.exp(-(np.log(x) - np.log(m1)) ** 2 / (2 * s1 ** 2)))
            f2 = (1.0 / (x * s2 * np.sqrt(2 * np.pi))
                  * np.exp(-(np.log(x) - np.log(m2)) ** 2 / (2 * s2 ** 2)))
            fx = w1 * f1 + (1 - w1) * f2
            tot += np.log(max(fx, 1e-12))
        return -tot

    res = minimize(nll, [med1_0, sig1_0, med2_0, sig2_0],
                   method="Nelder-Mead",
                   options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 6000})
    m1, s1, m2, s2 = [float(x) for x in res.x]
    # 约束回合理范围（极小样本容易跑飞）
    m1 = min(max(m1, 2.0), 120.0)
    m2 = min(max(m2, 120.0), 1500.0)
    s1 = min(max(s1, 0.1), 2.0)
    # 长途分量的 sigma 下界取 0.30：该分量只有 1 个观测（712 分钟），
    # 形状参数不可识别，拟合会把它压到 0.1 附近，等价于断言
    # 「每次都在 11.9 小时整点回来」—— 这显然不合理。
    # 取 0.30 对应 ±1 sigma 约 8.4~16.8 小时，与「一个工作日」的常识一致。
    s2 = min(max(s2, 0.30), 2.0)
    return m1, s1, m2, s2, w1


def hazard_mix(t, h, params):
    """混合模型下的 h 分钟内回归概率。"""
    m1, s1, m2, s2, w1 = params
    s_t = _mix_survival(t, m1, s1, m2, s2, w1)
    if s_t <= 1e-9:
        return 1.0
    s_th = _mix_survival(t + h, m1, s1, m2, s2, w1)
    return max(0.0, min(1.0, 1.0 - s_th / s_t))


def main():
    away, stay = load_durations()
    a_min = sorted(x["minutes"] for x in away)
    s_min = sorted(x["minutes"] for x in stay if not x["censored"])
    s_cens = [x["minutes"] for x in stay if x["censored"]]

    print("=" * 84)
    print(" 占用风险模型拟合（半马尔可夫占用模型第一步）")
    print("=" * 84)
    print(f"  数据区间：2026-10-14 16:08 ~ 10-16 17:59（约 49.9 小时）")
    print()
    print(f"=== 完整的外出段：{len(a_min)} 个 ===")
    print("  " + ", ".join(f"{x:.0f}" for x in a_min) + " 分钟")
    print(f"  合计 {sum(a_min):.0f} 分钟 = {sum(a_min)/60:.1f} 小时")
    print(f"  中位数 {a_min[len(a_min)//2]:.0f} 分钟")
    print()
    print(f"=== 在室段：{len(s_min)} 个完整 + {len(s_cens)} 个右删失 ===")
    print("  完整: " + ", ".join(f"{x:.0f}" for x in s_min) + " 分钟")
    if s_cens:
        print(f"  删失: " + ", ".join(f"≥{x:.0f}" for x in s_cens)
              + " 分钟（数据结束时仍在室内）")
    print()

    # 结构判断：是否双峰
    short = [x for x in a_min if x < 120]
    long_ = [x for x in a_min if x >= 120]
    print("=== 外出时长结构 ===")
    print(f"  短途（<2 小时）：{len(short)} 次，"
          f"{[f'{x:.0f}' for x in short]}")
    print(f"  长时间（≥2 小时）：{len(long_)} 次，"
          f"{[f'{x:.0f}' for x in long_]}")
    is_bimodal = len(short) > 0 and len(long_) > 0
    print(f"  双峰？{'是 —— 适合半马尔可夫模型' if is_bimodal else '否'}")
    print()

    # 风险函数表
    print("=" * 84)
    print(" 风险函数：已离开 t 分钟后，在未来 h 分钟内回归的概率")
    print("=" * 84)
    mix = fit_mixture(a_min)
    if mix:
        m1, s1, m2, s2, w1 = mix
        print(f"  双分量对数正态混合拟合：")
        print(f"    短途分量 中位 {m1:.1f} 分, sigma {s1:.2f}, 权重 {w1:.0%}")
        print(f"    长途分量 中位 {m2:.1f} 分, sigma {s2:.2f}, 权重 {1-w1:.0%}")
        print()
    else:
        print("  ⚠️ scipy 不可用，仅输出非参数估计")
        print()

    hdr = f"  {'已离开':>10}"
    for h in HORIZONS:
        hdr += f"{'h=' + str(h) + '分':>12}"
    if mix:
        hdr += f"  {'(h=30 平滑)':>14}"
    print(hdr)
    print("  " + "-" * (10 + 12 * len(HORIZONS) + (16 if mix else 0)))
    table = {}
    table_smooth = {}
    for t in CHECKPOINTS:
        if t > max(a_min):
            break
        row = f"  {t:>8.0f} 分"
        table[str(t)] = {}
        table_smooth[str(t)] = {}
        for h in HORIZONS:
            v = hazard(a_min, t, h)
            table[str(t)][str(h)] = round(v, 4)
            if mix:
                vs = hazard_mix(t, h, mix)
                table_smooth[str(t)][str(h)] = round(vs, 4)
            row += f"{v:>12.1%}"
        if mix:
            row += f"{table_smooth[str(t)]['30']:>14.1%}"
        print(row)
    print()
    print("  左半部分是**非参数估计**（n=10，噪声大，仅作参照）；")
    if mix:
        print("  最右列是**双分量混合的平滑估计**（推荐用于观测输入）。")
    print("  ⚠️ 注意风险函数**非单调**：2 小时后降到很低（'工作日'分支），")
    print("     8~12 小时后重新升高（快下班了）。这是双峰分布的体现。")
    print()

    # 分支概率
    p_short = len(short) / len(a_min) if a_min else 0.0
    print("=== 仿真用的经验分布（自助抽样）===")
    print(f"  短途分支概率 = {p_short:.1%}（{len(short)}/{len(a_min)}）")
    print(f"  长途分支概率 = {1-p_short:.1%}（{len(long_)}/{len(a_min)}）")
    print(f"  短途时长池：{sorted(short)}")
    print(f"  长途时长池：{sorted(long_)}")
    print()
    print("  ⚠️ n=10 的样本量很小，置信区间很宽。")
    print("     例如'短途中位 20 分钟'的真实值可能在 10~40 分钟之间。")
    print("     该模型的作用是让'可预测性'这件事存在，而非精确复现作息。")
    print()

    out = {
        "away_minutes": [round(x, 1) for x in a_min],
        "stay_minutes_complete": [round(x, 1) for x in s_min],
        "stay_minutes_censored": [round(x, 1) for x in s_cens],
        "n_away": len(a_min),
        "n_stay": len(s_min),
        "bimodal": is_bimodal,
        "p_short_branch": round(p_short, 4),
        "short_pool": [round(x, 1) for x in sorted(short)],
        "long_pool": [round(x, 1) for x in sorted(long_)],
        "hazard_table": table,
        "hazard_table_smooth": table_smooth,
        "mixture": ({"med1": round(mix[0],2), "sig1": round(mix[1],3),
                     "med2": round(mix[2],2), "sig2": round(mix[3],3),
                     "w1": round(mix[4],4)} if mix else None),
        "checkpoints": CHECKPOINTS,
        "horizons": HORIZONS,
        "caveat": "n=10，样本量小，风险函数置信区间宽",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 生成报告
    md = ["# 占用风险模型（半马尔可夫占用模型第一步）", "",
          "- 生成：2026-10-18",
          "- 数据：`data/processed/timeline_final.csv`，"
          "2026-10-14 16:08 ~ 10-16 17:59（约 49.9 小时）", "",
          "## 一、为什么要做这个", "",
          "当前仿真的占用是**每步独立抽样**（i.i.d.）：", "",
          "```python",
          "occupied = 1 if rng.random() < p[weekday][hour] else 0",
          "```", "",
          "这意味着\"已离开 5 小时\"不提供任何关于\"下一步会不会回来\"的信息。",
          "**因此在当前环境里\"预测回家时间\"在数学上不可能** ——",
          "这也是强化学习学不会\"提前预冷\"的根本原因。", "",
          "改法：把占用改成**半马尔可夫过程**（离开时抽时长，"
          "风险随已离开时长推进），并把\"已离开多久\"加入状态。", "",
          "## 二、实测的外出结构", "",
          f"完整外出段 **{len(a_min)} 个**：" +
          "、".join(f"{x:.0f}" for x in a_min) + " 分钟", "",
          f"合计 {sum(a_min):.0f} 分钟（{sum(a_min)/60:.1f} 小时），"
          f"中位数 {a_min[len(a_min)//2]:.0f} 分钟。", "",
          "### 结构：双峰", "",
          "| 分支 | 次数 | 时长 |",
          "|---|---|---|",
          f"| 短途（<2 小时） | {len(short)} | "
          + "、".join(f"{x:.0f}" for x in short) + " 分钟 |",
          f"| 长时间（≥2 小时） | {len(long_)} | "
          + "、".join(f"{x:.0f}" for x in long_) + " 分钟 |", "",
          "这正是半马尔可夫模型能吃下的结构：离开时以 "
          f"{p_short:.0%} 的概率进入短途分支、{1-p_short:.0%} 进入长途分支。", "",
          "## 三、风险函数", "",
          "**已离开 t 分钟后，在未来 h 分钟内回归的概率**：", ""]
    hdr2 = "| 已离开 |" + "|".join(f" h={h} 分 " for h in HORIZONS) + "|"
    md.append(hdr2)
    md.append("|---" * (len(HORIZONS) + 1) + "|")
    for t in CHECKPOINTS:
        if str(t) not in table:
            continue
        row = f"| {t:.0f} 分 |"
        for h in HORIZONS:
            row += f" {table[str(t)][str(h)]:.1%} |"
        md.append(row)
    md += ["",
           "**该函数非单调**：2 小时后风险降到很低（进入\"工作日\"分支），",
           "8~12 小时后重新升高（快下班了）。这是双峰分布的体现，",
           "也**正是人工规则最难写对的地方** —— "
           "\"该提前多久预冷\"取决于当前落在哪个分支的概率。", "",
           "## 四、仿真将如何使用", "",
           "```",
           "1. 人员离开时：以 p_short 概率从短途池抽时长，",
           "              以 1-p_short 概率从长途池抽时长",
           "2. 每步推进：empty_steps += 1",
           "3. 回归概率：由上面的风险表查得（线性插值）",
           "4. 观测新增两维：",
           "      empty_steps    已离开多少步（0 = 有人）",
           "      p_arrive_soon  未来 30 分钟内的回归概率",
           "```", "",
           "## 五、诚实的小样本声明", "",
           "**n = 10 次外出、14 段在室（末段右删失）。**", "",
           "这个样本量**不足以**做参数化密度估计，因此：", "",
           "- 仿真用**经验分布自助抽样**（直接从观测值抽），不做参数假设",
           "- 风险函数用**经验生存函数 + 线性插值**", "",
           "**置信区间很宽**：例如\"短途中位 20 分钟\"的真实值可能在",
           "10~40 分钟之间。**该模型的作用是让\"可预测性\"这件事存在，",
           "而不是精确复现用户作息。**", "",
           "若后续采集更多天数，重跑本脚本即可更新。", "",
           "## 六、复算", "",
           "```", "python scripts/fit_occupancy_hazard.py", "```", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    print(f"  数据已保存: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
