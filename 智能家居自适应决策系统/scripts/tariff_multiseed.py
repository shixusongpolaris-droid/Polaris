# -*- coding: utf-8 -*-
"""上海电价：多种子重复实验（判断"选分时"是否稳定省钱）。

为什么要多种子
--------------
单次训练存在随机性。上一轮「第 3 档分时反而更贵」的结果只来自一个种子，
无法判断那是真实现象还是训练偶然。本脚本对每种电价制度跑多个种子，
用均值 ± 标准差给出可判断的结论。

实验设计
--------
固定档位下比较「选分时」与「不选分时」——这是居民的真实选择，
因为阶梯档位由户年用电量决定，与是否选择分时无关。

  制度 A：未分时（全天按该档"未分时"单价）
  制度 B：分时（峰 6-22 / 谷 22-6，按该档峰谷单价）

对 3 个档位 × 2 种制度 × N 个种子分别训练，再在**各自制度的电价下**
评估电费（这是真实账单口径）。比较同档位下 A 与 B 的电费差异。

输出
----
models/tariff_multiseed/summary.json
docs/上海电价多种子实验.md
"""

import json
import os
import subprocess
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
PY = os.path.join(PROJ, ".venv", "Scripts", "python.exe")
TRAIN = os.path.join(PROJ, "scripts", "train.py")
OUT_DIR = os.path.join(PROJ, "models", "tariff_multiseed")
OUT_JSON = os.path.join(OUT_DIR, "summary.json")
OUT_MD = os.path.join(PROJ, "docs", "上海电价多种子实验.md")

TIMESTEPS = 400000
EVAL_EPISODES = 20
# 扩充种子集合：首轮 3 个种子发现方差极大（同一配置不同种子电量可差
# 一倍以上，部分种子收敛到"基本不开空调"的退化解），
# 因此追加种子以提高结论的统计可靠性。
SEEDS = [42, 66, 2026, 7, 123, 555, 888, 31415]

TIER_TABLE = {
    1: (0.617, 0.617, 0.307),
    2: (0.667, 0.677, 0.337),
    3: (0.917, 0.977, 0.487),
}


