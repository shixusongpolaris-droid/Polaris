# -*- coding: utf-8 -*-
"""上海电价的交叉评估：策略 × 电价制度 的完整矩阵。

为什么需要这个脚本
------------------
`train.py` 的评估只给出"电量"，不给"电费"。而判断分时电价是否有价值，
必须看**电费** —— 分时电价下智能体会在谷段多制冷蓄冷，
总电量可能更高，但电费应当更低。只看电量会得出错误结论。

评估设计
--------
两个维度做交叉：

  策略（9 个）
    · 3 个规则基线：全程开空调25°C / 有人开无人关 / 规则控制(滞环)
    · 6 个训练好的 PPO：未分时×3档、分时×3档

  电价制度（6 种）
    · 未分时 × 三档（0.617 / 0.667 / 0.917 元/度）
    · 分时   × 三档（0.617/0.307、0.677/0.337、0.977/0.487）

每个 (策略, 电价) 组合跑若干回合，记录：
  · energy_per_day_kwh  日均电量
  · energy_cost_yuan    日均电费（按当步电价逐笔累加）
  · discomfort_ratio    不舒适占比
  · switch_per_day      日均切换次数

关键读数
--------
1. 「策略 × 自己的训练电价」：回答"这套控制方案实际花多少钱"
2. 「同一电价下不同策略」：回答"哪种策略在该电价下最优"
3. 「同一策略在不同电价」：回答"策略对电价的鲁棒性"

输出
----
models/tariff_cross_eval/summary.json
docs/上海电价交叉评估.md
"""

import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
# 必须把项目根目录（而非 src/）加入 sys.path，
# 这样 `from src.env import ...` 才能解析
sys.path.insert(0, PROJ)

import numpy as np                                    # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from stable_baselines3 import PPO                     # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "tariff_cross_eval")
OUT_JSON = os.path.join(OUT_DIR, "summary.json")
OUT_MD = os.path.join(PROJ, "docs", "上海电价交叉评估.md")

EPISODES = 10
SEED0 = 1000

# ---- 电价制度（与 train.py 的 TIER_TABLE 一致）----
TIER_TABLE = {
    1: (0.617, 0.617, 0.307),
    2: (0.667, 0.677, 0.337),
    3: (0.917, 0.977, 0.487),
}

TARIFFS = []
for t in (1, 2, 3):
    TARIFFS.append({"key": f"flat_t{t}", "label": f"未分时·{t}档",
                    "tou": False, "tier": t})
for t in (1, 2, 3):
    TARIFFS.append({"key": f"tou_t{t}", "label": f"分时·{t}档",
                    "tou": True, "tier": t})

# ---- 待评估的模型 ----
MODELS = []
for t in (1, 2, 3):
    MODELS.append({"key": f"ppo_flat_t{t}", "label": f"PPO(未分时·{t}档训练)",
                   "path": os.path.join(PROJ, "models", f"sh_flat_t{t}", "final_model.zip"),
                   "trained_on": f"flat_t{t}"})
for t in (1, 2, 3):
    MODELS.append({"key": f"ppo_tou_t{t}", "label": f"PPO(分时·{t}档训练)",
                   "path": os.path.join(PROJ, "models", f"sh_tou_t{t}", "final_model.zip"),
                   "trained_on": f"tou_t{t}"})


def make_rew_cfg(tariff):
    """按电价制度构造 RewardConfig。"""
    flat, peak, valley = TIER_TABLE[tariff["tier"]]
    rc = RewardConfig(weight_mode="scenario")
    if tariff["tou"]:
        rc.tou_enabled = True
        rc.price_peak = peak
        rc.price_valley = valley
        rc.price_flat = peak
        rc.peak_periods = [(6, 22)]
        rc.valley_periods = [(22, 6)]
    else:
        rc.tou_enabled = False
        rc.price_flat_tariff = flat
    return rc


# ============================================================
# 规则基线
# ============================================================
def ac_idx_for(target_c, cfg):
    """把目标温度映射为最接近的空调档位索引（0 = 关闭）。"""
    best, best_d = 0, 1e18
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def _thermostat(env, target, deadband=0.5):
    """带滞环的恒温器，返回空调档位索引。

    修正（2026-10-17）：原实现 `return 2` 表示"制冷"、`return 1` 表示
    "维持"，是在空调档位为 [25..29]（ac_idx 1..5）时写的。档位扩展为
    [23..29]（ac_idx 1..7）后，2 已变成 24 °C、1 已变成 23 °C——
    即"制冷"实际设到 24 °C，"维持"实际设到 23 °C，均与标注的 25 °C
    不符。现改为按目标温度动态推导。
    （由团队审计成员 script-auditor 发现）
    """
    T = env.sim.state.temp_in
    if T > target + deadband:
        return ac_idx_for(target, env.act_cfg)
    if T < target - deadband:
        return 0      # 关闭
    return env.sim.state.ac_idx   # 维持当前档位


