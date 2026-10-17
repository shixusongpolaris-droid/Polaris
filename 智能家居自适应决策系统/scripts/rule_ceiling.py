# -*- coding: utf-8 -*-
"""规则基线的能力上限探测。

问题
----
此前所有"RL 优于规则"的结论都基于一条比较朴素的规则：

    无人在房间 -> 关空调
    谷段 -> 25 °C   平段 -> 26 °C   峰段 -> 28 °C
    滞环 ±0.5 °C

这条规则没有用天气预报、不做预冷、参数也未调优。若用它当基线，
"RL 获胜"可能只是赢了一个随手写的规则，而不是赢过规则这种控制范式。

本脚本回答
----------
**把规则做到它的能力上限之后，RL（energy_scale=100）还赢吗？**

做法
----
构建一个规则族，对参数做网格搜索，取其帕累托前沿：

    R1 基础分时规则     谷/平/峰 三个设定温度
    R2 预冷规则         R1 + 若未来 1~2 小时内进入峰段，
                        则当前目标温度再降 precool 度
    R3 全参数网格       v_sp × f_sp × p_sp × precool

    v_sp      ∈ {23, 24, 25, 26}
    f_sp      ∈ {25, 26, 27}
    p_sp      ∈ {27, 28, 29}
    precool   ∈ {0, 1, 2}

共 230 组（分时族 216 + 常设定温度族 14），每组 12 回合。
取全部结果中的帕累托非支配点作为
"规则的能力上限"。

对比
----
与 `models/energy_scale_ci/results.json` 中已经训练好的
RL（energy_scale ∈ {10,30,60,100}，各 10 种子）对比。

判据
----
  · 逐点支配：某 RL 工作点在 (电费, 不达标率) 上同时不劣于且至少
    一项严格优于某个规则工作点
  · 前沿距离：RL 前沿上距离规则前沿最近的点，其电费是否更低

输出
----
docs/规则能力上限.md
models/rule_ceiling/results.json
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

from src.env import RewardConfig, SmartHomeEnv, encode_action  # noqa: E402
from src.env.config import ActionConfig                # noqa: E402

OUT_DIR = os.path.join(PROJ, "models", "rule_ceiling")
OUT_MD = os.path.join(PROJ, "docs", "规则能力上限.md")

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


def clampi(x, lo=23.0, hi=29.0):
    return max(lo, min(hi, x))


def make_rule(v_sp, f_sp, p_sp, precool, keep_on=False, pre_hours=2):
    """分时规则 + 可选预冷 + 可选"无人也不关机"。

    参数 keep_on：
        False（默认） 无人在房间时关闭空调 —— 覆盖"低电费高不舒适"区间
        True          无人在房间时仍维持温度 —— 覆盖"高电费低不舒适"区间
        两者都属于合理的规则族，缺任一段都会使对比对规则不利。
    """
    def fn(env):
        obs = env.get_obs_dict()
        if not keep_on and not (obs["occ_a"] or obs["occ_b"]):
            return encode_action(AC_OFF, 0, 0, env.act_cfg)
        rc = env.rew_cfg
        hour = int(obs["hour"])
        p_now = rc.get_price(hour)
        is_valley = p_now <= rc.price_valley + 1e-9
        is_peak = p_now >= rc.price_peak - 1e-9

        target = f_sp
        if is_valley:
            target = v_sp
        elif is_peak:
            target = p_sp
        # 预冷：未来 pre_hours 小时内将进入峰段
        if precool > 0 and not is_peak:
            fut = [rc.get_price((hour + k) % 24)
                   for k in range(1, pre_hours + 1)]
            if any(p >= rc.price_peak - 1e-9 for p in fut):
                target = target - precool
        target = clampi(target)

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


def evaluate(action_fn, n=EVAL_EPISODES):
    rc = RewardConfig(weight_mode="scenario", tou_enabled=True,
                      energy_scale=100.0, lookahead_weight=LOOKAHEAD)
    E, C, D, S = [], [], [], []
    for ep in range(n):
        env = SmartHomeEnv(rew_cfg=rc, seed=EVAL_SEED0 + ep)
        env.reset(seed=EVAL_SEED0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            steps += 1
            cost += info["components"]["elec_price"] * \
                info["components"]["energy_kwh"]
            if term or trunc:
                m = info["episode_metrics"]
                days = max(steps * env.sim_cfg.dt_minutes / 60 / 24, 1e-9)
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                D.append(float(m.get("discomfort_ratio", 0.0)))
                C.append(cost / days)
                S.append(float(m.get("switch_per_day", 0.0)))
                break
        env.close()
    return {"energy": mean(E), "cost": mean(C),
            "discomfort": mean(D), "switch": mean(S)}


def pareto(points, keys=("cost", "discomfort")):
    """返回非支配点（keys 中的两个目标都越小越好）。

    修正（2026-10-17，由团队复核成员 fix-reviewer 指出）：
    去重与排序原先写死按 (cost, discomfort)，当 keys=("energy",
    "discomfort") 时会用错键——本例恰好未触发（电量与电费的前沿点位
    不重合），但属于潜在错误。现改为按 keys 动态处理。
    """
    out = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            le = all(q[k] <= p[k] + 1e-12 for k in keys)
            lt = any(q[k] < p[k] - 1e-12 for k in keys)
            if le and lt:
                dominated = True
                break
        if not dominated:
            out.append(p)
    # 去重（按 keys 指定的目标值）
    seen, uniq = set(), []
    for p in sorted(out, key=lambda x: tuple(x[k] for k in keys)):
        k = tuple(round(p[kk], 6) for kk in keys)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("=" * 88)
    print(" 规则基线能力上限探测")
    print("=" * 88)
    print(f"  网格：谷 {[23,24,25,26]} × 平 {[25,26,27]} × 峰 {[27,28,29]} "
          f"× 预冷 {[0,1,2]} × 无人时 [关机, 不停机] = 216 组，"
          f"另加常设定温度族 7 档 × 2 种 = 14 组，合计 230 组")
    print(f"  每组 {EVAL_EPISODES} 回合；场景为分时电价")
    print()

    rows = []
    t = 0
    V = [23.0, 24.0, 25.0, 26.0]
    F = [25.0, 26.0, 27.0]
    P = [27.0, 28.0, 29.0]
    PC = [0.0, 1.0, 2.0]
    KO = [False, True]
    for keep in KO:
        for v in V:
            for f in F:
                for pk in P:
                    for pc in PC:
                        r = evaluate(make_rule(v, f, pk, pc, keep_on=keep))
                        r.update({"v": v, "f": f, "p": pk,
                                  "precool": pc, "keep_on": keep,
                                  "family": "分时"})
                        rows.append(r)
                        t += 1
    # ---- 常设定温度族（覆盖"低不舒适"区间）----
    # 上面的分时族把峰段设定温度限制在 27~29 °C，因此其舒适度下界
    # 只能到约 30%。但"全程开空调 s °C"同样是合法规则，且能达到
    # 0% 不舒适。缺少这一段会使对比对规则严重不利，故补上。
    for s in [23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]:
        for keep in [True, False]:
            r = evaluate(make_rule(s, s, s, 0.0, keep_on=keep))
            r.update({"v": s, "f": s, "p": s, "precool": 0.0,
                      "keep_on": keep, "family": "常设"})
            rows.append(r)
            t += 1
    print(f"  评估完成 {t} 组")

    # 注意口径（2026-10-17 修正，由团队审计成员 stats-reviewer 指出）：
    # 原先只输出按**电费**取的非支配点，命名为 front，容易被误解为
    # "更省电"的前沿。分时电价下电费与电量并不等价（把用电从峰段
    # 挪到谷段会抬高总电量而降低电费），因此必须同时给出两条前沿。
    front = pareto(rows, keys=("cost", "discomfort"))          # 电费前沿
    front_energy = pareto(rows, keys=("energy", "discomfort"))  # 电量前沿
    print()
    print("=== 规则的帕累托前沿（能力上限）===")
    print(f"  {'谷':>4}{'平':>4}{'峰':>4}{'预冷':>5}{'无人':>6}"
          f"{'电量kWh':>10}{'电费元':>9}{'不达标':>9}{'切换/天':>9}")
    print("  " + "-" * 68)
    for p in sorted(front, key=lambda x: x["discomfort"]):
        ko = '关机' if not p.get('keep_on') else '不停机'
        print(f"  {p['v']:>4.0f}{p['f']:>4.0f}{p['p']:>4.0f}{p['precool']:>5.0f}"
              f"{ko:>6}"
              f"{p['energy']:>10.3f}{p['cost']:>9.3f}"
              f"{p['discomfort']:>9.2%}{p['switch']:>9.1f}")
    print()

    # 与此前用的朴素规则对比
    base = next((r for r in rows if r["v"] == 25.0 and r["f"] == 26.0
                 and r["p"] == 28.0 and r["precool"] == 0.0), None)
    if base:
        print("=== 朴素规则（谷25/平26/峰28，无预冷）===")
        print(f"  电量 {base['energy']:.3f}  电费 {base['cost']:.3f}  "
              f"不达标 {base['discomfort']:.2%}")
        print()

    # 载入 RL 结果
    ci_path = os.path.join(PROJ, "models", "energy_scale_ci", "results.json")
    rl = []
    if os.path.exists(ci_path):
        with open(ci_path, encoding="utf-8") as fp:
            ci = json.load(fp)
        for sc, v in ci.get("rl", {}).items():
            rl.append({"scale": float(sc), "cost": v["cost"],
                       "discomfort": v["discomfort"],
                       "energy": v["energy"],
                       "cost_ci": v["cost_ci"],
                       "discomfort_ci": v["discomfort_ci"]})
        rl.sort(key=lambda x: x["scale"])
        print("=== RL 工作点（energy_scale_ci，10 种子均值）===")
        print(f"  {'es':>5}{'电量':>9}{'电费':>8}{'不达标':>9}")
        print("  " + "-" * 32)
        for r in rl:
            print(f"  {r['scale']:>5.0f}{r['energy']:>9.3f}"
                  f"{r['cost']:>8.3f}{r['discomfort']:>9.2%}")
        print()

    # 支配关系
    print("=" * 88)
    print(" 判据一：RL 工作点是否支配规则的帕累托前沿点")
    print("=" * 88)
    dom = []
    for r in rl:
        beat = [p for p in front
                if r["cost"] <= p["cost"] + 1e-9
                and r["discomfort"] <= p["discomfort"] + 1e-9
                and (r["cost"] < p["cost"] - 1e-9
                     or r["discomfort"] < p["discomfort"] - 1e-9)]
        dom.append({"scale": r["scale"], "beat": beat,
                    "n_beat": len(beat)})
        if beat:
            names = [f"谷{p['v']:.0f}/平{p['f']:.0f}/峰{p['p']:.0f}"
                     f"/预冷{p['precool']:.0f}" for p in beat[:3]]
            print(f"  RL es={r['scale']:<4.0f}（{r['cost']:.3f} 元, "
                  f"{r['discomfort']:.2%}）支配 {len(beat)} 个规则点 "
                  f"{names}")
        else:
            print(f"  RL es={r['scale']:<4.0f}（{r['cost']:.3f} 元, "
                  f"{r['discomfort']:.2%}）未支配任何规则点")
    print()

    # 规则前沿是否被 RL 完全覆盖
    print("=" * 88)
    print(" 判据二：规则前沿上有多少点不被任何 RL 点支配")
    print("=" * 88)
    unbeaten = []
    for p in front:
        covered = any(r["cost"] <= p["cost"] + 1e-9
                      and r["discomfort"] <= p["discomfort"] + 1e-9
                      and (r["cost"] < p["cost"] - 1e-9
                           or r["discomfort"] < p["discomfort"] - 1e-9)
                      for r in rl)
        if not covered:
            unbeaten.append(p)
    print(f"  规则前沿 {len(front)} 点，其中 {len(unbeaten)} 点不被 RL 覆盖")
    for p in unbeaten:
        print(f"    谷{p['v']:.0f}/平{p['f']:.0f}/峰{p['p']:.0f}"
              f"/预冷{p['precool']:.0f}  电费 {p['cost']:.3f}  "
              f"不达标 {p['discomfort']:.2%}")
    print()

    out = {"rows": rows, "front": front,
           "front_cost": front, "front_energy": front_energy,
           "base": base, "rl": rl, "dominance": dom,
           "unbeaten": unbeaten}
    with open(os.path.join(OUT_DIR, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    md = ["# 规则基线的能力上限探测", "",
          f"- 生成：{datetime.now():%Y-%m-%d %H:%M:%S}",
          "- 场景：启用分时电价（上海居民一户一表）",
          f"- 网格：谷 {[23,24,25]} × 平 {[25,26,27]} × 峰 {[27,28,29]} "
          f"× 预冷 {[0,1,2]} × 无人时 [关机,不停机] = 216 组，"
           f"另加常设定温度族 14 组，合计 230 组；"
           f"每组 {EVAL_EPISODES} 回合", "",
          "## 一、为什么要做这个实验", "",
          "此前所有\"RL 优于规则\"的结论都基于一条比较朴素的规则：",
          "",
          "```",
          "无人在房间 -> 关空调",
          "谷段 -> 25 °C   平段 -> 26 °C   峰段 -> 28 °C",
          "滞环 ±0.5 °C",
          "```", "",
          "这条规则没有用天气预报、不做预冷、参数也未调优。",
          "若用它当基线，\"RL 获胜\"可能只是赢了一个随手写的规则。", "",
          "本实验把规则族做参数网格搜索，取其帕累托非支配点作为"
          "\"规则的能力上限\"，再看 RL 是否仍然占优。", "",
          "## 二、规则族定义", "",
          "    R1 基础分时规则     谷/平/峰 三个设定温度",
          "    R2 预冷规则         R1 + 若未来 1~2 小时内进入峰段，",
          "                        则当前目标温度再降 precool 度",
          "", "## 三、规则的帕累托前沿（能力上限）", "",
          "| 谷 | 平 | 峰 | 预冷 | 无人时 | 电量(kWh/天) | 电费(元/天) | 不达标率 | 切换(次/天) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for p in sorted(front, key=lambda x: x["discomfort"]):
        ko = '关机' if not p.get('keep_on') else '不停机'
        md.append(f"| {p['v']:.0f} | {p['f']:.0f} | {p['p']:.0f} | "
                  f"{p['precool']:.0f} | {ko} | {p['energy']:.3f} | "
                  f"{p['cost']:.3f} | {p['discomfort']:.2%} | "
                  f"{p['switch']:.1f} |")
    md.append("")
    if base:
        md += ["## 四、此前的朴素规则", "",
               f"谷 25 / 平 26 / 峰 28、无预冷：电量 {base['energy']:.3f} kWh，"
               f"电费 {base['cost']:.3f} 元，不达标 {base['discomfort']:.2%}", ""]
    # 电量前沿单独列出（2026-10-17 加入，由团队审计成员 stats-reviewer 指出）：
    # 分时电价下把用电从峰段挪到谷段会抬高总电量而降低电费，
    # 因此"电费前沿"与"电量前沿"不是同一条，讨论"更省电"不能用前者代替。
    md += ["## 四点五、电量前沿（按电量取非支配点）", "",
           "> ⚠️ 分时电价下电费与电量不等价：把用电从峰段挪到谷段会**抬高",
           "> 总电量、降低电费**。因此下面这条前沿才是回答\"谁更省电\"的依据，",
           "> 上面第三节的 `front` 是按**电费**取的。", "",
           "| 谷 | 平 | 峰 | 预冷 | 无人时 | 电量(kWh/天) | 电费(元/天) | 不达标率 |",
           "|---|---|---|---|---|---|---|---|"]
    for p in sorted(front_energy, key=lambda x: x["energy"]):
        ko = "关机" if not p.get("keep_on") else "不停机"
        md.append(f"| {p['v']:.0f} | {p['f']:.0f} | {p['p']:.0f} | "
                  f"{p['precool']:.0f} | {ko} | {p['energy']:.3f} | "
                  f"{p['cost']:.3f} | {p['discomfort']:.2%} |")
    md.append("")
    md += ["## 五、RL 工作点（10 种子均值）", "",
           "| energy_scale | 电量(kWh/天) | 电费(元/天) | 不达标率 |",
           "|---|---|---|---|"]
    for r in rl:
        md.append(f"| {r['scale']:.0f} | {r['energy']:.3f} | "
                  f"{r['cost']:.3f} | {r['discomfort']:.2%} |")
    md += ["", "## 六、支配关系", "",
           "| energy_scale | 支配的规则前沿点数 |", "|---|---|"]
    for d in dom:
        md.append(f"| {d['scale']:.0f} | {d['n_beat']} |")
    md += ["", "## 七、规则前沿上不被 RL 覆盖的点", "",
           f"规则前沿共 {len(front)} 点，其中 **{len(unbeaten)} 点** "
           f"不被任何 RL 工作点支配。", ""]
    if unbeaten:
        md += ["| 谷 | 平 | 峰 | 预冷 | 电费 | 不达标率 |",
               "|---|---|---|---|---|---|"]
        for p in unbeaten:
            md.append(f"| {p['v']:.0f} | {p['f']:.0f} | {p['p']:.0f} | "
                      f"{p['precool']:.0f} | {p['cost']:.3f} | "
                      f"{p['discomfort']:.2%} |")
    md.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"  报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
