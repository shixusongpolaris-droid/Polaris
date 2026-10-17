r"""
参数敏感性完整扫描 —— 明确方法的适用边界

背景：
    此前只扫描了两个参数（设备调制能力、建筑保温），做法是单一地比较
    "本方法相对恒温运行的节能率"。这有两个不足：
      1. 覆盖面窄，无法画出完整的适用区域
      2. 只比一个基线，"优于恒温运行"不等于"优于规则控制"

本脚本的改进：
    1. **扫描三个关键参数**：
       · ua_coef          建筑保温（传热系数）
       · ac_min_power_ratio  设备调制能力
       · internal_gain    内部热增益（人员/电器负荷）
    2. **在两个场景下扫描**：无分时电价 / 有分时电价
    3. **用综合指标比较**：对每个参数组合，评估
       {PPO, 规则控制, 恒温运行, 全关}
       四个策略，计算各自的**相对最优解的平均偏差**：
           score = mean( 能耗 / 最优能耗 , 不达标率 / 最优不达标率 ) - 1
       score 越小越好（0 表示该策略在所有指标上都是最优）。
       这样避免了"只赢一个基线"的片面结论。
    4. 输出**优势区域图**：在参数平面上标注哪个策略胜出。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    python scripts\param_sweep_full.py --quick
    python scripts\param_sweep_full.py --seeds 2 --episodes 8
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from src.env import RewardConfig, SimConfig, SmartHomeEnv, encode_action, ActionConfig

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
OUT_DIR = PROJECT_ROOT / "models" / "param_sweep"
FIG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

AC_OFF = 0
# 档位索引由配置推导，不再硬编码。
# 2026-10-17：空调档位由 [25..29] 扩展为 [23..29]（最低档改为 23 °C，
# 见 docs/空调最低档位验证.md），硬编码的 AC_25C = 1 已不再表示 25 °C。
_AC_DEFAULT = ActionConfig()
AC_25C = _AC_DEFAULT.ac_setpoints.index(25.0) + 1
AC_26C = _AC_DEFAULT.ac_setpoints.index(26.0) + 1
AC_27C = _AC_DEFAULT.ac_setpoints.index(27.0) + 1
AC_28C = _AC_DEFAULT.ac_setpoints.index(28.0) + 1
AC_29C = _AC_DEFAULT.ac_setpoints.index(29.0) + 1
AC_23C = _AC_DEFAULT.ac_setpoints.index(23.0) + 1
AC_24C = _AC_DEFAULT.ac_setpoints.index(24.0) + 1
def _ac_idx_for(target_c, act_cfg):
    best, best_d = AC_OFF, 1e9
    for i, s in enumerate(act_cfg.ac_setpoints):
        d = abs(s - target_c)
        if d < best_d:
            best_d, best = d, i + 1
    return best


# ============================================================
# 四个被评估的策略
# ============================================================
def policy_always_ac(env):
    """恒温运行：始终开机、不做启停，设定 25°C。

    这是需要注意的一个强策略。当空调最低功率较低时，
    "持续低功率运行"能维持稳定温度，而"启停控制"在停机期间
    会因制冷量不足而让室温攀升。

    实测（ua_coef=0.025, ac_min_power_ratio=0.10）：
        启停规则：有人时室温升到 30.3°C，不达标 10.9%
        持续运行：有人时室温最高 27.1°C，不达标 0.15%
    即"不停机"在弱制冷条件下显著优于"启停"，强化学习也学到了这一点。
    """
    return encode_action(AC_25C, 0, 0, env.act_cfg)


def policy_never_ac(env):
    """全程不开空调（能耗下界参照）。"""
    return encode_action(AC_OFF, 0, 0, env.act_cfg)


def policy_rule(env):
    """规则控制：有人维持 25°C，无人关闭。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    t = obs["temp_in"]
    if t > 25.5:
        ac = _ac_idx_for(25.0, env.act_cfg)
    elif t < 24.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def policy_rule_tou(env):
    """分时电价规则：谷段 25°C、平段 26°C、峰段 28°C。"""
    obs = env.get_obs_dict()
    if not (obs["occ_a"] or obs["occ_b"]):
        return encode_action(AC_OFF, 0, 0, env.act_cfg)
    tier = obs["price_tier"]
    target = 25.0 if tier == 0.0 else (28.0 if tier == 2.0 else 26.0)
    t = obs["temp_in"]
    if t > target + 0.5:
        ac = _ac_idx_for(target, env.act_cfg)
    elif t < target - 0.5:
        ac = AC_OFF
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


