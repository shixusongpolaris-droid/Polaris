"""诊断一个 PPO 模型 vs 规则的行为差异（单场景 9100）。"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))
sys.path.insert(0, str(ROOT / "multiroom" / "scripts"))

from gym_env import GymMultiRoomEnv  # noqa: E402
from multiroom_env import comfort_band  # noqa: E402
import phase0_diagnostic as p0  # noqa: E402

OUT = ROOT / "multiroom" / "docs" / "phase2_diagnosis.md"


def summarize(records):
    cost = sum(r["cost"] for r in records)
    occ = [r for r in records if r["state"] in ("A", "B")]
    bad = sum(1 for r in occ if r["bad"])
    return {
        "cost": cost,
        "occ": bad / max(len(occ), 1),
        "ac_a_empty": sum(1 for r in records if r["ac_a"] > 0 and r["state"] != "A"),
        "ac_b_empty": sum(1 for r in records if r["ac_b"] > 0 and r["state"] != "B"),
        "ac_out": sum(1 for r in records
                      if r["state"] == "out" and (r["ac_a"] > 0 or r["ac_b"] > 0)),
        "ac_a_on": sum(1 for r in records if r["ac_a"] > 0),
        "ac_b_on": sum(1 for r in records if r["ac_b"] > 0),
        "steps": len(records),
    }


def record_step(env, info):
    sim = env.sim
    lo, hi = comfort_band(sim.t, sim.cfg)
    state = info["state"]
    bad = False
    if state == "A":
        bad = not (lo <= sim.temp_a <= hi)
    elif state == "B":
        bad = not (lo <= sim.temp_b <= hi)
    return {"state": state, "temp_a": sim.temp_a, "temp_b": sim.temp_b,
            "ac_a": sim.prev_ac_a, "ac_b": sim.prev_ac_b,
            "cost": info["cost"], "bad": bad}


def run_model(path, seed=9100):
    from stable_baselines3 import PPO
    model = PPO.load(str(path / "model"), device="cpu")
    z = np.load(path / "norm_stats.npz")
    mean, var = z["mean"], z["var"]
    env = GymMultiRoomEnv(energy_scale=30.0, seed=seed)
    obs, _ = env.reset(seed=seed)
    rec = []
    while True:
        v = np.asarray(obs, dtype=np.float32)
        v = np.clip((v - mean) / np.sqrt(var + 1e-8), -10, 10)
        action = model.predict(v, deterministic=True)[0]
        obs, _, term, trunc, info = env.step(action)
        rec.append(record_step(env, info))
        if term or trunc:
            break
    return rec


def run_rule(fn, seed=9100):
    env = GymMultiRoomEnv(energy_scale=30.0, seed=seed)
    env.reset(seed=seed)
    rec = []
    while True:
        a, b = fn(env.sim)
        _, _, term, trunc, info = env.step([int(a), int(b)])
        rec.append(record_step(env, info))
        if term or trunc:
            break
    return rec


def main():
    ppo = run_model(ROOT / "multiroom" / "models" / "ppo" / "es10_s42")
    ind = run_rule(p0.independent)
    c15 = run_rule(lambda s: p0.coordinated(s, 15))
    rows = {"PPO es10_s42": summarize(ppo),
            "independent": summarize(ind),
            "coord15": summarize(c15)}
    lines = ["# Phase 2 行为诊断（场景 9100）", "",
             "> 只用于解释 PPO 为什么差；不是统计结论。", "",
             "| 策略 | 电费 | 不达标 | A 空开步 | B 空开步 | 外出时开 | A 总开 | B 总开 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in rows.items():
        lines.append(f"| {name} | {r['cost']:.2f} | {r['occ']:.2%} | "
                     f"{r['ac_a_empty']} | {r['ac_b_empty']} | {r['ac_out']} | "
                     f"{r['ac_a_on']} | {r['ac_b_on']} |")
    lines += ["", "## 读法", "",
              "- `A 空开步`：人不在 A 但 A 空调开着（可能是预冷，也可能是浪费）。",
              "- `B 空开步`：人不在 B 但 B 空调开着。",
              "- `外出时开`：人不在家（out）仍开空调，通常是浪费。",
              "- coord15 的“空开”有一部分是**有意的下一间预冷**；PPO 如果空开高、但没换来舒适，就是没学好。",
              ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for name, r in rows.items():
        print(f"{name:14s} cost={r['cost']:.2f} occ={r['occ']:.2%} "
              f"emptyA={r['ac_a_empty']} emptyB={r['ac_b_empty']} out={r['ac_out']}")


if __name__ == "__main__":
    main()
