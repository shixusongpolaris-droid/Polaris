# -*- coding: utf-8 -*-
"""盲评独立复算工具箱（stats-reviewer / t3）。

设计原则：
  · 不复用项目里的 measure()/pareto()/dedupe()/汇总代码；
  · 规则族按 scripts/gen7_unified_protocol.py 的 build_specs()/build_rule() 构造；
  · 评估口径按该脚本文档所述协议独立实现（种子 9100~9107、8 回合、semi_markov、
    7 天/回合），并先与旧 results.json 做逐位复现校验，确认实现无误。
"""

import importlib.util
import json
import math
import os
import sys
from statistics import mean, stdev

REPO = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
OLD_ROOT = r"D:\Harness工作区\polaris_t3"
SPEC_PATH = os.path.join(REPO, "scripts", "gen7_unified_protocol.py")

EVAL_EPISODES = 8
EVAL_SEED0 = 9100

# 六个电价格（与 scripts/gen7_tariff_six.py 的 TARIFFS 一致）
TARIFFS = {
    "t1_flat": dict(tier=1, tou=False, flat=0.617, peak=0.617, valley=0.307),
    "t1_tou": dict(tier=1, tou=True, flat=0.617, peak=0.617, valley=0.307),
    "t2_flat": dict(tier=2, tou=False, flat=0.667, peak=0.667, valley=0.337),
    "t2_tou": dict(tier=2, tou=True, flat=0.667, peak=0.677, valley=0.337),
    "t3_flat": dict(tier=3, tou=False, flat=0.917, peak=0.917, valley=0.487),
    "t3_tou": dict(tier=3, tou=True, flat=0.917, peak=0.977, valley=0.487),
}

VARIANTS = {
    # 修正前（ce731b5）环境，用于复现校验与"旧世界"基准
    "old": dict(root=OLD_ROOT, c4=None, c3=None, patch=None),
    # 旧代码 + 仅替换能耗公式为 t3 口径（gross/COP，含最低功率平均底价）
    "old_newenergy": dict(root=OLD_ROOT, c4=None, c3=None, patch="floor"),
    # 旧代码 + 仅替换能耗公式为**最终**口径（gross/COP，无平均底价，启动损耗仅 gross>0）
    "final_energy": dict(root=OLD_ROOT, c4=None, c3=None, patch="final"),
    # HEAD + C4 关 + C3 关（只保留最终能量公式与 S9b 等改动）
    "final_noc4_noc3": dict(root=REPO, c4="off", c3=False, patch=None),
    # HEAD + C3 关（C4 默认 precool）：隔离 C3 的作用
    "final_c3off": dict(root=REPO, c4=None, c3=False, patch=None),
    # HEAD 默认（C3 开 + C4 precool）= 交付的最终模型
    "final": dict(root=REPO, c4=None, c3=None, patch=None),
    # 冻结模型 36ff8e8（预测式 C3 + C4 请求关机）。
    # 注意：t6 的 out/rules_final_*.json / models_final*.json 是 e9e5bb1 时代
    # 跑出的；HEAD 已前进，必须用独立名字重跑，不能覆盖。
    "freeze": dict(root=REPO, c4=None, c3=None, patch=None),
    "freeze_c3off": dict(root=REPO, c4=None, c3=False, patch=None),
}


def patch_energy(se, mode="final"):
    """在旧代码上把能耗公式换成修正口径（只在本次分析进程内生效）。

    不修改任何项目文件：仅在内存中改写 HomeSimulator 的两个方法。
      · _update_temperature：与旧实现完全相同的热更新，额外记下 AC 的
        gross cooling（旧实现内部算过但未返回）；
      · _step_energy_kwh：按 gross cooling / COP 计压缩机功率，风机功率单列。
    mode="floor"：t3 口径（保留 ac_min_power_ratio 平均底价），用于复现 t3；
    mode="final"：最终口径（无平均底价；启动损耗只在 gross>0 时计）。
    这样温度轨迹与旧实现逐位相同，唯一变化是"度数→kWh"的换算。
    """
    sim = se.HomeSimulator
    orig_update = sim._update_temperature

    def upd(self, ac_cooling_cmd, ac_on, vent_on):
        cfg = self.sim_cfg
        T = self.state.temp_in
        gross = 0.0
        if ac_on:
            demand = cfg.ac_coef * (T - ac_cooling_cmd)
            gross = min(max(demand, 0.0), cfg.ac_max_cooling)
        self._gross_cooling = gross
        return orig_update(self, ac_cooling_cmd, ac_on, vent_on)

    def energy(self, ac_idx, light_idx, vent_idx, cooling_delivered,
               ac_started=False):
        cfg = self.sim_cfg
        rcfg = self.rew_cfg
        dt_h = cfg.dt_minutes / 60.0
        gross = getattr(self, "_gross_cooling", 0.0)
        if ac_idx == 0:
            p_ac = rcfg.ac_standby_kw
        else:
            cooling_kw = rcfg.ac_cooling_capacity_kw * (
                gross / max(cfg.ac_max_cooling, 1e-6))
            if cooling_kw > 1e-9:
                p = cooling_kw / max(getattr(rcfg, "ac_cop", 3.0), 1e-6)
                if mode == "floor":
                    p = max(p, rcfg.ac_min_power_ratio * rcfg.ac_rated_kw)
                p = min(p, rcfg.ac_rated_kw)
            else:
                p = 0.0
            p_ac = p + getattr(rcfg, "ac_fan_kw", 0.04)
        p_light = rcfg.light_rated_kw * rcfg.light_power_ratio[light_idx]
        p_vent = rcfg.vent_rated_kw if vent_idx == 1 else 0.0
        e = (p_ac + p_light + p_vent) * dt_h
        if ac_started and (mode == "floor" or gross > 1e-9):
            e += rcfg.ac_startup_penalty_kwh
        return e

    sim._update_temperature = upd
    sim._step_energy_kwh = energy


