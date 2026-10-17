# -*- coding: utf-8 -*-
"""能耗权重细扫 + 强化学习与规则的帕累托权威对比。

目的
----
前一轮验证只测了 energy_scale ∈ {10, 30, 60, 100} 四个点，且规则基线
曾因 `ac_idx` 语义误解而出错。本脚本做两件事：

  1. **细扫**：补测 20 / 25 / 40 / 50，与已有的 10 / 30 / 60 / 100 合并，
     得到完整的权重—性能曲线，确定最佳权重区间。
  2. **权威对比**：用**正确的**规则实现（`ac_idx_for`，与
     `evaluate_all.py` 一致）做设定温度扫描，得到规则自身的帕累托前沿；
     再与各权重的 RL 前沿比较，给出：
        · 等舒适度电量对比（前沿插值）
        · 逐点支配关系（不依赖插值，最硬的论证）
        · 规则的能力空洞区间，以及 RL 是否填补

口径说明
--------
评估使用默认 RewardConfig（`tou_enabled=False`、`price_flat_tariff=1.0`），
因此"电费"数值等于电量（kWh），比较的是**物理能耗**。
规则与 RL 使用完全相同的环境与种子序列。

输出
----
docs/能耗权重细扫与帕累托对比.md
models/energy_scale_fine/results.json
"""

import json
import os
import sys
import time
from datetime import datetime
from statistics import mean, pstdev

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

import numpy as np                                     # noqa: E402
from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from stable_baselines3 import PPO                      # noqa: E402
from stable_baselines3.common.monitor import Monitor   # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

# gen4：只复用 gen4 自己目录下的模型；旧代次目录不再参与复用
OLD_DIR = os.path.join(PROJ, "models", "energy_scale_gen4_old")
OUT_DIR = os.path.join(PROJ, "models", "energy_scale_gen4")
OUT_MD = os.path.join(PROJ, "docs", "能耗权重细扫与帕累托对比.md")

AC_OFF = 0
NEW_SCALES = [20, 25, 40, 50]
ALL_SCALES = [10, 20, 25, 30, 40, 50, 60, 100]
SEEDS = [42, 66, 2026, 7, 123]
RULE_SETPOINTS = [22, 23, 24, 25, 26, 27, 28, 29]
TIMESTEPS = 400_000
EVAL_EPISODES = 12
SEED0 = 5000


def ac_idx_for(target_c, cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


def make_rule(target_c):
    def thermostat(env):
        t = env.get_obs_dict()["temp_in"]
        if t > target_c + 0.5:
            return ac_idx_for(target_c, env.act_cfg)
        if t < target_c - 0.5:
            return AC_OFF
        return env.sim.state.ac_idx

    def fn(env):
        obs = env.get_obs_dict()
        if not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        light = 1 if obs["light_in"] < 300 else 0
        return encode_action(thermostat(env), light, 0, env.act_cfg)
    return fn


def evaluate(action_fn, rew_cfg=None, n=EVAL_EPISODES, seed0=SEED0):
    E, C, D, S = [], [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rew_cfg or RewardConfig(),
                           seed=seed0 + ep)
        env.reset(seed=seed0 + ep)
        cost, steps = 0.0, 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                D.append(float(m.get("discomfort_ratio", 0.0)))
                S.append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {"energy": mean(E), "energy_sd": pstdev(E) if len(E) > 1 else 0.0,
            "discomfort": mean(D), "cost": mean(C), "switch": mean(S),
            "n": len(E)}


def train_or_load(scale, seed):
    """优先复用旧目录里已训练的模型，否则训练并保存。"""
    for d in (os.path.join(OLD_DIR, f"es{scale}_s{seed}"),
              os.path.join(OUT_DIR, f"es{scale}_s{seed}")):
        ck = os.path.join(d, "final_model.zip")
        if os.path.exists(ck):
            return ck
    d = os.path.join(OUT_DIR, f"es{scale}_s{seed}")
    os.makedirs(d, exist_ok=True)
    ck = os.path.join(d, "final_model.zip")

    def mk():
        rc = RewardConfig(weight_mode="scenario")
        rc.energy_scale = float(scale)
        return Monitor(SmartHomeEnv(rew_cfg=rc, seed=seed))
    venv = DummyVecEnv([mk])
    model = PPO("MlpPolicy", venv, verbose=0, seed=seed, n_steps=2048,
                batch_size=256, learning_rate=3e-4, gamma=0.99,
                gae_lambda=0.95, ent_coef=0.01, device="cpu")
    model.learn(total_timesteps=TIMESTEPS)
    model.save(ck)
    venv.close()
    return ck


def eval_model(scale, seed, ck):
    model = PPO.load(ck, device="cpu")
    rc = RewardConfig(weight_mode="scenario")
    rc.energy_scale = float(scale)

    def fn(env):
        v = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(v, deterministic=True)[0])
    return evaluate(fn, rew_cfg=rc)


