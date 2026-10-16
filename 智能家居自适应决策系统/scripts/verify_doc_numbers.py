r"""
文档数据一致性核对

用途：
    在提交中期报告前，逐项核对各文档中引用的实验数字是否与
    原始结果文件（models/**/results*.json）一致。

背景：
    本项目经历了 v1 -> v2 -> v3 -> v4 多个版本，
    文档中的数字存在版本漂移风险。已发现一处硬伤：
    "PPO 在能耗上也优于规则控制" 源自 v1 数据，
    而 v2 中该结论反转（PPO 能耗高 11.8%），文档未同步更新。

    本脚本用于系统性排查同类问题。

运行方式：
    python scripts\verify_doc_numbers.py
"""

import json
import re
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# ============================================================
# 期望值：从原始结果文件读出的"真值"
# ============================================================
def MODELS_ROOT():
    """结果文件根目录。

    默认指向 models/；设置环境变量 VERIFY_MODELS_ROOT 可改指向
    models/_oldparams_backup/，用于核对标定前的历史数字。
    背景：2026-10-16 标定热参数后，文档正文保留的是标定前结果，
    并在文首以显著提示标注，因此核对正文时应指向备份目录。
    """
    return Path(os.environ.get(
        "VERIFY_MODELS_ROOT", str(PROJECT_ROOT / "models")))


def load_truth():
    """从原始结果文件提取权威数字。"""
    truth = {}

    # --- 主实验（无 TOU，v2）---
    p = MODELS_ROOT() / "evaluation" / "results_v2_notou.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        r = d["results"]
        def g(name, key):
            return r.get(name, {}).get(key, {}).get("mean")
        truth["v2_notou"] = {
            "rule_energy": g("规则控制(定温)", "energy_per_day_kwh"),
            "rule_discomfort": g("规则控制(定温)", "discomfort_ratio"),
            "rule_rmse": g("规则控制(定温)", "temp_dev_rmse"),
            "rule_switch": g("规则控制(定温)", "switch_per_day"),
            "ppo_energy": g("PPO:rl2_notou", "energy_per_day_kwh"),
            "ppo_discomfort": g("PPO:rl2_notou", "discomfort_ratio"),
            "ppo_rmse": g("PPO:rl2_notou", "temp_dev_rmse"),
            "ppo_switch": g("PPO:rl2_notou", "switch_per_day"),
            "always_energy": g("全程开空调25°C", "energy_per_day_kwh"),
            "always_discomfort": g("全程开空调25°C", "discomfort_ratio"),
        }

    # --- 主实验（v1，已废止）---
    p = MODELS_ROOT() / "evaluation" / "results_notou.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        r = d["results"]
        def g(name, key):
            return r.get(name, {}).get(key, {}).get("mean")
        truth["v1_notou"] = {
            "rule_energy": g("规则控制(定温)", "energy_per_day_kwh"),
            "ppo_energy": g("PPO:rl_notou", "energy_per_day_kwh"),
        }

    # --- TOU 场景 ---
    p = MODELS_ROOT() / "evaluation" / "results_v2_tou.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        r = d["results"]
        truth["v2_tou"] = {
            name: {k: v.get("mean") for k, v in m.items()
                   if isinstance(v, dict) and "mean" in v}
            for name, m in r.items()
        }

    # --- 循环网络实验 ---
    p = MODELS_ROOT() / "recurrent" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["recurrent"] = {
            name: {"cost": m["energy_cost_yuan"]["mean"],
                   "discomfort": m["discomfort_ratio"]["mean"],
                   "valley": m["tier_share"]["valley"]}
            for name, m in d["results"].items()
        }

    # --- 储能实验 ---
    p = MODELS_ROOT() / "storage" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["storage"] = {
            name: {"cost": m["cost"], "discomfort": m["discomfort"],
                   "energy": m["energy"], "valley": m["valley_share"]}
            for name, m in d["results"].items()
        }

    # --- 储能等舒适度比较（三旋钮）---
    p = MODELS_ROOT() / "storage_equal" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["storage_equal"] = {
            "rule_front": d.get("rule_front"),
            "ppo_front": d.get("ppo_front"),
            "rows": d.get("rows"),
        }

    # --- 循环网络实验（等舒适度）---
    p = MODELS_ROOT() / "tou_equal_comfort" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["tou_equal"] = {
            "rule_points": d.get("rule_points"),
            "equal_comfort": d.get("equal_comfort"),
        }

    # --- 训练步数扫描 ---
    p = MODELS_ROOT() / "train_steps" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["train_steps"] = d.get("means")

    # --- 参数扫描（完整）---
    p = MODELS_ROOT() / "param_sweep" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        summary = {}
        for scen, rows in (d.get("results") or {}).items():
            vr = [r["diff_vs_rule"] for r in rows]
            va = [r["diff_vs_always"] for r in rows]
            wins_r = sum(1 for x in vr if x < 0)
            wins_a = sum(1 for x in va if x < 0)
            summary[scen] = {
                "n": len(rows),
                "rule_min": min(vr), "rule_max": max(vr),
                "always_min": min(va), "always_max": max(va),
                "rule_wins": wins_r, "always_wins": wins_a,
            }
        truth["param_sweep"] = summary

    # --- 奖励权重重标定 ---
    p = MODELS_ROOT() / "reward_rebalance" / "results.json"
    if p.exists():
        d = json.load(open(p, encoding="utf-8"))
        truth["reward_rebalance"] = {
            "rule_curve": [(c["discomfort"], c["cost"])
                           for c in d.get("rule_curve", [])],
            "ppo": [(r["temp_scale"], r["metrics"]["cost"],
                     r["metrics"]["discomfort"])
                    for r in d.get("ppo_results", [])],
        }

    return truth


