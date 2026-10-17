# -*- coding: utf-8 -*-
"""不舒适率的状态分解审计。

为什么要做
----------
舒适区间按占用状态分段定义：

    无人        16 ~ 30 °C
    有人·睡眠   24 ~ 27 °C
    有人·清醒   22 ~ 26 °C

`discomfort_ratio` 统计的是"温度超出对应区间"的步占比。
因此一个策略的 12% 不达标，可能主要来自**无人时室温偏高**，
而不是有人在受罪。若两个策略的不达标发生在不同状态上，
直接比较不达标率就会得出误导性结论。

本脚本把每个策略的不达标按以下维度分解：

    · 状态：有人·清醒 / 有人·睡眠 / 无人
    · 方向：过热 / 过冷
    · 温度偏离的最大值与均值

并据此判断：谁的"不舒适"真正落在有人在房间的时段。

对象
----
    R1 规则（谷25/平25/峰25，无人关机）   —— 4.179 kWh / 12.03%
    R2 规则（谷25/平25/峰25，不停机）     —— 6.325 kWh /  0.15%
    RL energy_scale ∈ {10, 30, 60, 100}  —— 取 energy_scale_ci 的模型

输出
----
docs/不舒适率状态分解.md
models/discomfort_breakdown.json
"""

import json
import os
import sys
from datetime import datetime
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from src.env.config import ActionConfig                # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402

OUT_DIR = os.path.join(PROJ, "models")
OUT_JSON = os.path.join(OUT_DIR, "discomfort_breakdown.json")
OUT_MD = os.path.join(PROJ, "docs", "不舒适率状态分解.md")

EVAL_EPISODES = 12
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6
AC_OFF = 0
_AC = ActionConfig()


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(sp, keep_on):
    def fn(env):
        obs = env.get_obs_dict()
        if not keep_on and not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        T = obs["temp_in"]
        if T > sp + 0.5:
            ac = ac_idx_for(sp, env.act_cfg)
        elif T < sp - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def rew_cfg():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=100.0, lookahead_weight=LOOKAHEAD)


def breakdown(action_fn, n=EVAL_EPISODES):
    """统计不达标在各状态 / 方向上的分布。"""
    rc = rew_cfg()
    cfg = rc
    buckets = {
        "有人·清醒·过热": [], "有人·清醒·过冷": [],
        "有人·睡眠·过热": [], "有人·睡眠·过冷": [],
        "无人·过热": [], "无人·过冷": [],
    }
    tot_steps = 0
    bad_steps = 0
    temp_all = []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            temp_all.append(T)
            tot_steps += 1
            dev = float(info["components"].get("temp_dev", 0.0))
            if dev > 0:
                bad_steps += 1
            if not occ:
                lo, hi, st = cfg.temp_low_empty, cfg.temp_high_empty, "无人"
            else:
                is_sleep = (hour >= cfg.sleep_start_hour
                            or hour < cfg.sleep_end_hour)
                if is_sleep:
                    lo, hi, st = (cfg.temp_low_sleep, cfg.temp_high_sleep,
                                  "有人·睡眠")
                else:
                    lo, hi, st = (cfg.temp_low_occupied,
                                  cfg.temp_high_occupied, "有人·清醒")
            if T > hi:
                buckets[f"{st}·过热"].append(T - hi)
            elif T < lo:
                buckets[f"{st}·过冷"].append(lo - T)
            if term or trunc:
                break
        env.close()
    out = {"total_steps": tot_steps,
           "bad_steps": bad_steps,
           "discomfort": bad_steps / max(tot_steps, 1),
           "temp_mean": mean(temp_all),
           "temp_min": min(temp_all), "temp_max": max(temp_all),
           "buckets": {}}
    for k, v in buckets.items():
        out["buckets"][k] = {
            "steps": len(v),
            "share_of_all": len(v) / max(tot_steps, 1),
            "max_dev": max(v) if v else 0.0,
            "mean_dev": mean(v) if v else 0.0,
        }
    # 有人时段的不达标占比（最关键的指标）
    occ_bad = sum(out["buckets"][k]["steps"] for k in out["buckets"]
                  if k.startswith("有人"))
    out["occupied_bad_steps"] = occ_bad
    out["occupied_bad_ratio"] = occ_bad / max(tot_steps, 1)
    return out


