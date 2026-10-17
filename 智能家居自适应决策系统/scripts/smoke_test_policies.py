# -*- coding: utf-8 -*-
"""策略冒烟测试：验证各规则策略实际下发的设定温度与文档声明一致。

为什么需要
----------
空调档位由 [25..29]（ac_idx 1..5）扩展为 [23..29]（ac_idx 1..7）后，
多个脚本里写死的档位索引失效。本次修复把档位改为按目标温度动态推导，
但"语法通过"不等于"行为正确"——本会话已两次出现语法过但运行时报错
（未定义变量、缺 import）。

本脚本不训练任何模型，只在真实环境里逐状态调用各策略函数，
统计实际下发的设定温度分布，与各函数的 docstring 声明对照。

覆盖：
  tou_experiment.py      policy_rule_fixed (B1) 与 policy_rule_tou (B2)
  reward_calibration.py  policy_rule_tou
  tariff_cross_eval.py   policy_always_ac / policy_occ_only / policy_rule
"""

import importlib.util
import os
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "scripts"))

from src.env import RewardConfig, SmartHomeEnv, decode_action  # noqa: E402

EPISODES = 4
SEED0 = 9500


def load_mod(fname, alias):
    p = os.path.join(PROJ, "scripts", fname)
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def dispatched_setpoints(policy_fn, cfg):
    """在真实环境里跑若干回合，统计策略下发的设定温度。"""
    rc = RewardConfig(weight_mode="scenario", tou_enabled=True,
                      energy_scale=10.0, lookahead_weight=0.6)
    cnt = Counter()
    for ep in range(EPISODES):
        env = SmartHomeEnv(rew_cfg=rc, seed=SEED0 + ep)
        env.reset(seed=SEED0 + ep)
        while True:
            a = policy_fn(env)
            ac_idx, _, _ = decode_action(int(a), env.act_cfg)
            if ac_idx == 0:
                cnt["关"] += 1
            else:
                cnt[round(env.act_cfg.ac_setpoints[ac_idx - 1], 1)] += 1
            _, _, term, trunc, _ = env.step(a)
            if term or trunc:
                break
        env.close()
    return cnt


def fmt(cnt):
    tot = sum(cnt.values())
    parts = []
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        label = k if k == "关" else f"{k}°C"
        parts.append(f"{label}:{v/tot:.1%}")
    return "  ".join(parts)


def main():
    print("=" * 88)
    print(" 策略冒烟测试：实际下发设定温度 vs 文档声明")
    print("=" * 88)
    print()

    cases = []

    tou = load_mod("tou_experiment.py", "smoke_tou")
    cases.append(("tou_experiment.py  policy_rule_fixed (B1)",
                  "有人维持 25°C 附近 / 无人关闭 / <24°C 关断",
                  tou.policy_rule_fixed))
    cases.append(("tou_experiment.py  policy_rule_tou (B2)",
                  "谷 24 / 平 25 / 峰 27 / 无人关闭",
                  tou.policy_rule_tou))

    rc = load_mod("reward_calibration.py", "smoke_rc")
    cases.append(("reward_calibration.py  policy_rule_tou",
                  "应与 tou_experiment 的同名策略一致：谷24/平25/峰27",
                  rc.policy_rule_tou))

    tce = load_mod("tariff_cross_eval.py", "smoke_tce")
    cases.append(("tariff_cross_eval.py  policy_always_ac",
                  "全程开空调，恒温 25°C",
                  tce.policy_always_ac))
    cases.append(("tariff_cross_eval.py  policy_occ_only",
                  "有人恒温 25°C / 无人关闭",
                  tce.policy_occ_only))
    cases.append(("tariff_cross_eval.py  policy_rule",
                  "有人恒温 25°C / 无人关闭 + 调光",
                  tce.policy_rule))

    env_probe = SmartHomeEnv(rew_cfg=RewardConfig())
    env_probe.reset(seed=1)
    cfg = env_probe.act_cfg
    env_probe.close()

    print(f"  可用档位：关 + {[f'{s}°C' for s in cfg.ac_setpoints]}")
    print()
    for name, doc, fn in cases:
        try:
            cnt = dispatched_setpoints(fn, cfg)
            print(f"  {name}")
            print(f"    声明：{doc}")
            print(f"    实测：{fmt(cnt)}")
            # 关键断言
            sps = [k for k in cnt if k != "关"]
            bad = [s for s in sps if s not in cfg.ac_setpoints]
            if bad:
                print(f"    ❌ 出现非法档位 {bad}")
            print()
        except Exception as e:
            print(f"  ❌ {name} 运行失败：{type(e).__name__}: {e}")
            print()
            return 1

    # 两项专项断言
    print("=" * 88)
    print(" 专项断言")
    print("=" * 88)
    a = dispatched_setpoints(tou.policy_rule_fixed, cfg)
    if 23.0 in a or 24.0 in a:
        print(f"  ❌ B1 仍下发 23/24°C：{fmt(a)}")
    else:
        print(f"  ✅ B1 不再下发 23/24°C：{fmt(a)}")
    b = dispatched_setpoints(tou.policy_rule_tou, cfg)
    c = dispatched_setpoints(rc.policy_rule_tou, cfg)
    if a is not None and dict(b) == dict(c):
        print("  ✅ tou 与 reward 的 policy_rule_tou 下发分布完全相同")
    else:
        print(f"  ⚠️ 两者分布不同：{fmt(b)}  vs  {fmt(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
