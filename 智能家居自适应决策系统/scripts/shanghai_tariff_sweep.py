# -*- coding: utf-8 -*-
"""上海居民电价敏感性实验：6 组（分时/未分时 × 三档）训练与对比。

背景
----
依据上海市居民用电价格表（项目所有者提供）：

    一户一表居民用户（按户年用电量分档，单位：元/度）
      档位      年用电量        未分时   分时·峰时   分时·谷时
      第一档    0~3120(含)      0.617     0.617       0.307
      第二档    3120~4800(含)    0.667     0.677       0.337
      第三档    4800 以上        0.917     0.977       0.487

    峰谷时段：峰 06:00-22:00，谷 22:00-次日 06:00

档位单价随"户年用电量"累进，而仿真单回合仅 7 天，无法观测年度累计，
故经确认采用**固定档位**口径：一次实验内电价恒定，分别对三个档位训练。

设计
----
两个维度：
  · 电价结构：分时（峰谷两段） / 未分时（全天同一价）
  · 阶梯档位：第一档 / 第二档 / 第三档
共 6 组，每组均评估同一套基线（全程开空调、有人开无人关、规则滞环）。

三档的峰谷比同为 2.01，差别只在绝对水平，因此该设计等价于
对"电价水平"做敏感性分析。

输出
----
models/tariff_tier_sweep/summary.json   全部结果
docs/上海电价敏感性实验.md               可读报告
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
OUT_DIR = os.path.join(PROJ, "models", "tariff_tier_sweep")
OUT_JSON = os.path.join(OUT_DIR, "summary.json")
OUT_MD = os.path.join(PROJ, "docs", "上海电价敏感性实验.md")

# 上海居民分时/未分时电价表
TIERS = {
    1: {"flat": 0.617, "peak": 0.617, "valley": 0.307, "range": "0~3120 度"},
    2: {"flat": 0.667, "peak": 0.677, "valley": 0.337, "range": "3120~4800 度"},
    3: {"flat": 0.917, "peak": 0.977, "valley": 0.487, "range": "4800 度以上"},
}

CONFIGS = []
for t in (1, 2, 3):
    CONFIGS.append({"tariff": "shanghai_flat", "tier": t,
                    "tag": f"sh_flat_t{t}"})
for t in (1, 2, 3):
    CONFIGS.append({"tariff": "shanghai_tou", "tier": t,
                    "tag": f"sh_tou_t{t}"})

TIMESTEPS = 400000
EVAL_EPISODES = 20
SEED = 42


def run_one(cfg):
    cmd = [PY, TRAIN,
           "--timesteps", str(TIMESTEPS),
           "--eval-episodes", str(EVAL_EPISODES),
           "--weight-mode", "scenario",
           "--seed", str(SEED),
           "--tariff", cfg["tariff"],
           "--tier", str(cfg["tier"]),
           "--tag", cfg["tag"]]
    print(f"\n{'='*72}")
    print(f" 运行 {cfg['tag']}  ({cfg['tariff']}, 第{cfg['tier']}档)")
    print('='*72)
    r = subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  [失败] 退出码 {r.returncode}")
        if r.stderr:
            print("  stderr:", r.stderr[-800:])
        return None
    # 读结果
    fp = os.path.join(PROJ, "models", cfg["tag"], "train_result.json")
    if not os.path.exists(fp):
        print("  [失败] 未生成 train_result.json")
        return None
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}

    for cfg in CONFIGS:
        res = run_one(cfg)
        if res is None:
            continue
        results[cfg["tag"]] = {
            "tariff": cfg["tariff"],
            "tier": cfg["tier"],
            "prices": TIERS[cfg["tier"]],
            "train_time_sec": res.get("train_time_sec"),
            "results": res.get("results", {}),
        }
        # 打印该组结果
        for name, v in res.get("results", {}).items():
            if not isinstance(v, dict):
                continue
            e = v.get("energy_per_day_kwh", [None])[0]
            d = v.get("discomfort_ratio", [None])[0]
            c = v.get("energy_cost_yuan", [None])
            c = c[0] if isinstance(c, list) and c else None
            print(f"    {name:<18} 能耗 {e:6.3f} kWh/天  "
                  f"不达标 {d:6.1%}  "
                  + (f"电费 {c:6.2f} 元/天" if c is not None else ""))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"configs": CONFIGS, "timesteps": TIMESTEPS,
                   "eval_episodes": EVAL_EPISODES, "seed": SEED,
                   "tiers": TIERS, "results": results},
                  f, ensure_ascii=False, indent=2)

    # ---------- 汇总表 ----------
    print("\n" + "=" * 92)
    print(" 汇总：PPO 本方法在 6 种电价场景下的表现")
    print("=" * 92)
    print(f"  {'场景':<14}{'峰价':>7}{'谷价':>7}{'能耗kWh/天':>12}"
          f"{'不达标':>9}{'电费元/天':>11}{'切换/天':>9}")
    print("  " + "-" * 86)
    rows = []
    for tag in [c["tag"] for c in CONFIGS]:
        if tag not in results:
            continue
        r = results[tag]
        v = r["results"].get("PPO(本方法)")
        if not v:
            continue
        e = v["energy_per_day_kwh"][0]
        d = v["discomfort_ratio"][0]
        sw = v["switch_per_day"][0]
        c = v.get("energy_cost_yuan", [None])
        c = c[0] if isinstance(c, list) and c else None
        p = r["prices"]
        label = ("分时" if r["tariff"] == "shanghai_tou" else "未分时") + f"·{r['tier']}档"
        print(f"  {label:<14}{p['peak']:>7.3f}{p['valley']:>7.3f}"
              f"{e:>12.3f}{d:>9.1%}" + (f"{c:>11.2f}" if c is not None else f"{'-':>11}")
              + f"{sw:>9.1f}")
        rows.append((label, p, e, d, c, sw, r["tariff"], r["tier"]))

    # Markdown
    md = ["# 上海居民电价敏感性实验", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 训练步数：{TIMESTEPS:,}，评估回合：{EVAL_EPISODES}，种子：{SEED}",
          "",
          "## 电价依据",
          "",
          "上海市居民用电价格表（一户一表居民用户，按户年用电量分档）：",
          "",
          "| 档位 | 年用电量 | 未分时 | 分时·峰时 | 分时·谷时 |",
          "|---|---|---|---|---|"]
    for t in (1, 2, 3):
        p = TIERS[t]
        md.append(f"| 第{t}档 | {p['range']} | {p['flat']} | {p['peak']} | {p['valley']} |")
    md += ["",
           "峰谷时段：峰 06:00-22:00，谷 22:00-次日 06:00。",
           "居民可自主选择是否执行分时；未选择的统一按「未分时」栏计费。",
           "",
           "## 结果（PPO 本方法）",
           "",
           "| 场景 | 峰价 | 谷价 | 能耗(kWh/天) | 不达标 | 电费(元/天) | 切换(次/天) |",
           "|---|---|---|---|---|---|---|"]
    for label, p, e, d, c, sw, tariff, tier in rows:
        md.append(f"| {label} | {p['peak']:.3f} | {p['valley']:.3f} | {e:.3f} | "
                  f"{d:.1%} | " + (f"{c:.2f}" if c is not None else "-") + f" | {sw:.1f} |")
    md.append("")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print()
    print(f"  结果已保存: {OUT_JSON}")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