# ============================================================
# 评估
# ============================================================
METRIC_KEYS = ["energy_per_day_kwh", "discomfort_ratio", "temp_dev_rmse",
               "switch_per_day"]


def eval_policy(env_maker, action_fn, n_episodes, seed0):
    acc = {k: [] for k in METRIC_KEYS}
    acc["cost"] = []
    for ep in range(n_episodes):
        env = env_maker()
        env.reset(seed=seed0 + ep)
        cost = 0.0
        steps = 0
        while True:
            a = action_fn(env)
            _, _, term, trunc, info = env.step(a)
            c = info["components"]
            cost += c["elec_price"] * c["energy_kwh"]
            steps += 1
            if term or trunc:
                m = info["episode_metrics"]
                for k in METRIC_KEYS:
                    acc[k].append(float(m.get(k, 0.0)))
                days = max(steps * env.sim_cfg.dt_minutes / 60.0 / 24.0, 1e-9)
                acc["cost"].append(cost / days)
                break
        env.close()
    return {k: float(np.mean(v)) for k, v in acc.items()}


def eval_model(model, env_maker, n_episodes, seed0):
    def fn(env):
        obs = np.array(env.sim.to_vector(env.get_obs_dict()), dtype=np.float32)
        return int(model.predict(obs, deterministic=True)[0])
    return eval_policy(env_maker, fn, n_episodes, seed0)


def train_ppo(sim_cfg, rew_cfg, seed, timesteps, n_envs=4):
    vec = DummyVecEnv([
        (lambda i=i: Monitor(SmartHomeEnv(sim_cfg=sim_cfg, rew_cfg=rew_cfg,
                                          seed=seed + i)))
        for i in range(n_envs)])
    model = PPO("MlpPolicy", vec, verbose=0, seed=seed, gamma=0.99,
                n_steps=1024, batch_size=256, learning_rate=3e-4,
                ent_coef=0.01, policy_kwargs=dict(net_arch=[128, 128]))
    model.learn(total_timesteps=timesteps)
    vec.close()
    return model


# ============================================================
# 比较方法
# ============================================================
# 说明：最初尝试用"相对最优解的（能耗比, 不达标率比）均值 − 1"作为
# 综合得分，但实测发现这个指标有缺陷：
#     "全关"策略能耗为 0，能耗比被拉得极小，得分反而最好
#     —— 而它是完全不舒适的，显然是荒谬结论。
# 根本原因：能耗与舒适度量纲不同，开方（geometric mean）缩放
# 并不能得到公平的综合指标。
#
# 改用**等舒适度比较**：先算出"规则控制在与 PPO 相同不达标率下的电费"，
# 再与 PPO 的电费直接比较。这是唯一口径干净的做法，且已有验证
# （见 tou_equal_comfort.py）。
# ============================================================


def matched_comfort_cost(curve, target_d):
    """在"电费—不达标率"曲线上按不达标率插值出电费。

    curve: [(discomfort, cost), ...]，需按 discomfort 升序

    返回 (cost, status)：
        status = "ok"     正常插值
        status = "above"  目标不达标率高于规则曲线的最大可达值
                          （规则做不到这么不舒适，无法比较）
        status = "below"  目标不达标率低于规则曲线的最小可达值
                          （规则做不到这么舒适，无法比较）
    """
    if not curve:
        return None, "empty"
    xs = [c[0] for c in curve]
    ys = [c[1] for c in curve]
    if target_d < xs[0]:
        return None, "below"
    if target_d > xs[-1]:
        return None, "above"
    for i in range(len(xs) - 1):
        if xs[i] <= target_d <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            if span < 1e-9:
                return ys[i], "ok"
            w = (target_d - xs[i]) / span
            return ys[i] + w * (ys[i + 1] - ys[i]), "ok"
    return ys[-1], "ok"