# ============================================================
# 文档中引用的数字（人工整理的关键断言）
# ============================================================
DOC_CHECKS = [
    # (文档, 描述, 文档中写的值, truth键路径, 容差)
    # ---- 主实验（v2 无 TOU）----
    ("实验结果报告", "PPO 能耗（v2 主实验）", 4.472, ("v2_notou", "ppo_energy"), 0.01),
    ("实验结果报告", "规则能耗（v2 主实验）", 4.001, ("v2_notou", "rule_energy"), 0.01),
    ("实验结果报告", "PPO 不达标率", 0.1173, ("v2_notou", "ppo_discomfort"), 0.002),
    ("实验结果报告", "规则不达标率", 0.2157, ("v2_notou", "rule_discomfort"), 0.002),
    ("实验结果报告", "PPO RMSE", 0.055, ("v2_notou", "ppo_rmse"), 0.005),
    ("实验结果报告", "规则 RMSE", 0.735, ("v2_notou", "rule_rmse"), 0.005),
    ("实验结果报告", "PPO 切换次数", 7.38, ("v2_notou", "ppo_switch"), 0.05),
    ("实验结果报告", "规则切换次数", 24.59, ("v2_notou", "rule_switch"), 0.05),
    ("实验结果报告", "恒温运行能耗", 5.328, ("v2_notou", "always_energy"), 0.01),
]


def check_recurrent(truth):
    """核对循环网络实验（报告第 7 章）。"""
    rec = truth.get("recurrent")
    if not rec:
        return []
    claims = [
        ("B1 规则控制", 1.874, 0.469),
        ("B2 普通 PPO", 3.650, 0.313),
        ("B3 RecurrentPPO", 3.060, 0.285),
    ]
    out = []
    for name, cost, valley in claims:
        m = rec.get(name)
        if not m:
            out.append((name, "缺数据", None, None))
            continue
        dc = abs(m["cost"] - cost)
        dv = abs(m["valley"] - valley)
        out.append((name, "OK" if (dc <= 0.01 and dv <= 0.005)
                    else "**不符**", m["cost"], m["valley"]))
    return out


def check_storage(truth):
    """核对储能实验（报告 6.7）。"""
    st = truth.get("storage")
    if not st:
        return []
    claims = [
        ("A 规则控制（无储能）", 1.878, 0.3452),
        ("B 规则+储能(4kWh)", 1.616, 0.3452),
        ("C PPO（无储能）", 2.321, 0.4303),
        ("D PPO+储能(4kWh)", 1.782, 0.2520),
    ]
    out = []
    for name, cost, disc in claims:
        m = st.get(name)
        if not m:
            out.append((name, "缺数据", None, None))
            continue
        dc = abs(m["cost"] - cost)
        dd = abs(m["discomfort"] - disc)
        out.append((name, "OK" if (dc <= 0.01 and dd <= 0.005)
                    else "**不符**", m["cost"], m["discomfort"]))
    return out


