# -*- coding: utf-8 -*-
"""重评 models/gen7_tariff_six 下 30 个已保存 PPO 模型（不训练、不更新权重）。

用法： python run_models.py --variant old|new_off|new --out out/models_old.json
"""
import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402

MODEL_ROOT = os.path.join(H.REPO, "models", "gen7_tariff_six")
SEEDS = [42, 66, 2026, 7, 123]
TAGS = ["t1_flat", "t1_tou", "t2_flat", "t2_tou", "t3_flat", "t3_tou"]


def model_fn(model, stats):
    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        v = (v - stats["mean"]) / np.sqrt(stats["var"] + 1e-8)
        v = np.clip(v, -10.0, 10.0)
        return int(model.predict(v, deterministic=True)[0])
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=list(H.VARIANTS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from stable_baselines3 import PPO  # 延迟导入

    se = H.load_env(args.variant)
    con = H.make_con(se, args.variant)
    t0 = time.perf_counter()
    out = {}
    for tag in TAGS:
        rc = H.make_rc(se, tag)
        for sd in SEEDS:
            d = os.path.join(MODEL_ROOT, "%s_s%d" % (tag, sd))
            model = PPO.load(os.path.join(d, "model"), device="cpu")
            z = np.load(os.path.join(d, "norm_stats.npz"))
            stats = {"mean": z["mean"], "var": z["var"]}
            r = H.measure(se, rc, con, model_fn(model, stats), tag=tag,
                          name="%s_s%d" % (tag, sd), variant=args.variant)
            r["seed"] = sd
            out[r["name"]] = r
            print("  %-14s e=%.4f cost=%.4f occ_d=%.4f bad=%.4f "
                  "e_empty_share=%.3f c4=%.3f (%.0fs)"
                  % (r["name"], r["energy"], r["cost"], r["occ_discomfort"],
                     r["discomfort"], r["empty_energy_share"],
                     r["c4_override_share"], time.perf_counter() - t0),
                  flush=True)
    H.save_json({"variant": args.variant, "episodes": H.EVAL_EPISODES,
                 "seed0": H.EVAL_SEED0, "models": out}, args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