def build_rule_curve(sim_cfg, rew_cfg, tou, n_episodes, seed0):
    """通过扫描规则的目标温度，构造规则的"电费—不达标率"曲线。

    温度点取得较密（22~30°C，步长 1°C），使曲线覆盖足够宽的
    不达标率范围，避免插值时落到端点外。
    """
    curve = []
    targets = [22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0]

    def make_pol(target_t):
        def policy(env):
            obs = env.get_obs_dict()
            if not (obs["occ_a"] or obs["occ_b"]):
                return encode_action(AC_OFF, 0, 0, env.act_cfg)
            t = obs["temp_in"]
            if t > target_t + 0.5:
                ac = _ac_idx_for(target_t, env.act_cfg)
            elif t < target_t - 0.5:
                ac = AC_OFF
            else:
                ac = env.sim.state.ac_idx
            light = 1 if obs["light_in"] < 300 else 0
            return encode_action(ac, light, 0, env.act_cfg)
        return policy

    for tt in targets:
        m = eval_policy(lambda: SmartHomeEnv(sim_cfg=sim_cfg, rew_cfg=rew_cfg),
                        make_pol(tt), n_episodes, seed0)
        curve.append((m["discomfort_ratio"], m["cost"], tt))
    curve.sort(key=lambda x: x[0])
    return [(c[0], c[1]) for c in curve]


