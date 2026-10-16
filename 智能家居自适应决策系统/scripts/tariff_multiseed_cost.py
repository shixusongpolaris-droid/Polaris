# -*- coding: utf-8 -*-
"""对多种子训练出的 18 个模型补算「电费」，并做配对比较。

为什么需要这个脚本
------------------
`tariff_multiseed.py` 只记录了电量。但分时电价的价值在于把用电挪到
低价时段 —— 总电量可能相同甚至更高，电费却更低。因此必须按
「当步电价 × 当步电量」逐笔累加得到真实电费。

每个模型在其**训练时所用的电价制度**下评估（真实账单口径），
并按随机种子做配对比较（同一种子下 flat 与 TOU 的差异），
配对可以消掉一部分种子间波动。

输出
----
models/tariff_multiseed/cost_eval.json
docs/上海电价多种子电费对比.md
"""

import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                        # noqa: E402
from src.env import RewardConfig, SmartHomeEnv            # noqa: E402
from stable_baselines3 import PPO                         # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "tariff_multiseed")
OUT_JSON = os.path.join(OUT_DIR, "cost_eval.json")
OUT_MD = os.path.join(PROJ, "docs", "上海电价多种子电费对比.md")

EPISODES = 20
SEED0 = 2000
SEEDS = [42, 66, 2026]
TIER_TABLE = {
    1: (0.617, 0.617, 0.307),
    2: (0.667, 0.677, 0.337),
    3: (0.917, 0.977, 0.487),
}


def make_rew_cfg(tou, tier):
    flat, peak, valley = TIER_TABLE[tier]
    rc = RewardConfig(weight_mode="scenario")
    if tou:
        rc.tou_enabled = True
        rc.price_peak = peak
        rc.price_valley = valley
        rc.price_flat = peak
        rc.peak_periods = [(6, 22)]
        rc.valley_periods = [(22, 6)]
    else:
        rc.tou_enabled = False
        rc.price_flat_tariff = flat
    return rc


def evaluate(model, tou, tier, n=EPISODES, seed0=SEED0):
    rc = make_rew_cfg(tou, tier)
    E, C, D, S = [], [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=seed0 + ep)
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
            a = int(model.predict(v, deterministic=True)[0])
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                D.append(float(m.get("discomfort_ratio", 0.0)))
                S.append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {"energy": float(np.mean(E)), "energy_sd": float(np.std(E)),
            "cost": float(np.mean(C)), "cost_sd": float(np.std(C)),
            "discomfort": float(np.mean(D)), "discomfort_sd": float(np.std(D)),
            "switch": float(np.mean(S))}


