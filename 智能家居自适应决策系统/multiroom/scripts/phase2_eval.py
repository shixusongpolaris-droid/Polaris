"""Phase 2：2 房间基线 vs PPO 的 8 回合评估。

基线：independent / coordinated 15min / coordinated 45min / always_on
RL：models/ppo/es{10,30,60}_s{42,66,2026,7,123}
评估：种子 9100~9107（8 个随机场景），与训练场景同分布。
"""
import argparse
import json
import math
import sys
from pathlib import Path
from statistics import mean, stdev

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))
sys.path.insert(0, str(ROOT / "multiroom" / "scripts"))

from gym_env import GymMultiRoomEnv  # noqa: E402
import phase0_diagnostic as p0  # noqa: E402

EVAL_SEEDS = list(range(9100, 9108))
T95_7 = 2.365
T95_4 = 2.776
OUT = ROOT / "multiroom" / "docs" / "phase2_results"


def run_baseline(policy, seed):
    env = GymMultiRoomEnv(energy_scale=30.0, seed=seed)
    env.reset(seed=seed)
    steps = 0
    while True:
        a, b = policy(env.sim)
        _, _, term, trunc, _ = env.step([int(a), int(b)])
        steps += 1
        if term or trunc:
            break
    sim = env.sim
    return {
        "cost": sim.cost,
        "energy": sim.energy,
        "occ_discomfort": sim.occ_discomfort,
        "bad_a": sim.bad_a / max(sim.occ_steps_a, 1),
        "bad_b": sim.bad_b / max(sim.occ_steps_b, 1),
        "peak_power": sim.peak_power,
        "switch": sim.switch,
        "steps": steps,
    }


def model_fn(model, stats):
    def fn(obs):
        v = np.asarray(obs, dtype=np.float32)
        v = (v - stats["mean"]) / np.sqrt(stats["var"] + 1e-8)
        v = np.clip(v, -10.0, 10.0)
        return model.predict(v, deterministic=True)[0]
    return fn


def run_model(path, seed):
    from stable_baselines3 import PPO
    model = PPO.load(str(path / "model"), device="cpu")
    z = np.load(path / "norm_stats.npz")
    fn = model_fn(model, {"mean": z["mean"], "var": z["var"]})
    env = GymMultiRoomEnv(energy_scale=30.0, seed=seed)
    obs, _ = env.reset(seed=seed)
    while True:
        action = fn(obs)
        obs, _, term, trunc, _ = env.step(action)
        if term or trunc:
            break
    sim = env.sim
    return {
        "cost": sim.cost,
        "energy": sim.energy,
        "occ_discomfort": sim.occ_discomfort,
        "bad_a": sim.bad_a / max(sim.occ_steps_a, 1),
        "bad_b": sim.bad_b / max(sim.occ_steps_b, 1),
        "peak_power": sim.peak_power,
        "switch": sim.switch,
    }