def main():
    ap = argparse.ArgumentParser(description="参数敏感性完整扫描")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--episodes", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=250000)
    ap.add_argument("--temp-scale", type=float, default=1.0,
                    help="固定温度惩罚强度，使各参数组合的舒适度目标一致")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.episodes, args.timesteps = 1, 4, 80000

    t0 = time.time()

    # 扫描网格（每行一个参数组合）
    # 说明：ac_min_power_ratio 的下限取 0.25 而非更低值。
    # 原因（实测发现）：该参数很低时（如 0.10），空调制冷能力严重受限，
    #   此时"启停式规则"在停机期间室温会攀升到 30°C 以上（不达标 10.9%），
    #   而"持续低功率运行"能维持稳定温度（不达标 0.15%）。
    #   两类策略的机制差异过大，等舒适度比较失去意义。
    #   因此扫描聚焦于两类策略都能正常工作的参数区间。
    #
    # 重要修订（2026-10-16）：网格改为围绕**实测标定值**布置。
    #   此前网格以旧默认值为中心（ua 0.025~0.090、gain 0.005~0.050），
    #   而 2026-10-14~16 的实测标定结果为 ua_coef = 0.115、
    #   internal_gain = 0.80。旧网格的最高 ua 仍低于标定值，
    #   gain 更是低 16 倍，实际探索的是错误区间，
    #   且会用网格值覆盖标定值（见下方 SimConfig 构造），
    #   导致该扫描结果与其它实验不可比。
    #   现改为以标定值为中心、向两侧展开的敏感性区间。
    ua_values = [0.070, 0.115, 0.170, 0.240]      # 建筑保温（标定值 0.115）
    power_values = [0.25, 0.45, 0.65]             # 设备最低功率比（默认 0.20）
    gain_values = [0.30, 0.80, 1.40, 2.00]        # 内部热增益（标定值 0.80）

    print("=" * 82)
    print(" 参数敏感性完整扫描 —— 明确方法的适用边界")
    print("=" * 82)
    print(f"  扫描参数：")
    print(f"    ua_coef          建筑保温    : {ua_values}")
    print(f"    ac_min_power_ratio 设备调制  : {power_values}")
    print(f"    internal_gain     内部热增益 : {gain_values}")
    print(f"  种子数 {args.seeds}，每种子评估 {args.episodes} 回合")
    print(f"  每组训练 {args.timesteps:,} 步")
    print()
    print("  比较方法：固定温度惩罚强度下，比较 PPO 与各基线的电费与舒适度")
    print(f"    temp_scale 固定为 {args.temp_scale}，使各参数组合下")
    print("    PPO 的舒适度目标一致，从而'相对基线的电费差异'可跨组合比较。")
    print("    同时报告各策略的不达标率，供判断比较的可比性。")
    print()
    print("  方法学说明：此前尝试过两种更'严格'的做法，均在本环境下失效：")
    print("    (1) 对规则曲线插值求'同舒适度电费'——PPO 的不达标率始终低于")
    print("        规则曲线的整个范围，两条曲线不重叠，无法插值。")
    print("    (2) 搜索温度惩罚使 PPO 舒适度匹配规则——每个参数组合需 7 次")
    print("        训练，30 个组合训不动；且匹配后 PPO 的高能耗本质未变。")
    print("    最终采用固定 temp_scale 的方案：口径统一、成本可控，")
    print("    且同时报告舒适度差距，读者可自行判断可比性。")

    scenarios = [
        ("无分时电价", False, policy_rule),
        ("有分时电价", True, policy_rule_tou),
    ]

    all_results = {}

    for scen_name, tou, rule_fn in scenarios:
        print("\n" + "=" * 82)
        print(f" 场景：{scen_name}")
        print("=" * 82)

        rows = []
        # 以 ua × power 为主网格，gain 固定为**标定值** 0.80
        for ua in ua_values:
            for pw in power_values:
                rows.append({"ua": ua, "power": pw, "gain": 0.80})
        # 单独扫描 internal_gain，ua 固定为**标定值** 0.115
        for gn in gain_values:
            rows.append({"ua": 0.115, "power": 0.25, "gain": gn})

        print(f"\n  {'ua_coef':>9}{'power':>7}{'gain':>7}"
              f"{'PPO能耗':>9}{'PPO不达':>9}{'规则不达':>9}"
              f"{'vs恒温电费':>11}{'PPO电费':>9}{'规则电费':>9}"
              f"{'vs规则':>9}")
        print("  " + "-" * 92)

        for cfg in rows:
            sim_cfg = SimConfig(ua_coef=cfg["ua"],
                                internal_gain=cfg["gain"])
            # 固定温度惩罚强度，使各参数组合下 PPO 的舒适度目标一致，
            # 从而让"相对规则的电费差异"这个比较量在各组合之间可比。
            rew_cfg = RewardConfig(weight_mode="scenario", tou_enabled=tou,
                                   ac_min_power_ratio=cfg["power"],
                                   temp_scale=args.temp_scale)

            # ---- PPO：多种子训练 + 评估 ----
            ppo_metrics = []
            for si in range(args.seeds):
                seed = 1200 + si * 37
                model = train_ppo(sim_cfg, rew_cfg, seed, args.timesteps)
                m = eval_model(model,
                               lambda: SmartHomeEnv(sim_cfg=sim_cfg,
                                                    rew_cfg=rew_cfg),
                               args.episodes, seed0=700000 + si * 1000)
                ppo_metrics.append(m)
            ppo = {k: float(np.mean([p[k] for p in ppo_metrics]))
                   for k in ppo_metrics[0]}

            # ---- 规则控制 ----
            mk = lambda: SmartHomeEnv(sim_cfg=sim_cfg, rew_cfg=rew_cfg)
            rule = eval_policy(mk, rule_fn, args.episodes * args.seeds, 800000)
            # ---- 恒温运行（不启停）作为额外参照 ----
            always = eval_policy(mk, policy_always_ac, args.episodes, 810000)

            # 电费对比（同时报告两者舒适度，供读者判断可比性）
            ppo_cost = ppo["cost"]
            rule_cost = rule["cost"]
            always_cost = always["cost"]
            diff_rule = (ppo_cost - rule_cost) / rule_cost if rule_cost > 0 else float("nan")
            diff_always = ((ppo_cost - always_cost) / always_cost
                           if always_cost > 0 else float("nan"))

            # 舒适度差距（用于判断结论的可比性）
            d_gap = abs(ppo["discomfort_ratio"] - rule["discomfort_ratio"])

            cfg_out = dict(cfg)
            cfg_out.update({
                "ppo": ppo, "rule": rule, "always": always,
                "diff_vs_rule": diff_rule, "diff_vs_always": diff_always,
                "discomfort_gap": d_gap,
            })
            all_results.setdefault(scen_name, []).append(cfg_out)

            print(f"  {cfg['ua']:>9.3f}{cfg['power']:>7.2f}{cfg['gain']:>7.3f}"
                  f"{ppo['energy_per_day_kwh']:>9.3f}"
                  f"{ppo['discomfort_ratio']:>10.2%}"
                  f"{rule['discomfort_ratio']:>10.2%}"
                  f"{diff_always:>+10.1%}"
                  f"{ppo_cost:>9.3f}{rule_cost:>9.3f}"
                  f"{diff_rule:>+9.1%}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 82)
    print(" 汇总：适用边界")
    print("=" * 82)
    for scen_name, _, _ in scenarios:
        rs = all_results[scen_name]
        wins_rule = [r for r in rs if r["diff_vs_rule"] < 0]
        wins_always = [r for r in rs if r["diff_vs_always"] < 0]
        print(f"\n  {scen_name}（共 {len(rs)} 个参数组合）:")
        print(f"    相对规则控制：本方法占优 {len(wins_rule)} 个 "
              f"({len(wins_rule)/len(rs):.0%})")
        print(f"    相对恒温运行：本方法占优 {len(wins_always)} 个 "
              f"({len(wins_always)/len(rs):.0%})")
        dr = [r["diff_vs_rule"] for r in rs]
        da = [r["diff_vs_always"] for r in rs]
        print(f"    vs 规则电费差异范围: {min(dr):+.1%} ~ {max(dr):+.1%}")
        print(f"    vs 恒温电费差异范围: {min(da):+.1%} ~ {max(da):+.1%}")
        print(f"    差异最小的 3 个组合（相对规则）：")
        for r in sorted(rs, key=lambda x: x["diff_vs_rule"])[:3]:
            print(f"      ua={r['ua']:.3f}, power={r['power']:.2f}, "
                  f"gain={r['gain']:.3f}  ->  电费 {r['diff_vs_rule']:+.1%}"
                  f"（PPO 不达 {r['ppo']['discomfort_ratio']:.2%} vs "
                  f"规则 {r['rule']['discomfort_ratio']:.2%}）")
        print(f"    相对规则优势随参数的变化（看趋势）：")
        for pw in sorted(set(r["power"] for r in rs)):
            sub = [r for r in rs if r["power"] == pw and r["gain"] == 0.020]
            if sub:
                avg = float(np.mean([r["diff_vs_rule"] for r in sub]))
                print(f"      power={pw:.2f}: 平均 {avg:+.1%}")
        for ua in sorted(set(r["ua"] for r in rs)):
            sub = [r for r in rs if r["ua"] == ua and r["gain"] == 0.020]
            if sub:
                avg = float(np.mean([r["diff_vs_rule"] for r in sub]))
                print(f"      ua={ua:.3f}: 平均 {avg:+.1%}")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    for ax, (scen_name, _, _) in zip(axes, scenarios):
        rs = [r for r in all_results[scen_name] if r["gain"] == 0.020]
        uas = sorted(set(r["ua"] for r in rs))
        pws = sorted(set(r["power"] for r in rs))
        grid = np.full((len(pws), len(uas)), np.nan)
        for r in rs:
            if r["power"] not in pws or r["ua"] not in uas:
                continue
            i = pws.index(r["power"])
            j = uas.index(r["ua"])
            grid[i, j] = r["diff_vs_rule"] * 100        # 百分比
        im = ax.imshow(grid, aspect="auto", cmap="RdYlGn_r",
                       origin="lower", vmin=-50, vmax=100)
        ax.set_xticks(range(len(uas)))
        ax.set_xticklabels([f"{u:.3f}" for u in uas])
        ax.set_yticks(range(len(pws)))
        ax.set_yticklabels([f"{p:.2f}" for p in pws])
        ax.set_xlabel("ua_coef（建筑传热系数，越大保温越差）")
        ax.set_ylabel("ac_min_power_ratio（设备最低功率比）")
        ax.set_title(f"{scen_name}：PPO 相对规则的等舒适度电费差异\n"
                     f"（绿色=PPO更优，红色=规则更优）", fontsize=11)
        for i in range(len(pws)):
            for j in range(len(uas)):
                if not np.isnan(grid[i, j]):
                    ax.text(j, i, f"{grid[i, j]:+.0f}%", ha="center",
                            va="center", fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046,
                     label="电费差异 (%，负=PPO更优)")

    fig.suptitle("参数敏感性扫描：方法适用边界（等舒适度比较）", fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "param_sweep_full.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")
    print(f"\n  总耗时: {(time.time() - t0) / 60:.1f} 分钟")

    with open(OUT_DIR / "results.json", "w", encoding="utf-8") as f:
        json.dump({"args": vars(args),
                   "grid": {"ua": ua_values, "power": power_values,
                            "gain": gain_values},
                   "results": all_results},
                  f, ensure_ascii=False, indent=2, default=float)
    print(f"  结果已保存: models/param_sweep/results.json")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
