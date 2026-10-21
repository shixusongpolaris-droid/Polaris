# -*- coding: utf-8 -*-
"""单个 (variant, tariff) 组合上评估 941 条规则。用法：
  python run_rules.py --variant new --tariff t1_tou --out out/rules_new_t1_tou.json
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=list(H.VARIANTS))
    ap.add_argument("--tariff", required=True, choices=list(H.TARIFFS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed0", type=int, default=0)
    args = ap.parse_args()

    se = H.load_env(args.variant)
    up = H.load_rules()
    specs = up.build_specs()
    if args.limit:
        specs = specs[:args.limit]
    rc = H.make_rc(se, args.tariff)
    con = H.make_con(se, args.variant)

    t0 = time.perf_counter()
    rows = []
    for i, sp in enumerate(specs, 1):
        fn = up.build_rule(sp)
        r = H.measure(se, rc, con, fn, tag=args.tariff, name=sp["name"],
                      variant=args.variant,
                      seed0=(args.seed0 or None))
        r["spec"] = {k: v for k, v in sp.items() if k != "name"}
        rows.append(r)
        if i % 100 == 0:
            print("  %s/%s %d/%d (%.0fs)" % (args.variant, args.tariff, i,
                                             len(specs), time.perf_counter() - t0),
                  flush=True)
    payload = {"variant": args.variant, "tariff": args.tariff,
               "n_rules": len(rows), "episodes": H.EVAL_EPISODES,
               "seed0": H.EVAL_SEED0, "rows": rows,
               "seconds": time.perf_counter() - t0}
    H.save_json(payload, args.out)
    print("  saved %s  (%d rules, %.0fs)" % (args.out, len(rows),
                                             time.perf_counter() - t0))


if __name__ == "__main__":
    main()