def policy_always_ac(env):
    return encode_action(_thermostat(env, 25.0), 0, 0, env.act_cfg)


def policy_occ_only(env):
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    return encode_action(_thermostat(env, 25.0), 0, 0, env.act_cfg)


def policy_rule(env):
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(0, 0, 0, env.act_cfg)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(_thermostat(env, 25.0), light, 0, env.act_cfg)


BASELINES = [
    {"key": "base_always", "label": "全程开空调25°C", "fn": policy_always_ac},
    {"key": "base_occ", "label": "有人开无人关", "fn": policy_occ_only},
    {"key": "base_rule", "label": "规则控制(滞环)", "fn": policy_rule},
]


def make_model_fn(path):
    model = PPO.load(str(path), device="cpu")

    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(v, deterministic=True)[0])
    return fn


def evaluate(action_fn, tariff, n_episodes=EPISODES, seed0=SEED0):
    """跑若干回合，返回四项日均指标的均值与标准差。"""
    rc = make_rew_cfg(tariff)
    acc = {"energy": [], "cost": [], "discomfort": [], "switch": []}
    for ep in range(n_episodes):
        env = SmartHomeEnv(rew_cfg=rc, seed=seed0 + ep)
        env.reset(seed=seed0 + ep)
        cost_total = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost_total += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                acc["energy"].append(float(m.get("energy_per_day_kwh", 0.0)))
                acc["cost"].append(cost_total / days)
                acc["discomfort"].append(float(m.get("discomfort_ratio", 0.0)))
                acc["switch"].append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {k: (float(np.mean(v)), float(np.std(v))) for k, v in acc.items()}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # 组装全部策略
    policies = list(BASELINES)
    for m in MODELS:
        if not os.path.exists(m["path"]):
            print(f"  [跳过] 模型不存在: {m['path']}")
            continue
        policies.append({"key": m["key"], "label": m["label"],
                         "fn": make_model_fn(m["path"]),
                         "trained_on": m["trained_on"]})

    print(f"策略数 {len(policies)}，电价制度数 {len(TARIFFS)}")
    print(f"组合数 {len(policies) * len(TARIFFS)}，每组 {EPISODES} 回合\n")

    results = {}
    for p in policies:
        results[p["key"]] = {"label": p["label"],
                             "trained_on": p.get("trained_on"),
                             "by_tariff": {}}
        for t in TARIFFS:
            r = evaluate(p["fn"], t)
            results[p["key"]]["by_tariff"][t["key"]] = r
        # 进度
        own = p.get("trained_on")
        if own:
            r = results[p["key"]]["by_tariff"][own]
            print(f"  {p['label']:<22} 在自己训练的电价下: "
                  f"电量 {r['energy'][0]:.3f} kWh/天  "
                  f"电费 {r['cost'][0]:.3f} 元/天  "
                  f"不达标 {r['discomfort'][0]:.1%}")
        else:
            print(f"  {p['label']:<22} 已评估 {len(TARIFFS)} 种电价")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"tariffs": TARIFFS, "models": MODELS,
                   "episodes": EPISODES, "seed0": SEED0,
                   "results": results}, f, ensure_ascii=False, indent=2)

    # ================= 报告 =================
    md = ["# 上海电价交叉评估", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 每组合 {EPISODES} 回合，起始种子 {SEED0}",
          "",
          "## 为什么单看电量会误判",
          "",
          "分时电价下智能体会在谷段多制冷「蓄冷」，总电量可能反而更高，",
          "但电费更低。因此必须同时看**电量**与**电费**。",
          "",
          "## 电价制度", "",
          "| 制度 | 峰价 | 谷价 | 峰谷比 |",
          "|---|---|---|---|"]
    for t in TARIFFS:
        flat, peak, valley = TIER_TABLE[t["tier"]]
        if t["tou"]:
            md.append(f"| {t['label']} | {peak} | {valley} | {peak/valley:.2f} |")
        else:
            md.append(f"| {t['label']} | {flat} | {flat} | 1.00 |")
    md.append("")

    # 主表：每个策略在自己的训练电价下
    md += ["## 一、各策略在「自己训练的电价」下的表现", "",
           "| 策略 | 训练电价 | 电量(kWh/天) | 电费(元/天) | 不达标 | 切换(次/天) |",
           "|---|---|---|---|---|---|"]
    for p in policies:
        own = p.get("trained_on")
        if not own:
            continue
        r = results[p["key"]]["by_tariff"][own]
        tlabel = next(t["label"] for t in TARIFFS if t["key"] == own)
        md.append(f"| {p['label']} | {tlabel} | {r['energy'][0]:.3f} | "
                  f"{r['cost'][0]:.3f} | {r['discomfort'][0]:.1%} | {r['switch'][0]:.1f} |")
    md.append("")

    # 分时 vs 未分时：同档位下，各自训练的策略，比较电费
    md += ["## 二、选分时 vs 不选分时（同档位比较）", "",
           "把「未分时训练的策略在其电价下」与「分时训练的策略在其电价下」对照：",
           "",
           "| 档位 | 未分时 电量 | 未分时 电费 | 分时 电量 | 分时 电费 | 电费变化 |",
           "|---|---|---|---|---|---|"]
    for t in (1, 2, 3):
        kf, kt = f"ppo_flat_t{t}", f"ppo_tou_t{t}"
        if kf not in results or kt not in results:
            continue
        rf = results[kf]["by_tariff"][f"flat_t{t}"]
        rt = results[kt]["by_tariff"][f"tou_t{t}"]
        delta = (rt["cost"][0] - rf["cost"][0]) / rf["cost"][0]
        md.append(f"| 第{t}档 | {rf['energy'][0]:.3f} | {rf['cost'][0]:.3f} | "
                  f"{rt['energy'][0]:.3f} | {rt['cost'][0]:.3f} | **{delta:+.1%}** |")
    md.append("")

    # 交叉表：每个 PPO 在全部电价下的电费
    md += ["## 三、电费交叉矩阵（行为在训练时固定，仅更换计价制度）", "",
           "| 策略 \\ 电价 | " + " | ".join(t["label"] for t in TARIFFS) + " |",
           "|---" * (len(TARIFFS) + 1) + "|"]
    for p in policies:
        if not p.get("trained_on"):
            continue
        cells = []
        for t in TARIFFS:
            r = results[p["key"]]["by_tariff"][t["key"]]
            cells.append(f"{r['cost'][0]:.2f}")
        md.append(f"| {p['label']} | " + " | ".join(cells) + " |")
    md.append("")
    md.append("（表中数字为日均电费，单位：元/天）")
    md.append("")

    # 同电价下策略排名
    md += ["## 四、每种电价下的最优策略（按电费）", "",
           "| 电价制度 | 电费最低 | 电费 | 不达标 | 说明 |",
           "|---|---|---|---|---|"]
    for t in TARIFFS:
        best = None
        for p in policies:
            if not p.get("trained_on"):
                continue
            r = results[p["key"]]["by_tariff"][t["key"]]
            if best is None or r["cost"][0] < best[1]:
                best = (p["label"], r["cost"][0], r["discomfort"][0])
        if best:
            md.append(f"| {t['label']} | {best[0]} | {best[1]:.3f} | {best[2]:.1%} | |")
    md.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print()
    print(f"  结果已保存: {OUT_JSON}")
    print(f"  报告已保存: {OUT_MD}")

    # 终端打印核心结论
    print()
    print("=" * 88)
    print(" 核心结论：选分时 vs 不选分时（同档位）")
    print("=" * 88)
    for t in (1, 2, 3):
        kf, kt = f"ppo_flat_t{t}", f"ppo_tou_t{t}"
        if kf not in results or kt not in results:
            continue
        rf = results[kf]["by_tariff"][f"flat_t{t}"]
        rt = results[kt]["by_tariff"][f"tou_t{t}"]
        delta = (rt["cost"][0] - rf["cost"][0]) / rf["cost"][0]
        print(f"  第{t}档  未分时 {rf['cost'][0]:.3f} 元/天 (电量 {rf['energy'][0]:.3f})"
              f"   分时 {rt['cost'][0]:.3f} 元/天 (电量 {rt['energy'][0]:.3f})"
              f"   电费变化 {delta:+.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