def agg(rows):
    n = len(rows)
    cost = mean(r["cost"] for r in rows)
    occ = mean(r["occ_discomfort"] for r in rows)
    return {
        "n": n,
        "cost": cost,
        "cost_ci": (T95_7 * stdev(r["cost"] for r in rows) / math.sqrt(n)
                    if n > 1 else 0.0),
        "energy": mean(r["energy"] for r in rows),
        "occ_discomfort": occ,
        "bad_a": mean(r["bad_a"] for r in rows),
        "bad_b": mean(r["bad_b"] for r in rows),
        "peak_power": mean(r["peak_power"] for r in rows),
        "switch": mean(r["switch"] for r in rows),
        "rows": rows,
    }


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    va = stdev(a) ** 2 / len(a)
    vb = stdev(b) ** 2 / len(b)
    se = math.sqrt(va + vb)
    if se <= 1e-12:
        return None
    return (mean(a) - mean(b)) / se


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-root",
                    default=str(ROOT / "multiroom" / "models" / "ppo"))
    ap.add_argument("--suffix", default="",
                    help="输出文件后缀，如 _1m")
    args = ap.parse_args()
    global OUT
    OUT = ROOT / "multiroom" / "docs" / f"phase2_results{args.suffix}"
    models_root = Path(args.models_root)

    policies = {
        "always_on": p0.always_on,
        "independent": p0.independent,
        "coord_15": lambda sim: p0.coordinated(sim, 15),
        "coord_45": lambda sim: p0.coordinated(sim, 45),
    }
    base = {}
    for name, fn in policies.items():
        rows = [run_baseline(fn, s) for s in EVAL_SEEDS]
        base[name] = agg(rows)
        print(f"{name:12s} cost={base[name]['cost']:.2f} "
              f"occ={base[name]['occ_discomfort']:.2%}", flush=True)

    rl = {}
    for es in (10, 30, 60):
        per_seed = []
        for seed in (42, 66, 2026, 7, 123):
            path = models_root / f"es{es:g}_s{seed}"
            if not path.exists():
                print(f"missing {path}", flush=True)
                continue
            rows = [run_model(path, s) for s in EVAL_SEEDS]
            m = mean(r["cost"] for r in rows)
            o = mean(r["occ_discomfort"] for r in rows)
            per_seed.append({"seed": seed, "cost": m,
                             "occ_discomfort": o, "rows": rows})
            print(f"es={es:g} seed={seed} cost={m:.2f} occ={o:.2%}",
                  flush=True)
        if per_seed:
            costs = [p["cost"] for p in per_seed]
            occs = [p["occ_discomfort"] for p in per_seed]
            rl[f"es{es:g}"] = {
                "n": len(per_seed),
                "cost": mean(costs),
                "cost_ci": (T95_4 * stdev(costs) / math.sqrt(len(costs))
                            if len(costs) > 1 else 0.0),
                "occ_discomfort": mean(occs),
                "per_seed": per_seed,
            }

    # 对比：RL vs independent / coord_15
    comp = {}
    for key, r in rl.items():
        t_ind = welch([p["cost"] for p in r["per_seed"]],
                      [x["cost"] for x in base["independent"]["rows"]])
        t_c15 = welch([p["cost"] for p in r["per_seed"]],
                      [x["cost"] for x in base["coord_15"]["rows"]])
        comp[key] = {"vs_independent_t": t_ind, "vs_coord15_t": t_c15}

    out = {"eval_seeds": EVAL_SEEDS, "baselines": base, "rl": rl,
           "compare": comp}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(str(OUT) + ".json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    lines = [
        "# Phase 2：2 房间基线 vs PPO",
        "",
        f"> 评估场景：种子 {EVAL_SEEDS[0]}~{EVAL_SEEDS[-1]}（8 个随机场景，同训练分布）。",
        "> 所有策略同环境、同场景；C3 每房间安全覆盖开启。",
        "> 合成 B 房间、合成作息；不是实测多房间。",
        "",
        "## 一、基线",
        "",
        "| 策略 | 电费(元/周) | 电量(kWh/周) | 有人不达标 | A | B | 峰值 kW |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("always_on", "independent", "coord_15", "coord_45"):
        r = base[name]
        lines.append(f"| {name} | {r['cost']:.2f} | {r['energy']:.2f} | "
                     f"{r['occ_discomfort']:.2%} | {r['bad_a']:.2%} | "
                     f"{r['bad_b']:.2%} | {r['peak_power']:.2f} |")
    def fmt_t(t):
        return "-" if t is None else f"{t:+.2f}"

    lines += ["", "## 二、PPO", "",
              "| 模型 | 种子数 | 电费(元/周) | 有人不达标 | vs independent t | vs coord15 t |",
              "|---|---:|---:|---:|---:|---:|"]
    for key, r in rl.items():
        c = comp[key]
        lines.append(f"| {key} | {r['n']} | {r['cost']:.2f}±{r['cost_ci']:.2f} | "
                     f"{r['occ_discomfort']:.2%} | "
                     f"{fmt_t(c['vs_independent_t'])} | "
                     f"{fmt_t(c['vs_coord15_t'])} |")
    lines += [
        "",
        "## 三、判读（自动生成，需人工/盲评复核）",
        "",
        "- 负 t 表示 RL 比基线便宜；正 t 表示 RL 更贵。",
        "- 这只比较电费；必须同时看“有人不达标”，否则便宜可能是牺牲舒适。",
        "- 强基线是 coord_15 / coord_45，不是 independent；",
        "  如果 RL 只赢 independent、赢不了 coord，结论仍是“只优于朴素规则”。",
        "- n=5 个训练种子，t 值只是近似；不要当精确显著性。",
    ]
    with open(str(OUT) + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {OUT}.md / .json")


if __name__ == "__main__":
    main()