def pareto(points, key_d="discomfort", key_e="energy"):
    """返回按不达标率升序的帕累托前沿（电量随不达标率单调下降）。"""
    pts = sorted(points, key=lambda p: p[key_d])
    front, best = [], float("inf")
    for p in pts:
        if p[key_e] < best:
            best = p[key_e]
            front.append(p)
    return front


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print("=" * 88)
    print(" 能耗权重细扫 + 帕累托权威对比")
    print("=" * 88)
    print()

    # ---------- 1. 规则前沿 ----------
    print("=== 规则基线：设定温度扫描 ===")
    print(f"  {'设定':>6}{'电量kWh':>10}{'不达标':>9}{'切换/天':>9}")
    print("  " + "-" * 36)
    rule_pts = []
    for sp in RULE_SETPOINTS:
        r = evaluate(make_rule(float(sp)))
        r["setpoint"] = sp
        rule_pts.append(r)
        print(f"  {sp:>5}°C{r['energy']:>10.3f}{r['discomfort']:>9.1%}"
              f"{r['switch']:>9.1f}")
    rule_front = pareto(rule_pts)
    print()
    print("  规则帕累托前沿：")
    for p in rule_front:
        print(f"    {p['setpoint']}°C  不达标 {p['discomfort']:.1%}  "
              f"电量 {p['energy']:.3f}")
    rule_ds = sorted(p["discomfort"] for p in rule_front)
    gaps = [(rule_ds[i], rule_ds[i + 1]) for i in range(len(rule_ds) - 1)
            if rule_ds[i + 1] - rule_ds[i] > 0.03]
    print("  规则的能力空洞（相邻前沿点间隔 > 3 个百分点）：")
    for a, b in gaps:
        print(f"    {a:.1%} ~ {b:.1%}  （宽 {b-a:.1%}）")
    print()

    # ---------- 2. RL 各权重 ----------
    print("=== 强化学习：能耗权重细扫（5 种子）===")
    print(f"  {'权重':>6}{'电量kWh/天':>16}{'不达标':>10}{'切换/天':>10}")
    print("  " + "-" * 44)
    rl = {}
    for es in ALL_SCALES:
        rs = []
        for seed in SEEDS:
            ck = train_or_load(es, seed)
            rs.append(eval_model(es, seed, ck))
        rl[es] = {
            "energy": mean(x["energy"] for x in rs),
            "energy_sd": pstdev([x["energy"] for x in rs]),
            "discomfort": mean(x["discomfort"] for x in rs),
            "switch": mean(x["switch"] for x in rs),
            "n": len(rs),
            "seeds": [{"energy": x["energy"], "discomfort": x["discomfort"]}
                      for x in rs],
        }
        s = rl[es]
        print(f"  {es:>6}{s['energy']:>9.3f}±{s['energy_sd']:<6.3f}"
              f"{s['discomfort']:>10.1%}{s['switch']:>10.1f}")
    print()

    # ---------- 3. 等舒适度对比 ----------
    print("=" * 88)
    print(" 等舒适度对比（规则前沿插值到 RL 的不达标率处）")
    print("=" * 88)
    print(f"  {'权重':>6}{'RL不达标':>10}{'RL电量':>10}"
          f"{'规则同舒适度':>14}{'RL优势':>10}")
    print("  " + "-" * 52)
    rf = sorted(rule_front, key=lambda p: p["discomfort"])
    eq = []
    for es in ALL_SCALES:
        d = rl[es]["discomfort"]
        e = rl[es]["energy"]
        lo = max([p for p in rf if p["discomfort"] <= d],
                 key=lambda p: p["discomfort"], default=None)
        hi = min([p for p in rf if p["discomfort"] >= d],
                 key=lambda p: p["discomfort"], default=None)
        if lo and hi and hi["discomfort"] > lo["discomfort"]:
            w = (d - lo["discomfort"]) / (hi["discomfort"] - lo["discomfort"])
            re_ = lo["energy"] + w * (hi["energy"] - lo["energy"])
        elif lo:
            re_ = lo["energy"]
        elif hi:
            re_ = hi["energy"]
        else:
            continue
        adv = (re_ - e) / re_
        eq.append({"scale": es, "rl_d": d, "rl_e": e, "rule_e": re_, "adv": adv})
        print(f"  {es:>6}{d:>10.1%}{e:>10.3f}{re_:>14.3f}{adv:>+10.1%}")
    print()

    # ---------- 4. 逐点支配 ----------
    print("=" * 88)
    print(" 逐点支配分析（不依赖插值，最硬的论证）")
    print("=" * 88)
    dom = []
    for es in ALL_SCALES:
        de, ee = rl[es]["discomfort"], rl[es]["energy"]
        beat = [p for p in rule_pts
                if de <= p["discomfort"] and ee <= p["energy"]
                and (de < p["discomfort"] or ee < p["energy"])]
        for p in beat:
            dom.append({"scale": es, "rl_d": de, "rl_e": ee,
                        "rule_setpoint": p["setpoint"],
                        "rule_d": p["discomfort"], "rule_e": p["energy"]})
        if beat:
            sps = ", ".join(f"{p['setpoint']}°C" for p in beat)
            print(f"  RL es={es:<4}（{de:.1%}, {ee:.3f}）支配规则设定点：{sps}")
    if not dom:
        print("  无支配关系")
    print()

    # ---------- 5. 最佳权重 ----------
    wins = [r for r in eq if r["adv"] > 0]
    best = max(eq, key=lambda r: r["adv"]) if eq else None
    print("=" * 88)
    print(" 结论")
    print("=" * 88)
    if best:
        print(f"  等舒适度优势最大的权重：energy_scale = {best['scale']}"
              f"（省 {best['adv']:.1%}）")
    print(f"  等舒适度下优于规则的权重：{sorted(r['scale'] for r in wins)}")
    print(f"  等舒适度下劣于规则的权重："
          f"{sorted(r['scale'] for r in eq if r['adv'] <= 0)}")

    # ---------- 输出 ----------
    out = {"rule_points": rule_pts, "rule_front": rule_front,
           "rule_gaps": gaps, "rl": {str(k): v for k, v in rl.items()},
           "equal_comfort": eq, "dominated": dom}
    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 能耗权重细扫与帕累托对比", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          f"- 种子：{SEEDS}（5 个），每种子 {TIMESTEPS:,} 步，"
          f"评估 {EVAL_EPISODES} 回合",
          "- 口径：默认 RewardConfig（未启用分时电价），故电费数值 = 电量(kWh)",
          "- 规则实现已核对 `ac_idx` 语义（0=关闭、1~5=25~29 °C）", "",
          "## 一、规则基线的设定温度扫描", "",
          "| 设定温度 | 电量(kWh/天) | 不达标 | 切换(次/天) | 是否在帕累托前沿 |",
          "|---|---|---|---|---|"]
    front_sp = {p["setpoint"] for p in rule_front}
    for p in rule_pts:
        md.append(f"| {p['setpoint']} °C | {p['energy']:.3f} | "
                  f"{p['discomfort']:.1%} | {p['switch']:.1f} | "
                  f"{'✅' if p['setpoint'] in front_sp else '—'} |")
    md += ["", f"**规则的能力空洞**（相邻前沿点间隔 > 3 个百分点）：", ""]
    for a, b in gaps:
        md.append(f"- 不达标率 **{a:.1%} ~ {b:.1%}**（宽 {b-a:.1%}）")
    md += ["", "## 二、强化学习能耗权重细扫（5 种子均值）", "",
           "| energy_scale | 电量(kWh/天) | 不达标 | 切换(次/天) |",
           "|---|---|---|---|"]
    for es in ALL_SCALES:
        s = rl[es]
        md.append(f"| {es} | {s['energy']:.3f} ± {s['energy_sd']:.3f} | "
                  f"{s['discomfort']:.1%} | {s['switch']:.1f} |")
    md += ["", "## 三、等舒适度对比", "",
           "把规则前沿线性插值到 RL 所在的不达标率处：", "",
           "| energy_scale | RL 不达标 | RL 电量 | 规则同舒适度电量 | RL 优势 |",
           "|---|---|---|---|---|"]
    for r in eq:
        md.append(f"| {r['scale']} | {r['rl_d']:.1%} | {r['rl_e']:.3f} | "
                  f"{r['rule_e']:.3f} | **{r['adv']:+.1%}** |")
    md += ["", "## 四、逐点支配分析（不依赖插值）", "",
           "| RL 权重 | RL 不达标 | RL 电量 | 被支配的规则设定点 |",
           "|---|---|---|---|"]
    by_scale = {}
    for d in dom:
        by_scale.setdefault(d["scale"], []).append(d)
    for es in ALL_SCALES:
        if es not in by_scale:
            continue
        items = by_scale[es]
        sps = ", ".join(f"{i['rule_setpoint']}°C" for i in items)
        md.append(f"| {es} | {items[0]['rl_d']:.1%} | {items[0]['rl_e']:.3f} | "
                  f"{sps} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print()
    print(f"  总耗时 {(time.time()-t0)/60:.1f} 分钟")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
