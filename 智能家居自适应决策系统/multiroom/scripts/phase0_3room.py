"""3 房间 Phase 0：独立规则 vs 协调规则 vs 全开（不训练 RL）。

一人在 A/B/C/外出；三台空调共享 1.6 kW 上限；
协调规则 = 独立规则 + 下一间提前 30 分钟预冷 + 预冷时用更高设定点（降低功率）。
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))

from env3 import (  # noqa: E402
    AC_SETPOINTS, ROOMS, Config3, Sim3, is_sleep,
)

SEEDS = list(range(9200, 9208))
STEPS = 7 * 96
OUT = ROOT / "multiroom" / "docs" / "phase0_3room"


def target(t):
    return 26.0 if is_sleep(t, Config3()) else 25.0


def ac_for(tgt):
    return min(range(len(AC_SETPOINTS)),
               key=lambda i: abs(AC_SETPOINTS[i] - tgt)) + 1


def rule(temp, tgt, current):
    if temp > tgt + 0.5:
        return ac_for(tgt)
    if temp < tgt - 0.5:
        return 0
    return current


def independent(sim):
    s = sim.schedule.state_at(sim.t)
    tgt = target(sim.t)
    return tuple(rule(sim.temp[i], tgt, sim.prev_ac[i]) if s == room else 0
                 for i, room in enumerate(ROOMS))


def coordinated(sim):
    acs = list(independent(sim))
    nt, ns = sim.schedule.next_transition(sim.t)
    if nt is not None and ns in ROOMS:
        if (nt - sim.t).total_seconds() <= 1800:
            i = ROOMS.index(ns)
            # 功率感知：当前有人房间已有 AC 在开，预冷用 27°C 降低功率
            pre_tgt = 27.0 if sum(1 for v in sim.prev_ac if v > 0) > 0 else target(nt)
            acs[i] = rule(sim.temp[i], pre_tgt, sim.prev_ac[i])
    return tuple(acs)


def always_on(sim):
    return (ac_for(target(sim.t)),) * 3


def scenario(seed):
    rng = np.random.default_rng(seed)
    start = datetime(2026, 10, 12) + __import__("datetime").timedelta(
        days=int(rng.integers(0, 7)))
    offset = int(rng.integers(0, 60))
    cfg = Config3(
        ua=(0.15175 * float(rng.uniform(0.9, 1.1)),
            0.160 * float(rng.uniform(0.9, 1.1)),
            0.140 * float(rng.uniform(0.9, 1.1))),
        temp_init=(26.0, 26.0 + float(rng.uniform(2, 4)),
                   26.0 + float(rng.uniform(1, 3))),
    )
    return cfg, start, offset


def run(policy, seed):
    cfg, start, offset = scenario(seed)
    sim = Sim3(cfg, start, offset)
    for _ in range(STEPS):
        sim.step(policy(sim))
    return {
        "cost": sim.cost,
        "energy": sum(sim.energy),
        "occ_discomfort": sim.occ_discomfort,
        "bad_a": sim.bad[0] / max(sim.occ_steps[0], 1),
        "bad_b": sim.bad[1] / max(sim.occ_steps[1], 1),
        "bad_c": sim.bad[2] / max(sim.occ_steps[2], 1),
        "peak_power": sim.peak_power,
        "cap_binds": sim.cap_binds,
        "unserved": sim.unserved,
        "switch": sim.switch,
    }


def agg(rows):
    return {
        "cost": mean(r["cost"] for r in rows),
        "energy": mean(r["energy"] for r in rows),
        "occ_discomfort": mean(r["occ_discomfort"] for r in rows),
        "bad_a": mean(r["bad_a"] for r in rows),
        "bad_b": mean(r["bad_b"] for r in rows),
        "bad_c": mean(r["bad_c"] for r in rows),
        "peak_power": mean(r["peak_power"] for r in rows),
        "cap_binds": mean(r["cap_binds"] for r in rows),
        "unserved": mean(r["unserved"] for r in rows),
        "switch": mean(r["switch"] for r in rows),
    }


def main():
    rows = {}
    for name, fn in (("always_on", always_on),
                     ("independent", independent),
                     ("coordinated", coordinated)):
        rows[name] = agg([run(fn, s) for s in SEEDS])
        print(f"{name:12s} cost={rows[name]['cost']:.2f} "
              f"occ={rows[name]['occ_discomfort']:.2%} "
              f"cap={rows[name]['cap_binds']:.1f} "
              f"unserved={rows[name]['unserved']:.2f}", flush=True)
    ind = rows["independent"]
    co = rows["coordinated"]
    d_cost = (co["cost"] / ind["cost"] - 1) * 100
    d_dis = (co["occ_discomfort"] - ind["occ_discomfort"]) * 100
    go = (d_dis <= -1.0) and (d_cost <= 15.0)

    lines = [
        "# Phase 0：3 房间 + 总功率上限诊断",
        "",
        "> 合成一周 × 8 个场景；一人在 A/B/C/外出；B/C 未实测。",
        "> 三台空调共享 **1.6 kW** 上限；门默认关闭；只做 AC。",
        "> 协调规则 = 独立规则 + 下一间提前 30 分钟预冷（预冷用 27°C 降功率）。",
        "> **不是 MPC / RL 结果；只是“更多房间 + 共享功率”的门槛测试。**",
        "",
        "## 一、结果",
        "",
        "| 策略 | 电费(元/周) | 电量(kWh/周) | 有人不达标 | A | B | C | 峰值 kW | 限功率步 | 未满足冷量 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in rows.items():
        lines.append(
            f"| {name} | {r['cost']:.2f} | {r['energy']:.2f} | "
            f"{r['occ_discomfort']:.2%} | {r['bad_a']:.2%} | "
            f"{r['bad_b']:.2%} | {r['bad_c']:.2%} | "
            f"{r['peak_power']:.2f} | {r['cap_binds']:.1f} | "
            f"{r['unserved']:.1f} |")
    lines += [
        "",
        "## 二、协调间隙",
        "",
        f"- 协调 vs 独立：电费 **{d_cost:+.2f}%**，"
        f"不达标 **{d_dis:+.2f}pp**。",
        f"- 协调相对全开：电费 "
        f"{(co['cost']/rows['always_on']['cost']-1)*100:+.1f}%，"
        f"不达标 {co['occ_discomfort']:.2%} vs "
        f"{rows['always_on']['occ_discomfort']:.2%}。",
        "",
        "## 三、Go / No-Go",
        "",
        f"**{'GO' if go else 'NO-GO'}**：不达标改善 {abs(d_dis):.2f}pp，"
        f"电费变化 {d_cost:+.2f}%。",
        "",
        "判定：",
        "- GO：协调规则仍能在 ≤15% 成本内把不达标改善 ≥1pp；",
        "- NO-GO：协调规则被功率上限卡住，改善很小但成本明显上升。",
        "",
        "## 四、局限",
        "",
        "- 一人在三间/外出；B/C 房间合成；单周 × 8 场景；",
        "- 只做 AC；门关闭；限功率按比例削减（不是优先级分配）；",
        "- 协调规则不是 MPC；没有 RL；",
        "- 没有墙体蓄热/潜热/压缩机循环；不能外推真实家庭。",
    ]
    md = OUT.with_suffix(".md")
    js = OUT.with_suffix(".json")
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "d_cost_pct": d_cost,
                   "d_discomfort_pp": d_dis, "go": go},
                  f, ensure_ascii=False, indent=2)
    print(f"wrote {md}")
    print(f"coord vs ind: cost {d_cost:+.2f}% occ {d_dis:+.2f}pp "
          f"{'GO' if go else 'NO-GO'}")


if __name__ == "__main__":
    main()