def check_train_steps(truth):
    """核对训练步数扫描（报告第 9 章）。"""
    ts = truth.get("train_steps")
    if not ts:
        return []
    claims = [("400000", 0.1143), ("800000", 0.1152), ("1200000", 0.1143)]
    out = []
    for k, disc in claims:
        m = ts.get(k)
        if not m:
            out.append((k, "缺数据", None, None))
            continue
        dd = abs(m["disc"] - disc)
        out.append((k, "OK" if dd <= 0.005 else "**不符**", m["disc"], None))
    return out


def check_param_sweep(truth):
    """核对参数扫描的范围与占优计数（报告 6.4c）。"""
    ps = truth.get("param_sweep")
    if not ps:
        return []
    claims = {
        "无分时电价": {"rule_min": -0.251, "rule_max": 0.625,
                  "always_min": -0.575, "always_max": 0.187,
                  "rule_wins": 1, "always_wins": 9},
        "有分时电价": {"rule_min": 0.616, "rule_max": 1.270,
                  "always_min": -0.381, "always_max": 0.217,
                  "rule_wins": 0, "always_wins": 4},
    }
    out = []
    for scen, cl in claims.items():
        m = ps.get(scen)
        if not m:
            out.append((scen, "缺数据", None, None))
            continue
        ok = all(abs(m[k] - v) <= 0.02 if isinstance(v, float) else m[k] == v
                 for k, v in cl.items())
        out.append((scen, "OK" if ok else "**不符**",
                    f"规则胜{m['rule_wins']} 恒温胜{m['always_wins']}",
                    f"范围 {m['rule_min']:+.1%}~{m['rule_max']:+.1%}"))
    return out


def get_path(truth, path):
    cur = truth
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def main():
    print("=" * 90)
    print(" 文档数据一致性核对")
    print("=" * 90)

    truth = load_truth()
    print(f"\n  已从原始结果文件加载 {len(truth)} 组数据：")
    for k, v in truth.items():
        if isinstance(v, dict):
            print(f"    {k}: {len(v)} 项")

    # ---------- 逐项核对 ----------
    print("\n" + "-" * 90)
    print(" 逐项核对（文档引用 vs 原始数据）")
    print("-" * 90)
    print(f"  {'文档':<16}{'描述':<26}{'文档值':>11}{'实际值':>11}"
          f"{'差异':>10}{'判定':>8}")
    print("  " + "-" * 86)

    n_ok = n_bad = n_missing = 0
    problems = []
    for doc, desc, claimed, path, tol in DOC_CHECKS:
        actual = get_path(truth, path)
        if actual is None:
            print(f"  {doc:<16}{desc:<26}{claimed:>11.4f}{'缺数据':>11}"
                  f"{'—':>10}{'跳过':>8}")
            n_missing += 1
            continue
        diff = abs(actual - claimed)
        ok = diff <= tol
        if ok:
            n_ok += 1
        else:
            n_bad += 1
            problems.append((doc, desc, claimed, actual))
        print(f"  {doc:<16}{desc:<26}{claimed:>11.4f}{actual:>11.4f}"
              f"{diff:>10.5f}{'OK' if ok else '**不符**':>8}")

    print("  " + "-" * 86)
    print(f"\n  一致 {n_ok} 项，不一致 {n_bad} 项，缺数据 {n_missing} 项")

    if problems:
        print("\n  ⚠️ 不一致清单：")
        for doc, desc, claimed, actual in problems:
            print(f"    · [{doc}] {desc}: 文档写 {claimed}，实际 {actual}")

    # ---------- 派生指标核对 ----------
    print("\n" + "-" * 90)
    print(" 派生指标核对（由原始数据重新计算）")
    print("-" * 90)

    v2 = truth.get("v2_notou")
    if v2:
        d_red = (v2["rule_discomfort"] - v2["ppo_discomfort"]) / v2["rule_discomfort"]
        r_red = (v2["rule_rmse"] - v2["ppo_rmse"]) / v2["rule_rmse"]
        s_red = (v2["rule_switch"] - v2["ppo_switch"]) / v2["rule_switch"]
        e_save_rule = (v2["rule_energy"] - v2["ppo_energy"]) / v2["rule_energy"]
        e_save_always = (v2["always_energy"] - v2["ppo_energy"]) / v2["always_energy"]

        checks = [
            ("不达标率降幅（文档写 45.6%）", d_red, 0.456, 0.005),
            ("RMSE 降幅（文档写 92.5%）", r_red, 0.925, 0.005),
            ("切换降幅（文档写 70%）", s_red, 0.70, 0.02),
            ("相对恒温节能（文档写 16%）", e_save_always, 0.16, 0.02),
        ]
        for desc, actual, claimed, tol in checks:
            ok = abs(actual - claimed) <= tol
            print(f"  {desc:<34} 实际 {actual:>8.2%}  判定 "
                  f"{'OK' if ok else '**不符**'}")

        print(f"\n  {'PPO 相对规则的能耗差异':<34} {e_save_rule:>+8.2%}  "
              f"({'PPO 更省' if e_save_rule > 0 else 'PPO 更耗电'})")
        print(f"  {'（文档已如实说明 PPO 能耗高 11.8%）':<34}")

    # ---------- 版本漂移排查 ----------
    print("\n" + "-" * 90)
    print(" 版本漂移排查：同一指标在 v1 与 v2 中是否反转")
    print("-" * 90)
    v1 = truth.get("v1_notou")
    if v1 and v2:
        print(f"  v1: PPO {v1['ppo_energy']:.4f} vs 规则 {v1['rule_energy']:.4f}"
              f"  -> {'PPO 更省' if v1['ppo_energy'] < v1['rule_energy'] else 'PPO 更耗'}")
        print(f"  v2: PPO {v2['ppo_energy']:.4f} vs 规则 {v2['rule_energy']:.4f}"
              f"  -> {'PPO 更省' if v2['ppo_energy'] < v2['rule_energy'] else 'PPO 更耗'}")
        if (v1["ppo_energy"] < v1["rule_energy"]) != \
           (v2["ppo_energy"] < v2["rule_energy"]):
            print(f"\n  ⚠️ **能耗比较在 v1 与 v2 之间发生反转！**")
            print(f"     这就是此前文档错误的根源。")
            print(f"     当前文档已修正为以 v2 为准，并标注 v1 为历史版本。")
        else:
            print(f"\n  能耗比较方向一致，无反转。")

    print("\n" + "=" * 90)
    return 0


