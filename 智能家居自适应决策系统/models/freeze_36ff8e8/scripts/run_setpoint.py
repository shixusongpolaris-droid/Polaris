# -*- coding: utf-8 -*-
"""S1 回归小实验：恒温 23~29°C 的日均电量是否随设定温度单调下降。

规则：T > setpoint+0.5 开空调（最近档），T < setpoint-0.5 关，否则保持。
在 old / old_newenergy / new 三个变体上各跑一次，仅看电量（与电价无关）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402


def ac_idx_for(t, cfg):
    best, bd = 0, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    se = H.load_env(args.variant)
    rc = H.make_rc(se, "t1_flat")
    con = H.make_con(se, args.variant)
    out = {}
    for sp in [23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]:
        def fn(env, sp=sp):
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            cfg = env.act_cfg
            if T > sp + 0.5:
                ac = ac_idx_for(sp, cfg)
            elif T < sp - 0.5:
                ac = 0
            else:
                ac = env.sim.state.ac_idx
            light = 1 if od["light_in"] < 300 else 0
            return se.encode_action(ac, light, 0, cfg)
        r = H.measure(se, rc, con, fn, tag="t1_flat",
                      name="恒温 %.0f" % sp, variant=args.variant)
        out["%.0f" % sp] = {k: r[k] for k in
                            ("energy", "cost", "occ_discomfort",
                             "e_empty_per_day", "ac_on_share_empty")}
        print("%s  设定 %.0f°C：%.3f kWh/天，不达标 %.2f%%，无人开机率 %.1f%%"
              % (args.variant, sp, r["energy"], r["occ_discomfort"] * 100,
                 r["ac_on_share_empty"] * 100), flush=True)
    H.save_json(out, args.out)


if __name__ == "__main__":
    main()