def main():
    print("=" * 92)
    print(" 不舒适率的状态分解审计")
    print("=" * 92)
    print(f"  舒适区间：无人 16~30°C / 有人·睡眠 24~27°C / 有人·清醒 22~26°C")
    print(f"  评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}+")
    print()

    cases = []
    cases.append(("R1 规则(25,无人关机)", make_rule(25.0, False), None))
    cases.append(("R2 规则(25,不停机)", make_rule(25.0, True), None))
    for es in [10.0, 30.0, 60.0, 100.0]:
        ck = os.path.join(PROJ, "models", "energy_scale_ci",
                          f"es{int(es)}_s42", "final_model.zip")
        if not os.path.exists(ck):
            print(f"  跳过 es={es:.0f}：模型不存在")
            continue
        model = PPO.load(ck, device="cpu")

        def fn(env, _m=model):
            v = np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32)
            return int(_m.predict(v, deterministic=True)[0])
        cases.append((f"RL energy_scale={es:.0f}", fn, es))

    results = {}
    print(f"  {'策略':<22}{'总不达标':>10}{'其中有人':>10}{'无人':>9}"
          f"{'温度区间':>16}")
    print("  " + "-" * 70)
    for name, fn, es in cases:
        r = breakdown(fn)
        results[name] = r
        occ_share = sum(r["buckets"][k]["steps"] for k in r["buckets"]
                        if k.startswith("有人")) / max(r["total_steps"], 1)
        empty_share = sum(r["buckets"][k]["steps"] for k in r["buckets"]
                          if k.startswith("无人")) / max(r["total_steps"], 1)
        rng_txt = "{:.1f}~{:.1f}".format(r["temp_min"], r["temp_max"])
        print(f"  {name:<22}{r['discomfort']:>10.2%}{occ_share:>10.2%}"
              f"{empty_share:>9.2%}{rng_txt:>16}")
    print()
    print("  （'其中有人' = 有人在房间时段的不达标占全部步数的比例 —— 这是关键指标）")
    print()

    print("=" * 92)
    print(" 明细：各状态下的不达标步数")
    print("=" * 92)
    keys = ["有人·清醒·过热", "有人·清醒·过冷", "有人·睡眠·过热",
            "有人·睡眠·过冷", "无人·过热", "无人·过冷"]
    print(f"  {'策略':<22}" + "".join(f"{k.replace('有人·','').replace('无人·','无人'):>12}" for k in keys))
    print("  " + "-" * 94)
    for name, _, _ in cases:
        r = results[name]
        cells = ""
        for k in keys:
            v = r["buckets"][k]
            cells += f"{v['steps']:>7} ({v['max_dev']:.1f})"
        print(f"  {name:<22}{cells}")
    print()
    print("  括号内为该状态下温度偏离的最大值（°C）")
    print()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    md = ["# 不舒适率的状态分解审计", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}+",
          "- 场景：启用分时电价", "",
          "## 一、为什么要分解", "",
          "舒适区间按占用状态分段定义：", "",
          "```",
          "无人        16 ~ 30 °C",
          "有人·睡眠   24 ~ 27 °C   （23:00-07:00）",
          "有人·清醒   22 ~ 26 °C",
          "```", "",
          "`discomfort_ratio` 统计的是\"温度超出对应区间\"的步占比。",
          "因此一个策略的 12% 不达标，**可能主要来自无人时室温偏高**，",
          "而不是有人在受罪。若两个策略的不达标发生在不同状态上，",
          "直接比较不达标率就会得出误导性结论。", "",
          "## 二、总体对比", "",
          "| 策略 | 总不达标率 | 其中有人时段 | 其中无人时段 | 温度区间 |",
          "|---|---|---|---|---|"]
    for name, _, _ in cases:
        r = results[name]
        occ = sum(r["buckets"][k]["steps"] for k in r["buckets"]
                  if k.startswith("有人")) / max(r["total_steps"], 1)
        emp = sum(r["buckets"][k]["steps"] for k in r["buckets"]
                  if k.startswith("无人")) / max(r["total_steps"], 1)
        md.append(f"| {name} | {r['discomfort']:.2%} | {occ:.2%} | "
                  f"{emp:.2%} | {r['temp_min']:.1f}~{r['temp_max']:.1f} °C |")
    md += ["", "## 三、明细（不达标步数 / 该状态最大偏离）", "",
           "| 策略 | " + " | ".join(k for k in keys) + " |",
           "|---" * (len(keys) + 1) + "|"]
    for name, _, _ in cases:
        r = results[name]
        cells = [f"{r['buckets'][k]['steps']} / "
                 f"{r['buckets'][k]['max_dev']:.1f}°C" for k in keys]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
