# -*- coding: utf-8 -*-
"""标定半马尔可夫占用模型的两个参数，使在室率接近实测。

问题
----
初版参数下（p_long_before_10=0.75，夜间只用长时间池）实测得到
有人 53.7%，而实测数据是 67.5% —— 人物外出过于频繁。

原因是两个参数都在把"外出"推多：
  · 10 点前的出发被高概率判为长途（712 分钟），而出发时刻在白天
    大致均匀，故长途占比远高于实测的 1/10
  · 夜间只从长时间池（500/620）抽，使夜间在室时长偏长、
    白天出发密度被相对放大

本脚本网格搜索两个参数，取"在室率最接近 67.5%"的组合。

参数
----
  p_long_before_10    10 点前出发进入长途分支的概率
  night_long_prob     夜间在室时从长时间池抽的概率

评价
----
  主指标：在室率与 67.5% 的绝对偏差
  次指标：长途外出占比应接近 10%（实测 1/10）
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

import numpy as np                                     # noqa: E402
from src.env import SmartHomeEnv, encode_action        # noqa: E402
from src.env.config import SimConfig                   # noqa: E402

TARGET_OCC = 0.675
EPISODES = 6
GRID_P = [0.15, 0.25, 0.35, 0.50, 0.75]
GRID_N = [0.20, 0.35, 0.50, 0.70, 1.00]

OUT = os.path.join(PROJ, "models", "sm_calibration.json")


def measure(p_long, night_prob):
    """运行若干回合，返回 (在室率, 长途外出占比, 外出段中位时长)。"""
    c = SimConfig()
    c.occupancy_mode = "semi_markov"
    c.sm_p_long_before_10 = p_long
    # 把"夜间长池概率"注入：夜间按该概率抽长池，否则抽一般池
    orig_draw = None

    occ = tot = 0
    long_aways = away_n = 0
    runs = []
    prev = 0
    cur_away = 0
    for ep in range(EPISODES):
        env = SmartHomeEnv(sim_cfg=c, seed=8100 + ep)
        env.reset(seed=8100 + ep)
        sim = env.sim
        # 打补丁：让夜间按 night_prob 决定是否用长池
        def draw_stay(self, _np=night_prob):
            hour = self.sim_time.hour
            if hour >= 22 or hour < 6:
                pool = (self.sim_cfg.sm_stay_long_pool
                        if self._rng.random() < _np
                        else self.sim_cfg.sm_stay_pool)
            else:
                pool = self.sim_cfg.sm_stay_pool
            minutes = float(self._rng.choice(pool))
            return max(1, int(round(minutes / self.sim_cfg.dt_minutes)))
        import types
        sim._sm_draw_stay_steps = types.MethodType(draw_stay, sim)

        while True:
            od = env.get_obs_dict()
            o = 1 if (od["occ_a"] or od["occ_b"]) else 0
            occ += o
            tot += 1
            if prev == 1 and o == 0:
                cur_away = 0
            if o == 0:
                cur_away += 1
            if prev == 0 and o == 1 and cur_away > 0:
                away_n += 1
                if cur_away * 15 >= 120:
                    long_aways += 1
                runs.append(cur_away * 15)
            prev = o
            _, _, t, tr, _ = env.step(encode_action(0, 0, 0, env.act_cfg))
            if t or tr:
                break
        env.close()
    occ_rate = occ / max(tot, 1)
    long_frac = long_aways / max(away_n, 1)
    med = sorted(runs)[len(runs) // 2] if runs else 0
    return occ_rate, long_frac, med


def main():
    print("=" * 78)
    print(" 半马尔可夫占用模型参数标定")
    print("=" * 78)
    print(f"  目标在室率 {TARGET_OCC:.1%}（实测 1937/2859 分钟）")
    print(f"  目标长途占比 约 10%（实测 1/10 次外出）")
    print()
    print(f"  {'p_long':>8}{'night_prob':>12}{'在室率':>9}{'偏差':>8}"
          f"{'长途占比':>10}{'外出中位':>10}")
    print("  " + "-" * 58)

    best = None
    rows = []
    for p in GRID_P:
        for n in GRID_N:
            occ, lf, med = measure(p, n)
            dev = abs(occ - TARGET_OCC)
            rows.append({"p_long": p, "night_prob": n, "occ": occ,
                         "dev": dev, "long_frac": lf, "med": med})
            # 综合评分：在室率偏差为主，长途占比偏离 10% 为次
            score = dev + 0.3 * abs(lf - 0.10)
            mark = ""
            if best is None or score < best[0]:
                best = (score, p, n, occ, lf, med)
                mark = "  <-"
            print(f"  {p:>8.2f}{n:>12.2f}{occ:>9.1%}{dev:>8.1%}"
                  f"{lf:>10.0%}{med:>10.0f}{mark}")
    print()
    score, p, n, occ, lf, med = best
    print(f"  最优：p_long_before_10={p:.2f}, night_long_prob={n:.2f}")
    print(f"        在室率 {occ:.1%}（目标 {TARGET_OCC:.1%}，偏差 {abs(occ-TARGET_OCC):.1%}）")
    print(f"        长途占比 {lf:.0%}（目标 10%）")
    print(f"        外出中位时长 {med:.0f} 分钟（实测 21 分钟）")
    print()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"best": {"p_long_before_10": p, "night_long_prob": n,
                            "occ_rate": occ, "long_frac": lf,
                            "away_median_min": med},
                   "grid": rows,
                   "target": {"occ_rate": TARGET_OCC, "long_frac": 0.10,
                              "away_median_min": 21}}, f,
                  ensure_ascii=False, indent=2)
    print(f"  结果已保存: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
