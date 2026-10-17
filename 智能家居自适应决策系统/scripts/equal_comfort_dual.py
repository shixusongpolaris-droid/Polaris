# -*- coding: utf-8 -*-
"""在"有人时段不舒适"口径下重做规则 vs RL 的等舒适度对比。

问题
----
此前的对比用的是 `discomfort_ratio`（全部步数的总不达标率）。
但舒适区间按占用状态分三段定义：

    无人 16~30     有人·睡眠 24~27     有人·清醒 22~26

因此总不达标率混杂了"没人在房间时过热"与"有人在房间时过热"两种
性质不同的事件。前者不代表有人在受罪。

上一版审计只对 4 个规则工作点算了有人时段占比，覆盖不全。本脚本对
**规则前沿的全部点 + 全开各档位 + RL 各权重** 统一计算：

    total_discomfort      总不达标率（此前所有实验用的口径）
    occ_discomfort        有人时段不达标率（更贴近真实体验）
    energy / cost

然后在这两个口径下分别做等舒适度对比，看结论是否一致。

对象
----
  · 规则前沿的全部 11 个点
  · 常设定温度族的全部 14 个变体（"全开"及"无人关机"）
  · RL energy_scale ∈ {10, 30, 60, 100}（energy_scale_ci 的 seed=42 模型）

输出
----
docs/等舒适度双口径对比.md
models/equal_comfort_dual.json
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

OUT_JSON = os.path.join(PROJ, "models", "equal_comfort_dual.json")
OUT_MD = os.path.join(PROJ, "docs", "等舒适度双口径对比.md")

EVAL_EPISODES = 12
EVAL_SEED0 = 9100
LOOKAHEAD = 0.6
AC_OFF = 0


def ac_idx_for(t, cfg):
    best, bd = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - t)
        if d < bd:
            bd, best = d, i + 1
    return best


def make_rule(v_sp, f_sp, p_sp, keep_on):
    def fn(env):
        obs = env.get_obs_dict()
        if not keep_on and not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        rc = env.rew_cfg
        hour = int(obs["hour"])
        p_now = rc.get_price(hour)
        if p_now <= rc.price_valley + 1e-9:
            target = v_sp
        elif p_now >= rc.price_peak - 1e-9:
            target = p_sp
        else:
            target = f_sp
        T = obs["temp_in"]
        if T > target + 0.5:
            ac = ac_idx_for(target, env.act_cfg)
        elif T < target - 0.5:
            ac = AC_OFF
        else:
            ac = env.sim.state.ac_idx
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(ac, light, 0, env.act_cfg)
    return fn


def rew_cfg():
    return RewardConfig(weight_mode="scenario", tou_enabled=True,
                        energy_scale=100.0, lookahead_weight=LOOKAHEAD)


def measure(action_fn, n=EVAL_EPISODES):
    rc = rew_cfg()
    E, C = [], []
    tot = bad = occ_steps = occ_bad = 0
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            if not occ:
                lo, hi = rc.temp_low_empty, rc.temp_high_empty
            else:
                occ_steps += 1
                if hour >= rc.sleep_start_hour or hour < rc.sleep_end_hour:
                    lo, hi = rc.temp_low_sleep, rc.temp_high_sleep
                else:
                    lo, hi = rc.temp_low_occupied, rc.temp_high_occupied
            tot += 1
            is_bad = (T > hi) or (T < lo)
            if is_bad:
                bad += 1
                if occ:
                    occ_bad += 1
            cost += info["components"]["elec_price"] * \
                info["components"]["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                days = max(env.episode_steps * env.sim_cfg.dt_minutes / 60 / 24,
                           1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                break
        env.close()
    return {"energy": mean(E), "cost": mean(C),
            "total_discomfort": bad / max(tot, 1),
            "occ_discomfort": occ_bad / max(occ_steps, 1),
            "occ_steps": occ_steps, "total_steps": tot}


def interp_cost(front, target, key):
    """在 front（按 key 升序）上线性插值电费。"""
    lo = max([p for p in front if p[key] <= target],
             key=lambda x: x[key], default=None)
    hi = min([p for p in front if p[key] >= target],
             key=lambda x: x[key], default=None)
    if lo and hi and hi[key] > lo[key]:
        w = (target - lo[key]) / (hi[key] - lo[key])
        return lo["cost"] + w * (hi["cost"] - lo["cost"])
    if lo:
        return lo["cost"]
    if hi:
        return hi["cost"]
    return None


def main():
    print("=" * 92)
    print(" 等舒适度双口径对比（总不达标 vs 有人时段不达标）")
    print("=" * 92)
    print()

    # 规则集：常设定温度族 + 分时前沿点
    rules = []
    for s in [23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]:
        for keep in [True, False]:
            r = measure(make_rule(s, s, s, keep))
            r.update({"label": f"常设{s:.0f}°C/{'不停机' if keep else '关机'}",
                      "s": s, "keep_on": keep, "family": "常设"})
            rules.append(r)
    FRONT_SPECS = [(25, 25, 25, False), (26, 26, 26, False),
                   (26, 25, 27, False), (26, 25, 28, False),
                   (26, 25, 29, False), (25, 25, 28, False),
                   (25, 25, 29, False), (29, 29, 29, False)]
    for (v, f, p, keep) in FRONT_SPECS:
        if (v, f, p) == (25, 25, 25) or (v, f, p) == (26, 26, 26):
            if not keep:
                continue
        r = measure(make_rule(v, f, p, keep))
        r.update({"label": f"谷{v}/平{f}/峰{p}/"
                          f"{'不停机' if keep else '关机'}",
                  "family": "分时"})
        rules.append(r)

    print("=== 规则候选 ===")
    print(f"  {'规则':<22}{'电量':>9}{'电费':>8}{'总不达标':>10}{'有人不达标':>11}")
    print("  " + "-" * 60)
    for r in rules:
        print(f"  {r['label']:<22}{r['energy']:>9.3f}{r['cost']:>8.3f}"
              f"{r['total_discomfort']:>10.2%}{r['occ_discomfort']:>11.2%}")
    print()

    rl = []
    for es in [10.0, 30.0, 60.0, 100.0]:
        ck = os.path.join(PROJ, "models", "energy_scale_ci",
                          f"es{int(es)}_s42", "final_model.zip")
        if not os.path.exists(ck):
            continue
        model = PPO.load(ck, device="cpu")

        def fn(env, _m=model):
            v = np.array(env.sim.to_vector(env.get_obs_dict()),
                         dtype=np.float32)
            return int(_m.predict(v, deterministic=True)[0])
        r = measure(fn)
        r["label"] = f"RL es={es:.0f}"
        r["scale"] = es
        rl.append(r)
    print("=== RL ===")
    print(f"  {'策略':<14}{'电量':>9}{'电费':>8}{'总不达标':>10}{'有人不达标':>11}")
    print("  " + "-" * 52)
    for r in rl:
        print(f"  {r['label']:<14}{r['energy']:>9.3f}{r['cost']:>8.3f}"
              f"{r['total_discomfort']:>10.2%}{r['occ_discomfort']:>11.2%}")
    print()

    for key, name in [("total_discomfort", "总不达标口径（此前所有实验）"),
                      ("occ_discomfort", "有人时段不达标口径（更贴近体验）")]:
        print("=" * 92)
        print(f" 等舒适度对比 —— {name}")
        print("=" * 92)
        front = sorted(rules, key=lambda x: x[key])
        # 只保留帕累托点
        pf = []
        best_cost = 1e9
        for p in front:
            if p["cost"] < best_cost - 1e-12:
                pf.append(p)
                best_cost = p["cost"]
        print(f"  {'策略':<14}{'不达标':>9}{'RL电费':>9}"
              f"{'规则同水平':>11}{'差值':>10}{'谁省':>8}")
        print("  " + "-" * 62)
        rows = []
        for r in rl:
            rc = interp_cost(pf, r[key], key)
            if rc is None:
                continue
            d = (r["cost"] - rc) / rc
            who = "规则" if d > 0 else "RL"
            rows.append({"scale": r["scale"], "rl": r["cost"],
                         "rule": rc, "diff": d, "who": who})
            print(f"  {r['label']:<14}{r[key]:>9.2%}{r['cost']:>9.3f}"
                  f"{rc:>11.3f}{d:>+10.1%}{who:>8}")
        print()

    out = {"rules": rules, "rl": rl}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 等舒适度双口径对比", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 评估 {EVAL_EPISODES} 回合，种子 {EVAL_SEED0}+",
          "- RL 使用 energy_scale_ci 的 seed=42 模型", "",
          "## 一、两个口径的区别", "",
          "舒适区间按占用状态分三段：", "",
          "```",
          "无人        16 ~ 30 °C",
          "有人·睡眠   24 ~ 27 °C",
          "有人·清醒   22 ~ 26 °C",
          "```", "",
          "`discomfort_ratio`（此前所有实验用的口径）把三段混在一起统计，",
          "因此包含了\"没人在房间时过热\"这种不代表有人在受罪的事件。",
          "本文同时给出**总不达标率**与**有人时段不达标率**两个口径，",
          "看结论是否一致。", "",
          "## 二、规则候选", "",
          "| 规则 | 电量(kWh/天) | 电费(元/天) | 总不达标 | 有人不达标 |",
          "|---|---|---|---|---|"]
    for r in rules:
        md.append(f"| {r['label']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['total_discomfort']:.2%} | {r['occ_discomfort']:.2%} |")
    md += ["", "## 三、RL 工作点", "",
           "| 策略 | 电量(kWh/天) | 电费(元/天) | 总不达标 | 有人不达标 |",
           "|---|---|---|---|---|"]
    for r in rl:
        md.append(f"| {r['label']} | {r['energy']:.3f} | {r['cost']:.3f} | "
                  f"{r['total_discomfort']:.2%} | {r['occ_discomfort']:.2%} |")
    md.append("")
    for key, name in [("total_discomfort", "总不达标口径"),
                      ("occ_discomfort", "有人时段不达标口径")]:
        front = sorted(rules, key=lambda x: x[key])
        pf, best_cost = [], 1e9
        for p in front:
            if p["cost"] < best_cost - 1e-12:
                pf.append(p)
                best_cost = p["cost"]
        md += [f"## 四、等舒适度对比 —— {name}", "",
               "| 策略 | 不达标率 | RL 电费 | 规则同水平电费 | 差值 | 谁更省 |",
               "|---|---|---|---|---|---|"]
        for r in rl:
            rc = interp_cost(pf, r[key], key)
            if rc is None:
                continue
            d = (r["cost"] - rc) / rc
            md.append(f"| {r['label']} | {r[key]:.2%} | {r['cost']:.3f} | "
                      f"{rc:.3f} | {d:+.1%} | "
                      f"{'规则' if d > 0 else 'RL'} |")
        md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