def main():
    data = {}
    for tier in (1, 2, 3):
        for short, tou in (("flat", False), ("tou", True)):
            for seed in SEEDS:
                tag = f"ms_{short}_t{tier}_s{seed}"
                path = os.path.join(PROJ, "models", tag, "final_model.zip")
                if not os.path.exists(path):
                    print(f"  [跳过] {tag} 模型不存在")
                    continue
                model = PPO.load(path, device="cpu")
                r = evaluate(model, tou, tier)
                data[f"{short}_t{tier}_s{seed}"] = {
                    "tou": tou, "tier": tier, "seed": seed, **r}
                print(f"  {tag:<22} 电量 {r['energy']:.3f}  "
                      f"电费 {r['cost']:.3f} 元/天  不达标 {r['discomfort']:.1%}")

    # ---- 配对比较 ----
    print()
    print("=" * 96)
    print(" 配对比较：同种子下「分时」相对「未分时」的电费变化")
    print("=" * 96)
    print(f"  {'档位':<8}{'种子':>7}{'未分时电费':>12}{'分时电费':>11}"
          f"{'差额':>10}{'变化':>9}")
    print("  " + "-" * 88)
    paired = {}
    for tier in (1, 2, 3):
        diffs = []
        for seed in SEEDS:
            kf = f"flat_t{tier}_s{seed}"
            kt = f"tou_t{tier}_s{seed}"
            if kf not in data or kt not in data:
                continue
            cf, ct = data[kf]["cost"], data[kt]["cost"]
            diffs.append(ct - cf)
            print(f"  第{tier}档{seed:>7}{cf:>12.3f}{ct:>11.3f}"
                  f"{ct-cf:>+10.3f}{(ct-cf)/cf:>+9.1%}")
        if diffs:
            paired[tier] = diffs
            m = float(np.mean(diffs))
            sd = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
            se = sd / (len(diffs) ** 0.5) if len(diffs) > 0 else 0.0
            print(f"  {'':<8}{'均值':>7}{'':>12}{'':>11}{m:>+10.3f}"
                  f"   (标准差 {sd:.3f}, 标准误 {se:.3f})")
        print()

    # ---- 汇总 ----
    print("=" * 96)
    print(" 汇总（均值 ± 标准差，种子数 = 3）")
    print("=" * 96)
    print(f"  {'制度':<12}{'电量kWh/天':>16}{'电费元/天':>16}{'不达标':>14}{'切换/天':>10}")
    print("  " + "-" * 88)
    summary = {}
    for tier in (1, 2, 3):
        for short in ("flat", "tou"):
            keys = [k for k in data if k.startswith(f"{short}_t{tier}_")]
            if not keys:
                continue
            E = np.array([data[k]["energy"] for k in keys])
            C = np.array([data[k]["cost"] for k in keys])
            D = np.array([data[k]["discomfort"] for k in keys])
            S = np.array([data[k]["switch"] for k in keys])
            label = ("未分时" if short == "flat" else "分时") + f"·{tier}档"
            summary[f"{short}_t{tier}"] = {
                "energy": float(E.mean()), "energy_sd": float(E.std(ddof=1)),
                "cost": float(C.mean()), "cost_sd": float(C.std(ddof=1)),
                "discomfort": float(D.mean()),
                "discomfort_sd": float(D.std(ddof=1)),
                "switch": float(S.mean()), "n": len(keys)}
            print(f"  {label:<12}{E.mean():>10.3f}±{E.std(ddof=1):<5.3f}"
                  f"{C.mean():>10.3f}±{C.std(ddof=1):<5.3f}"
                  f"{D.mean():>9.1%}±{D.std(ddof=1):<5.1%}{S.mean():>9.1f}")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"episodes": EPISODES, "seed0": SEED0, "tier_table": TIER_TABLE,
                   "per_model": data, "summary": summary,
                   "paired_diff": {str(k): v for k, v in paired.items()}},
                  f, ensure_ascii=False, indent=2)

    md = ["# 上海电价：多种子电费对比", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 每模型评估 {EPISODES} 回合（种子 {SEED0}~{SEED0+EPISODES-1}）",
          f"- 训练种子：{SEEDS}", "",
          "## 为什么必须看电费而不是电量", "",
          "分时电价的价值在于把用电挪到低价时段。总电量可能相同甚至更高，",
          "电费却更低。只看电量会得出相反结论。",
          "",
          "电费口径：按每步「当时电价 × 当步电量」逐笔累加后除以天数，",
          "与 `evaluate_all.py` 一致。", "",
          "## 各制度结果（训练种子 3 个，评估 20 回合）", "",
          "| 制度 | 电量(kWh/天) | 电费(元/天) | 不达标 | 切换(次/天) |",
          "|---|---|---|---|---|"]
    for tier in (1, 2, 3):
        for short in ("flat", "tou"):
            k = f"{short}_t{tier}"
            if k not in summary:
                continue
            s = summary[k]
            label = ("未分时" if short == "flat" else "分时") + f"·{tier}档"
            md.append(f"| {label} | {s['energy']:.3f} ± {s['energy_sd']:.3f} | "
                      f"{s['cost']:.3f} ± {s['cost_sd']:.3f} | "
                      f"{s['discomfort']:.1%} | {s['switch']:.1f} |")
    md += ["", "## 配对比较（同种子，分时 − 未分时）", "",
           "| 档位 | 种子 | 未分时电费 | 分时电费 | 差额 | 变化 |",
           "|---|---|---|---|---|---|"]
    for tier in (1, 2, 3):
        for seed in SEEDS:
            kf, kt = f"flat_t{tier}_s{seed}", f"tou_t{tier}_s{seed}"
            if kf not in data or kt not in data:
                continue
            cf, ct = data[kf]["cost"], data[kt]["cost"]
            md.append(f"| 第{tier}档 | {seed} | {cf:.3f} | {ct:.3f} | "
                      f"{ct-cf:+.3f} | {(ct-cf)/cf:+.1%} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print()
    print(f"  结果已保存: {OUT_JSON}")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
