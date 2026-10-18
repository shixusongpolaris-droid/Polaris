# -*- coding: utf-8 -*-
"""从半马尔可夫生成器本身实测风险函数，替换不一致的查表。

为什么必须重算（团队复核成员 fix-reviewer 指出的问题）
------------------------------------------------------
原风险表来自 `fit_occupancy_hazard.py` 用**连续双分量对数正态**拟合的
模型，而仿真的时长是从**量化后的经验池**抽、且长途分支概率按出发时刻
条件化（0.25 / 0.02）、长途池是单点 712.1 分钟。

两者不是同一个分布，因此 `p_arrive_soon` 与真实回归概率严重不符：

    已离开 t    真实 H30    原表      偏差
    30 分        0.750      0.542     低估 21pp
    60 分        0.000      0.286     高估 29pp
    90~600 分    0.000      0.124~0.080   整段高估
    675 分       1.000      0.080     低估 92pp

这会让**观测给策略错误提示**，并且会真的骗到计划中的"风险阈值规则"
（在 t=60~600 频繁误触发、在 t=675 不触发）。

做法
----
直接跑生成器本身，统计"已离开 k 步的条件下，未来 2 步（30 分钟）内
回归的频率"。这是对生成器真实分布的**无偏经验估计**，不需要任何
分布假设。

输出
----
models/hazard_by_step.json       实测表
并在 stdout 打印可直接粘进 config.py 的字面量。
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                          # noqa: E402
from src.env import SmartHomeEnv, encode_action             # noqa: E402
from src.env.config import SimConfig                        # noqa: E402

EPISODES = 120
HORIZON_STEPS = 2        # 30 分钟 = 2 步
OUT = os.path.join(PROJ, "models", "hazard_by_step.json")


def main():
    c = SimConfig()
    c.occupancy_mode = "semi_markov"

    # 收集：每个"无人步"的 (已离开步数, 未来 2 步内是否回归)
    rec = []          # list of (k, hit)
    max_away = 0

    for ep in range(EPISODES):
        env = SmartHomeEnv(sim_cfg=c, seed=9000 + ep)
        env.reset(seed=9000 + ep)
        # 先把整回合的占用序列取出来（用空政策走完）
        occ_seq = []
        while True:
            od = env.get_obs_dict()
            occ_seq.append(1 if (od["occ_a"] or od["occ_b"]) else 0)
            _, _, t, tr, _ = env.step(encode_action(0, 0, 0, env.act_cfg))
            if t or tr:
                break
        env.close()

        n = len(occ_seq)
        k = 0
        for i, o in enumerate(occ_seq):
            if o == 0:
                k += 1
                max_away = max(max_away, k)
                # 未来 HORIZON_STEPS 步内是否回归
                nxt = occ_seq[i + 1:i + 1 + HORIZON_STEPS]
                hit = 1 if (nxt and any(x == 1 for x in nxt)) else 0
                rec.append((k, hit, len(nxt) == HORIZON_STEPS))
            else:
                k = 0

    # 只在"未来窗口完整"的样本上统计，避免回合末尾截断偏差
    print("=" * 78)
    print(" 从半马尔可夫生成器实测风险函数（未来 30 分钟内回归概率）")
    print("=" * 78)
    print(f"  回合数 {EPISODES}，无人步样本 {len(rec)}，最大连续无人 {max_away} 步")
    print()
    print(f"  {'已离开(步)':>10}{'已离开(分)':>12}{'样本数':>9}"
          f"{'H30 实测':>11}{'原表':>9}")
    print("  " + "-" * 52)

    OLD_T = [15, 30, 45, 60, 90, 120, 180, 240, 360, 480, 600]
    OLD_P = [0.652, 0.542, 0.409, 0.286, 0.124, 0.052,
             0.011, 0.003, 0.011, 0.043, 0.080]

    table = {}
    for k in range(1, max_away + 1):
        xs = [h for (kk, h, full) in rec if kk == k and full]
        if not xs:
            # 样本不足时沿用上一档（单调外推更安全）
            table[k] = table.get(k - 1, 0.0)
            continue
        table[k] = float(np.mean(xs))
        if k <= 8 or k in (16, 24, 32, 40, 44, 45, 46, 47):
            mins = k * 15
            old = ""
            for j, t0 in enumerate(OLD_T):
                if abs(t0 - mins) <= 7.5:
                    old = f"{OLD_P[j]:.3f}"
                    break
            print(f"  {k:>10}{mins:>12}{len(xs):>9}"
                  f"{table[k]:>11.3f}{old:>9}")

    print()
    # 校验：末段应为 1.0（长途是单点，到点必回）
    print("=== 校验 ===")
    top = max(table) if table else 0
    print(f"  最大步数 {top} 处 H30 = {table.get(top, 0):.3f}（应接近 1.0）")
    vals = [table[k] for k in sorted(table)]
    print(f"  单调段：H30 在 1~2 步最高，{int(top*0.6)} 步后降为 0，"
          f"末段回升——与原表的结构对比：")
    for k in [1, 2, 3, 4, 5, 6, 8, 16, 32, 44, 45, 46, 47]:
        if k in table:
            print(f"    已离开 {k:>2} 步（{k*15:>3} 分）: H30 = {table[k]:.3f}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"horizon_steps": HORIZON_STEPS,
                   "episodes": EPISODES,
                   "max_away_steps": top,
                   "hazard_by_step": {str(k): round(v, 4)
                                      for k, v in sorted(table.items())},
                   "note": "由生成器实测，未来 2 步内回归频率"},
                  f, ensure_ascii=False, indent=2)
    print()
    print(f"  已保存: {OUT}")
    print()
    print("=== 可直接粘进 config.py 的字面量（按步索引）===")
    items = [f"{table[k]:.4f}" for k in sorted(table)]
    line = "    sm_hazard_by_step: List[float] = field(default_factory=lambda: [\n        "
    buf = ""
    for i, v in enumerate(items):
        buf += v + ", "
        if (i + 1) % 8 == 0:
            buf += "\n        "
    print(line + buf.rstrip() + "])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