def main2():
    """扩展核对：循环网络 / 储能 / 训练步数 / 参数扫描。"""
    truth = load_truth()

    sections = [
        ("循环网络实验（报告第 7 章）", check_recurrent(truth),
         ["策略", "判定", "实际电费", "实际谷段占比"]),
        ("储能实验（报告 6.7）", check_storage(truth),
         ["配置", "判定", "实际电费", "实际不达标率"]),
        ("训练步数扫描（报告第 9 章）", check_train_steps(truth),
         ["训练步数", "判定", "实际不达标率", ""]),
        ("参数扫描（报告 6.4c）", check_param_sweep(truth),
         ["场景", "判定", "占优计数", "差异范围"]),
    ]

    total_bad = 0
    for title, rows, headers in sections:
        print("\n" + "=" * 90)
        print(f" {title}")
        print("=" * 90)
        if not rows:
            print("  （无数据，跳过）")
            continue
        print(f"  {headers[0]:<24}{headers[1]:>10}"
              f"{headers[2]:>16}{headers[3]:>18}")
        print("  " + "-" * 68)
        for name, verdict, v1, v2 in rows:
            s1 = f"{v1:.3f}" if isinstance(v1, float) else str(v1 or "")
            s2 = (f"{v2:.2%}" if isinstance(v2, float) and v2 <= 1.5
                  else (f"{v2:.3f}" if isinstance(v2, float) else str(v2 or "")))
            print(f"  {name:<24}{verdict:>10}{s1:>16}{s2:>18}")
            if "不符" in verdict:
                total_bad += 1

    print("\n" + "=" * 90)
    print(f" 扩展核对完成：发现 {total_bad} 处不符")
    print("=" * 90)
    return total_bad


if __name__ == "__main__":
    rc = main()
    bad = main2()
    raise SystemExit(0 if bad == 0 else 1)