def run(tariff, tier, seed, tag):
    # 已训练过的模型直接复用，避免重复训练（追加种子时用）
    fp = os.path.join(PROJ, "models", tag, "train_result.json")
    if os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                o = json.load(f)
            v = o.get("results", {}).get("PPO(本方法)")
            if v:
                return {
                    "energy": v["energy_per_day_kwh"][0],
                    "discomfort": v["discomfort_ratio"][0],
                    "switch": v["switch_per_day"][0],
                    "rmse": v["temp_dev_rmse"][0],
                }
        except Exception:
            pass
    cmd = [PY, TRAIN,
           "--timesteps", str(TIMESTEPS),
           "--eval-episodes", str(EVAL_EPISODES),
           "--weight-mode", "scenario",
           "--seed", str(seed),
           "--tariff", tariff,
           "--tier", str(tier),
           "--tag", tag]
    r = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    fp = os.path.join(PROJ, "models", tag, "train_result.json")
    if not os.path.exists(fp):
        return None
    with open(fp, encoding="utf-8") as f:
        o = json.load(f)
    v = o.get("results", {}).get("PPO(本方法)")
    if not v:
        return None
    return {
        "energy": v["energy_per_day_kwh"][0],
        "discomfort": v["discomfort_ratio"][0],
        "switch": v["switch_per_day"][0],
        "rmse": v["temp_dev_rmse"][0],
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}

    total = 3 * 2 * len(SEEDS)
    done = 0
    for tier in (1, 2, 3):
        for tariff in ("shanghai_flat", "shanghai_tou"):
            short = "flat" if tariff == "shanghai_flat" else "tou"
            key = f"{short}_t{tier}"
            results[key] = {"tariff": tariff, "tier": tier, "seed_results": {}}
            for seed in SEEDS:
                done += 1
                tag = f"ms_{short}_t{tier}_s{seed}"
                print(f"  [{done}/{total}] {tag} ...", flush=True)
                r = run(tariff, tier, seed, tag)
                if r:
                    results[key]["seed_results"][str(seed)] = r
                    print(f"        电量 {r['energy']:.3f} kWh/天  "
                          f"不达标 {r['discomfort']:.1%}")
                else:
                    print("        失败")

    # 汇总
    def agg(key, field):
        vals = [v[field] for v in results[key]["seed_results"].values()]
        if not vals:
            return None
        m = sum(vals) / len(vals)
        var = sum((x - m) ** 2 for x in vals) / len(vals) if len(vals) > 1 else 0.0
        return m, var ** 0.5, len(vals)

    print()
    print("=" * 92)
    print(" 多种子结果汇总")
    print("=" * 92)
    print(f"  {'制度':<12}{'种子数':>7}{'电量kWh/天':>14}{'不达标':>12}{'切换/天':>12}")
    print("  " + "-" * 88)
    for tier in (1, 2, 3):
        for short in ("flat", "tou"):
            key = f"{short}_t{tier}"
            e = agg(key, "energy")
            d = agg(key, "discomfort")
            s = agg(key, "switch")
            if not e:
                continue
            label = ("未分时" if short == "flat" else "分时") + f"·{tier}档"
            print(f"  {label:<12}{e[2]:>7}{e[0]:>10.3f}±{e[1]:.3f}"
                  f"{d[0]:>10.1%}±{d[1]:.1%}{s[0]:>10.1f}")

    # 同档位比较
    print()
    print("=" * 92)
    print(" 核心比较：同档位下「选分时」相对「不选分时」的电量与舒适度变化")
    print("=" * 92)
    rows = []
    for tier in (1, 2, 3):
        ef = agg(f"flat_t{tier}", "energy")
        et = agg(f"tou_t{tier}", "energy")
        df = agg(f"flat_t{tier}", "discomfort")
        dt = agg(f"tou_t{tier}", "discomfort")
        if not (ef and et):
            continue
        dE = (et[0] - ef[0]) / ef[0]
        dD = dt[0] - df[0]
        print(f"  第{tier}档  电量 {ef[0]:.3f} -> {et[0]:.3f} ({dE:+.1%})   "
              f"不达标 {df[0]:.1%} -> {dt[0]:.1%} ({dD:+.1%})")
        rows.append((tier, ef, et, df, dt, dE, dD))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"seeds": SEEDS, "timesteps": TIMESTEPS,
                   "eval_episodes": EVAL_EPISODES,
                   "tier_table": TIER_TABLE, "results": results},
                  f, ensure_ascii=False, indent=2)

    md = ["# 上海电价多种子实验", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 种子：{SEEDS}，每种子训练 {TIMESTEPS:,} 步，评估 {EVAL_EPISODES} 回合",
          "",
          "## 设计",
          "",
          "固定档位比较「选分时」与「不选分时」——这是居民的真实选择，",
          "因为阶梯档位由户年用电量决定，与是否选择分时无关。",
          "",
          "| 制度 | 峰价 | 谷价 |",
          "|---|---|---|"]
    for tier in (1, 2, 3):
        flat, peak, valley = TIER_TABLE[tier]
        md.append(f"| 第{tier}档 未分时 | {flat} | {flat} |")
        md.append(f"| 第{tier}档 分时 | {peak} | {valley} |")
    md += ["", "## 结果（均值 ± 标准差）", "",
           "| 制度 | 种子数 | 电量(kWh/天) | 不达标 | 切换(次/天) |",
           "|---|---|---|---|---|"]
    for tier in (1, 2, 3):
        for short in ("flat", "tou"):
            key = f"{short}_t{tier}"
            e = agg(key, "energy"); d = agg(key, "discomfort"); s = agg(key, "switch")
            if not e:
                continue
            label = ("未分时" if short == "flat" else "分时") + f"·{tier}档"
            md.append(f"| {label} | {e[2]} | {e[0]:.3f} ± {e[1]:.3f} | "
                      f"{d[0]:.1%} ± {d[1]:.1%} | {s[0]:.1f} |")
    md += ["", "## 同档位比较", "",
           "| 档位 | 未分时电量 | 分时电量 | 电量变化 | 未分时不达标 | 分时不达标 |",
           "|---|---|---|---|---|---|"]
    for tier, ef, et, df, dt, dE, dD in rows:
        md.append(f"| 第{tier}档 | {ef[0]:.3f} ± {ef[1]:.3f} | {et[0]:.3f} ± {et[1]:.3f} | "
                  f"**{dE:+.1%}** | {df[0]:.1%} | {dt[0]:.1%} |")
    md.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print()
    print(f"  结果已保存: {OUT_JSON}")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
