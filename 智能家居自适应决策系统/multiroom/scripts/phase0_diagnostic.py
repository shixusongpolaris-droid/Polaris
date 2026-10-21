"""Phase 0：2 房间协调间隙诊断（不训练 RL）。

比较三种策略：
  1. always_on：两房间一直开（目标 25/26）；
  2. independent：只控制当前有人房间，另一间关；
  3. coordinated：在 independent 基础上，若 60 分钟内要换到另一间，提前预冷那一间。

输出：multiroom/docs/phase0_diagnostic.md / .json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # 项目根
sys.path.insert(0, str(ROOT / "multiroom" / "src"))

from multiroom_env import (  # noqa: E402
    AC_SETPOINTS, MultiRoomConfig, MultiRoomSim, is_sleep,
)

CFG = MultiRoomConfig()
START = datetime(2026, 10, 12, 0, 0)                # 周一
STEPS = 7 * CFG.steps_per_day


def target(t):
    return 26.0 if is_sleep(t, CFG) else 25.0


def ac_for(temp_target):
    return min(range(len(AC_SETPOINTS)),
               key=lambda i: abs(AC_SETPOINTS[i] - temp_target)) + 1


def rule_action(temp, tgt, current):
    if temp > tgt + 0.5:
        return ac_for(tgt)
    if temp < tgt - 0.5:
        return 0
    return current


def independent(sim):
    t = sim.t
    s = sim.schedule.state_at(t)
    tgt = target(t)
    a = rule_action(sim.temp_a, tgt, sim.prev_ac_a) if s == "A" else 0
    b = rule_action(sim.temp_b, tgt, sim.prev_ac_b) if s == "B" else 0
    return a, b


def coordinated(sim, horizon_min=60):
    a, b = independent(sim)
    t = sim.t
    nt, ns = sim.schedule.next_transition(t)
    if nt is not None and ns in ("A", "B"):
        if (nt - t).total_seconds() <= horizon_min * 60:
            tgt = target(nt)
            if ns == "A":
                a = rule_action(sim.temp_a, tgt, sim.prev_ac_a)
            else:
                b = rule_action(sim.temp_b, tgt, sim.prev_ac_b)
    return a, b


def always_on(sim):
    tgt = target(sim.t)
    return ac_for(tgt), ac_for(tgt)


def run(policy):
    sim = MultiRoomSim(CFG, START)
    for _ in range(STEPS):
        a, b = policy(sim)
        sim.step(a, b)
    return {
        "cost": sim.cost,
        "energy": sim.energy,
        "energy_a": sim.energy_a,
        "energy_b": sim.energy_b,
        "occ_discomfort": sim.occ_discomfort,
        "bad": sim.bad,
        "occ_steps": sim.occ_steps,
        "bad_a": sim.bad_a,
        "occ_a": sim.occ_steps_a,
        "bad_b": sim.bad_b,
        "occ_b": sim.occ_steps_b,
        "peak_power": sim.peak_power,
        "switch": sim.switch,
    }


def pct(x):
    return f"{x:.2%}"


def main():
    rows = {"always_on": run(always_on), "independent": run(independent)}
    for h in (15, 30, 45, 60):
        rows[f"coord_{h}"] = run(lambda sim, h=h: coordinated(sim, h))
    ind = rows["independent"]
    coords = {h: rows[f"coord_{h}"] for h in (15, 30, 45, 60)}
    cands = [(h, r) for h, r in coords.items()
             if (ind["occ_discomfort"] - r["occ_discomfort"]) >= 0.01]
    best_h, best = (min(cands, key=lambda x: x[1]["cost"])
                    if cands else (None, coords[60]))
    d_cost = (best["cost"] / ind["cost"] - 1) * 100 if ind["cost"] else 0.0
    d_dis = (best["occ_discomfort"] - ind["occ_discomfort"]) * 100
    go = (d_dis <= -1.0) and (d_cost <= 15.0)

    labels = {"always_on": "always_on（两间一直开）",
              "independent": "independent（只开有人间）",
              "coord_15": "coordinated 15min",
              "coord_30": "coordinated 30min",
              "coord_45": "coordinated 45min",
              "coord_60": "coordinated 60min"}
    lines = [
        "# Phase 0：2 房间协调间隙诊断",
        "",
        "> 合成一周（2026-10-12 周一 起 7 天）；B 房间为 A 参数 + 初温 28°C，**未实测**。",
        "> 只做 AC 动作；门默认关闭；无总功率上限；协调规则知道下一段作息（oracle）。",
        "> **这不是 RL 结果，只是“协调空间有多大”的门槛测试。**",
        "",
        "## 一、结果",
        "",
        "| 策略 | 电费(元/周) | 电量(kWh/周) | 有人不达标 | A 房间 | B 房间 | 峰值功率 kW | 切换次数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in rows.items():
        lines.append(
            f"| {labels.get(name, name)} | {r['cost']:.2f} | {r['energy']:.2f} | "
            f"{pct(r['occ_discomfort'])} | "
            f"{pct(r['bad_a']/max(r['occ_a'],1))} | "
            f"{pct(r['bad_b']/max(r['occ_b'],1))} | "
            f"{r['peak_power']:.2f} | {r['switch']} |")
    lines += [
        "",
        "## 二、协调间隙",
        "",
        f"- 最佳协调点：**{labels.get('coord_'+str(best_h), best_h)}**；"
        f"相对独立规则，电费 **{d_cost:+.2f}%**，"
        f"有人不达标 **{d_dis:+.2f}pp**。",
        f"- 最佳协调点相对恒温全开：电费 "
        f"{(best['cost']/rows['always_on']['cost']-1)*100:+.1f}%，"
        f"不达标 {pct(best['occ_discomfort'])} vs "
        f"{pct(rows['always_on']['occ_discomfort'])}"
        f"（不同舒适度，仅参考）。",
        "",
        "## 三、Go / No-Go",
        "",
        f"**{'GO' if go else 'NO-GO'}**：最佳协调点相对独立规则的不达标改善 "
        f"{abs(d_dis):.2f}pp，电费变化 {d_cost:+.2f}%。",
        "",
        "判定阈值（预设）：",
        "- GO：不达标改善 ≥1pp，且电费增加 ≤15%；",
        "- NO-GO：改善 <1pp，或成本增加 >15% 只换来很小改善。",
        "",
        "## 四、局限",
        "",
        "- 合成作息、合成 B 房间；不是实测多房间；",
        "- 协调规则是 oracle（知道下一段作息），给出的是协调空间的**上限**；",
        "- 只做 AC，不含灯/通风；门默认关闭；无总功率上限；",
        "- 单周、确定性；不能当统计显著性；",
        "- 还没有 RL；Phase 0 只回答“值不值得做”。",
    ]
    md = ROOT / "multiroom" / "docs" / "phase0_diagnostic.md"
    js = ROOT / "multiroom" / "docs" / "phase0_diagnostic.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "best_horizon": best_h,
                   "d_cost_pct": d_cost, "d_discomfort_pp": d_dis,
                   "go": go}, f, ensure_ascii=False, indent=2)
    print(f"wrote {md}")
    print(f"wrote {js}")
    for name, r in rows.items():
        print(f"{name:12s} cost={r['cost']:7.2f}  energy={r['energy']:7.2f}  "
              f"occ_dis={r['occ_discomfort']:.2%}  peak={r['peak_power']:.2f}")
    print(f"best={best_h}  vs ind: cost {d_cost:+.2f}%  occ {d_dis:+.2f}pp  "
          f"{'GO' if go else 'NO-GO'}")


if __name__ == "__main__":
    main()