def load_env(variant):
    cfg = VARIANTS[variant]
    root = cfg["root"]
    # 保证只从该 root 导入 src（不同 variant 必须用独立进程）
    sys.path.insert(0, root)
    import src.env as se  # noqa: E402
    if cfg.get("patch"):
        patch_energy(se, cfg["patch"])
    return se


def load_rules():
    spec = importlib.util.spec_from_file_location("_up_rules", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_rc(se, tag, energy_scale=30.0):
    d = TARIFFS[tag]
    rc = se.RewardConfig(weight_mode="scenario", tou_enabled=d["tou"],
                         energy_scale=energy_scale, lookahead_weight=0.6)
    rc.price_flat = d["flat"]
    rc.price_peak = d["peak"]
    rc.price_valley = d["valley"]
    if d["tou"]:
        rc.peak_periods = [(6, 22)]
        rc.valley_periods = [(22, 6)]
    else:
        rc.price_flat_tariff = d["flat"]
        rc.peak_periods = None
        rc.valley_periods = None
    return rc


def make_con(se, variant):
    cfg = VARIANTS[variant]
    kw = {}
    if cfg.get("c4") is not None:
        kw["c4_mode"] = cfg["c4"]
    if cfg.get("c3") is not None:
        kw["c3_enforce"] = cfg["c3"]
    return se.ConstraintConfig(**kw)


def _decode_ac(action, act_cfg):
    """独立解码动作中的空调档位（不依赖 decode_action 的返回顺序假设）。"""
    n_ac = act_cfg.ac_choices
    n_light = act_cfg.light_choices
    return int(action) // (n_light * 2)


def measure(se, rc, con, action_fn, tag="", name="", variant="",
            n=EVAL_EPISODES, seed0=None):
    """独立实现的评估循环。

    口径（与 gen7_unified_protocol.py 文档一致）：
      energy = mean_episodes(episode_metrics.energy_per_day_kwh)
      cost   = mean_episodes( sum(step elec_price * energy_kwh) / days )
      occ_discomfort = 有人且越界步数 / 有人步数
      discomfort     = 全部越界步数 / 全部步数
    额外记录：有人/无人分项电量、空调开机率、无人均温、C4 改写动作步数。
    """
    sc = se.SimConfig(occupancy_mode="semi_markov")
    E, C, D, OD = [], [], [], []
    tot = bad = occ_steps = occ_bad = 0
    e_occ = e_empty = e_total = 0.0
    on_occ = on_empty = 0
    tsum_occ = tsum_empty = 0.0
    c4_override = 0
    c3_override = 0
    c3_violation = 0
    ac_blocked_steps = 0
    gt30 = gt28 = 0
    tmax = -1e9
    tmax_occ = -1e9
    s0 = EVAL_SEED0 if seed0 is None else int(seed0)
    for ep in range(n):
        seed = s0 + ep
        env = se.SmartHomeEnv(rew_cfg=rc, sim_cfg=sc, con_cfg=con, seed=seed)
        env.reset(seed=seed)
        days = max(env.episode_steps * env.sim_cfg.dt_minutes / 60 / 24, 1e-9)
        cost = 0.0
        e_sum = 0.0
        ep_bad = ep_occ_bad = ep_steps = ep_occ_steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            od = env.get_obs_dict()
            T = float(od["temp_in"])
            occ = bool(od["occ_a"] or od["occ_b"])
            hour = int(od["hour"])
            e = float(info["components"]["energy_kwh"])
            price = float(info["components"]["elec_price"])
            e_sum += e
            e_total += e
            cost += price * e
            executed_ac = int(env.sim.state.ac_idx)
            if _decode_ac(a, env.act_cfg) != executed_ac:
                c4_override += 1
            if info.get("c3_override"):
                c3_override += 1
            if info.get("c3_violation"):
                c3_violation += 1
            if info.get("ac_blocked"):
                ac_blocked_steps += 1
            if T > 30.0:
                gt30 += 1
            if T > 28.0:
                gt28 += 1
            if T > tmax:
                tmax = T
            if occ and T > tmax_occ:
                tmax_occ = T
            if occ:
                occ_steps += 1
                ep_occ_steps += 1
                e_occ += e
                tsum_occ += T
                if executed_ac > 0:
                    on_occ += 1
                lo, hi = ((rc.temp_low_sleep, rc.temp_high_sleep)
                          if (hour >= rc.sleep_start_hour or hour < rc.sleep_end_hour)
                          else (rc.temp_low_occupied, rc.temp_high_occupied))
            else:
                e_empty += e
                tsum_empty += T
                if executed_ac > 0:
                    on_empty += 1
                lo, hi = rc.temp_low_empty, rc.temp_high_empty
            tot += 1
            ep_steps += 1
            if T > hi or T < lo:
                bad += 1
                ep_bad += 1
                if occ:
                    occ_bad += 1
                    ep_occ_bad += 1
            if term or trunc:
                m = info["episode_metrics"]
                E.append(float(m.get("energy_per_day_kwh", 0.0)))
                C.append(cost / days)
                D.append(ep_bad / max(ep_steps, 1))
                OD.append(ep_occ_bad / max(ep_occ_steps, 1))
                break
        env.close()
    n_ep = float(n)
    days_total = n_ep * 7.0
    return {
        "tag": tag, "name": name, "variant": variant,
        "energy": mean(E), "cost": mean(C),
        "discomfort": bad / max(tot, 1),
        "occ_discomfort": occ_bad / max(occ_steps, 1),
        "energy_all": E, "cost_all": C,
        "discomfort_all": D, "occ_discomfort_all": OD,
        "energy_frac_check": e_sum / 7.0,
        "e_occ_per_day": e_occ / days_total,
        "e_empty_per_day": e_empty / days_total,
        "empty_energy_share": (e_empty / e_total) if e_total > 0 else 0.0,
        "e_total": e_total,
        "ac_on_share_occ": on_occ / max(occ_steps, 1),
        "ac_on_share_empty": on_empty / max(tot - occ_steps, 1),
        "temp_mean_occ": tsum_occ / max(occ_steps, 1),
        "temp_mean_empty": tsum_empty / max(tot - occ_steps, 1),
        "c4_override_steps": c4_override,
        "c4_override_share": c4_override / max(tot, 1),
        "c3_override_steps": c3_override,
        "c3_override_share": c3_override / max(tot, 1),
        "c3_violation_steps": c3_violation,
        "c3_violation_share": c3_violation / max(tot, 1),
        "ac_blocked_steps": ac_blocked_steps,
        "ac_blocked_share": ac_blocked_steps / max(tot, 1),
        "share_gt30": gt30 / max(tot, 1),
        "share_gt28": gt28 / max(tot, 1),
        "temp_max": tmax,
        "temp_max_occ": tmax_occ,
        "n_steps": tot, "n_occ_steps": occ_steps,
        "n_episodes": n, "seed0": EVAL_SEED0,
    }


# ---------------- 独立实现的前沿/去重（不复用项目代码） ----------------
def pareto_front(rows, key_x="cost", key_y="occ_discomfort", eps=1e-9):
    """非支配集：两目标均为最小化（cost, occ_discomfort）。

    弱支配定义与项目 pareto() 一致，但这里是独立实现：
    只有当存在另一点在两个目标上都不差、且至少一个严格更好时才被剔除，
    因此完全并列的点会全部留在前沿里（后续 dedupe 时统计 n_tied）。
    """
    out = []
    for r in rows:
        dominated = False
        for o in rows:
            if o is r:
                continue
            if (o[key_x] <= r[key_x] + eps and o[key_y] <= r[key_y] + eps
                    and (o[key_x] < r[key_x] - eps
                         or o[key_y] < r[key_y] - eps)):
                dominated = True
                break
        if not dominated:
            out.append(r)
    return sorted(out, key=lambda r: (r[key_y], r[key_x]))


def dedupe_points(front):
    out = []
    for r in front:
        hit = None
        for u in out:
            if (abs(u["cost"] - r["cost"]) <= 1e-9
                    and abs(u["occ_discomfort"] - r["occ_discomfort"]) <= 1e-9):
                hit = u
                break
        if hit is not None:
            hit["n_tied"] += 1
            hit.setdefault("aliases", []).append(r["name"])
        else:
            u = dict(r)
            u["n_tied"] = 1
            u["aliases"] = []
            out.append(u)
    return out


def max_gap(points, key="occ_discomfort"):
    ds = sorted(p[key] for p in points)
    best = (0.0, 0.0, -1.0)
    for j in range(len(ds) - 1):
        w = ds[j + 1] - ds[j]
        if w > best[2]:
            best = (ds[j], ds[j + 1], w)
    return {"lo": best[0], "hi": best[1], "width": best[2]}


def welch_t(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    va = stdev(a) ** 2 / len(a)
    vb = stdev(b) ** 2 / len(b)
    se = math.sqrt(va + vb)
    if se <= 1e-12:
        return None
    return (mean(a) - mean(b)) / se


def save_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
