r"""
环境自检脚本 —— 验证 MDP 环境实现是否正确

检查内容：
    1. 接口正确性：reset / step 返回格式是否符合 Gymnasium 规范
    2. 观测合法性：所有观测是否落在声明的空间边界内
    3. 动力学合理性：空调是否真能降温、温度是否连续
    4. 奖励合理性：奖励数量级是否适合训练、各项代价是否在起作用
    5. 约束有效性：空调最小切换间隔是否被真正执行
    6. 指标可算性：文档 5.3 节的三类指标能否正常产出

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate
    python scripts\test_env.py
"""

import sys
from pathlib import Path

# 让脚本能导入 src 包
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import numpy as np

from src.env import SmartHomeEnv, SimConfig, decode_action, encode_action
from src.env.config import ActionConfig

# 空调档位索引由配置推导（2026-10-17：档位由 [25..29] 扩展为 [23..29]，
# 写死的 1/3 已不再表示 25/28 °C）。
_AC = ActionConfig()
ac_25 = _AC.ac_setpoints.index(25.0) + 1
ac_27 = _AC.ac_setpoints.index(27.0) + 1


PASS = "[通过]"
FAIL = "[失败]"
WARN = "[注意]"


def section(title: str) -> None:
    print("\n" + "=" * 62)
    print(f" {title}")
    print("=" * 62)


def check_interface(env: SmartHomeEnv) -> bool:
    """检查 1：接口格式。"""
    section("检查 1：接口正确性")
    ok = True

    obs, info = env.reset(seed=42)
    # 观测维度取决于是否启用储能（v4）：
    #   未启用储能 -> 25 维（与 v1~v3 模型兼容）
    #   启用储能   -> 27 维（额外含 battery_soc / battery_power）
    # 因此这里用 active_obs_names() 的长度，而不是固定的 OBS_DIM。
    n_obs_expected = len(env.sim.active_obs_names())
    print(f"  reset() 返回观测类型: {type(obs).__name__}, "
          f"形状: {obs.shape}, 数据类型: {obs.dtype}")
    print(f"  期望观测维度: {n_obs_expected}"
          f"（储能{'已' if getattr(env.rew_cfg, 'battery_enabled', False) else '未'}启用）")
    if obs.shape != (n_obs_expected,):
        print(f"  {FAIL} 观测维度应为 {n_obs_expected}，实际 {obs.shape}")
        ok = False
    if obs.shape != env.observation_space.shape:
        print(f"  {FAIL} 观测形状与 observation_space 不一致："
              f"{obs.shape} vs {env.observation_space.shape}")
        ok = False
    if obs.dtype != np.float32:
        print(f"  {FAIL} 观测数据类型应为 float32，实际 {obs.dtype}")
        ok = False

    result = env.step(0)
    if len(result) != 5:
        print(f"  {FAIL} step() 应返回 5 个值，实际 {len(result)}")
        ok = False
    else:
        o, r, term, trunc, inf = result
        print(f"  step() 返回: 观测{o.shape}, 奖励{type(r).__name__}, "
              f"终止{term}, 截断{trunc}, 信息字典({len(inf)} 项)")
        if not isinstance(r, float):
            print(f"  {WARN} 奖励不是 float 类型")

    print(f"  观测空间: {env.observation_space.shape}")
    print(f"  动作空间: {env.action_space}  (共 {env.action_space.n} 个动作)")
    print(f"  回合长度: {env.episode_steps} 步 = "
          f"{env.episode_steps / env.sim_cfg.steps_per_day:.1f} 天")

    print(f"  {PASS if ok else FAIL} 接口检查")
    return ok


def check_observation_bounds(env: SmartHomeEnv, n_steps: int = 2000) -> bool:
    """检查 2：观测是否始终在声明范围内。"""
    section("检查 2：观测合法性（随机策略跑 2000 步）")
    env.reset(seed=1)
    low = env.observation_space.low
    high = env.observation_space.high
    # 观测维度动态取决于是否启用储能（v4），这里以 observation_space 为准
    n_obs = int(env.observation_space.shape[0])

    violations = []
    obs_min = np.full(n_obs, np.inf)
    obs_max = np.full(n_obs, -np.inf)

    for _ in range(n_steps):
        a = env.action_space.sample()
        obs, _, term, trunc, _ = env.step(a)
        obs_min = np.minimum(obs_min, obs)
        obs_max = np.maximum(obs_max, obs)
        bad = np.where((obs < low - 1e-6) | (obs > high + 1e-6))[0]
        for i in bad:
            violations.append((i, env.obs_names[i], float(obs[i])))

    names = env.obs_names
    print(f"  {'特征名':<14}{'最小值':>12}{'最大值':>12}   {'边界':>22}")
    print("  " + "-" * 62)
    for i, name in enumerate(names):
        bound = f"[{low[i]:.0f}, {high[i]:.0f}]"
        print(f"  {name:<14}{obs_min[i]:>12.2f}{obs_max[i]:>12.2f}   {bound:>22}")

    if violations:
        print(f"\n  {FAIL} 发现 {len(violations)} 处越界，前 5 处：")
        for i, name, val in violations[:5]:
            print(f"      {name} = {val}")
        return False

    print(f"\n  {PASS} 所有观测均在声明范围内")
    return True


def check_dynamics(env: SmartHomeEnv) -> bool:
    """检查 3：动力学是否合理（空调能否降温）。"""
    section("检查 3：动力学合理性")
    ok = True

    # --- 实验 A：持续开空调 vs 不开空调 ---
    def run_days(ac_idx: int, steps: int = 32):
        env.reset(seed=7)
        temps = []
        for _ in range(steps):
            a = encode_action(ac_idx, 0, 0, env.act_cfg)
            obs, _, _, _, _ = env.step(a)
            temps.append(float(obs[0]))
        return temps

    t_no_ac = run_days(ac_idx=0)          # 全程关空调
    t_ac24 = run_days(ac_idx=ac_25)         # 全程开空调设定 25°C
    t_ac28 = run_days(ac_idx=ac_27)         # 全程开空调设定 27°C

    print("  实验 A：连续 8 小时（32 步）不同空调策略的室温变化")
    print(f"    不开空调      : 起点 {t_no_ac[0]:.2f}°C -> 终点 {t_no_ac[-1]:.2f}°C")
    print(f"    空调设定 24°C : 起点 {t_ac24[0]:.2f}°C -> 终点 {t_ac24[-1]:.2f}°C")
    print(f"    空调设定 28°C : 起点 {t_ac28[0]:.2f}°C -> 终点 {t_ac28[-1]:.2f}°C")

    if t_ac24[-1] >= t_no_ac[-1]:
        print(f"  {FAIL} 开空调后室温未低于不开空调，热模型可能有误")
        ok = False
    else:
        cool_effect = t_no_ac[-1] - t_ac24[-1]
        print(f"    开空调相对不开空调降温幅度: {cool_effect:.2f}°C")

    # 设定温度越低应越凉
    if not (t_ac24[-1] <= t_ac28[-1] + 0.01):
        print(f"  {WARN} 设定 24°C 的室温未低于设定 28°C，检查功率模型")
    else:
        print(f"    设定温度关系正确: 24°C档({t_ac24[-1]:.2f}) <= 28°C档({t_ac28[-1]:.2f})")

    # --- 实验 B：温度连续性（无跳变） ---
    env.reset(seed=11)
    prev = float(env.get_obs_dict()["temp_in"])
    max_jump = 0.0
    for _ in range(500):
        obs, _, _, _, _ = env.step(env.action_space.sample())
        cur = float(obs[0])
        max_jump = max(max_jump, abs(cur - prev))
        prev = cur
    print(f"\n  实验 B：500 步内单步最大温度跳变 = {max_jump:.3f}°C")
    if max_jump > 3.0:
        print(f"  {WARN} 单步跳变较大，检查热模型系数")
    else:
        print(f"  {PASS} 温度变化连续，无异常跳变")

    print(f"  {PASS if ok else FAIL} 动力学检查")
    return ok


def check_reward(env: SmartHomeEnv, n_steps: int = 3000) -> bool:
    """检查 4：奖励数量级与多目标权衡是否真正生效。"""
    section("检查 4：奖励合理性与多目标平衡")
    env.reset(seed=3)
    rewards, comfort_parts, energy_parts, switch_parts, lambdas = [], [], [], [], []

    for _ in range(n_steps):
        a = env.action_space.sample()
        obs, r, _, _, info = env.step(a)
        c = info["components"]
        rewards.append(r)
        comfort_parts.append(c["comfort_total"])
        energy_parts.append(c["energy_term"])
        switch_parts.append(c["cost_switch"])
        lambdas.append(c["lambda_energy"])

    rewards = np.array(rewards)
    print(f"  奖励统计（{n_steps} 步随机策略）:")
    print(f"    均值 {rewards.mean():10.4f}   标准差 {rewards.std():10.4f}")
    print(f"    最小 {rewards.min():10.4f}   最大 {rewards.max():10.4f}")

    mc, me, ms = (np.mean(comfort_parts), np.mean(energy_parts),
                  np.mean(switch_parts))
    print(f"\n  各分项均值（这是判断多目标是否平衡的关键）:")
    print(f"    舒适度代价      {mc:10.4f}")
    print(f"    能耗代价项      {me:10.4f}")
    print(f"    切换惩罚        {ms:10.4f}")

    ok = True
    if abs(rewards.mean()) > 200:
        print(f"  {WARN} 奖励绝对值偏大，可能影响训练稳定性")
    if rewards.std() < 1e-6:
        print(f"  {FAIL} 奖励几乎没有变化，奖励函数可能失效")
        ok = False

    # ---- 关键检查：能耗项是否与舒适度项处于可比量级 ----
    # 若能耗项被淹没，λ_e 无论如何调整都无法影响策略，多目标机制失效
    if mc > 1e-9:
        ratio = me / mc
        print(f"\n  能耗项 / 舒适度项 = {ratio:.4f}")
        if ratio < 0.02:
            print(f"  {FAIL} 能耗项被舒适度项淹没（比值 < 0.02），"
                  f"多目标权衡无法生效")
            print(f"         请调大 RewardConfig.energy_scale")
            ok = False
        elif ratio > 5.0:
            print(f"  {WARN} 能耗项远大于舒适度项，舒适度可能被牺牲")
        else:
            print(f"  {PASS} 两个目标处于可比量级，多目标权衡可以生效")

    # ---- 检查场景化权重是否在变化 ----
    uniq = sorted(set(round(x, 4) for x in lambdas))
    print(f"\n  能耗权重 λ_e 出现过的取值: {uniq}")
    if len(uniq) <= 1:
        print(f"  {FAIL} λ_e 始终不变，场景化权重机制未生效")
        ok = False
    else:
        print(f"  {PASS} 场景化权重生效，共 {len(uniq)} 种取值")

    print(f"  {PASS if ok else FAIL} 奖励检查")
    return ok


def check_constraints(env: SmartHomeEnv) -> bool:
    """检查 5：约束有效性。

    说明：空调最小切换间隔设定为 10 分钟，而时间步长为 15 分钟，
    因此该约束在当前设置下是"天然满足"的（每步都超过 10 分钟）。
    本检查验证的是：
        (1) 约束逻辑不会误拦截正常动作
        (2) 当间隔要求大于一个时间步时，屏蔽机制确实生效
    """
    section("检查 5：约束有效性")
    dt = env.sim_cfg.dt_minutes
    interval = env.con_cfg.ac_min_switch_interval
    print(f"  时间步长 = {dt} 分钟，约束要求的最小间隔 = {interval} 分钟")
    if interval <= dt:
        print(f"  说明：约束间隔不超过一个时间步，属于天然满足（每步间隔 = {dt} 分钟）")
    else:
        print(f"  说明：约束间隔大于一个时间步，屏蔽机制应当生效")

    # ---- 场景 A：放松约束，验证屏蔽机制本身有效 ----
    print(f"\n  场景 A：临时把约束设为 60 分钟，检验屏蔽机制")
    saved = env.con_cfg.ac_min_switch_interval
    env.con_cfg.ac_min_switch_interval = 60
    env.reset(seed=5)

    changes, last_change = [], None
    ac_prev = env.sim.state.ac_idx
    blocked_total = 0
    for step in range(400):
        # 每步都请求切换空调，测试屏蔽是否拦截
        target = 1 if step % 2 == 0 else 2
        a = encode_action(target, 0, 0, env.act_cfg)
        _, _, _, _, info = env.step(a)
        if info.get("ac_blocked"):
            blocked_total += 1
        ac_now = env.sim.state.ac_idx
        if ac_now != ac_prev:
            if last_change is not None:
                changes.append(step - last_change)
            last_change = step
            ac_prev = ac_now

    env.con_cfg.ac_min_switch_interval = saved

    interval_steps = 60 / dt          # = 4 步
    print(f"    被屏蔽的步数: {blocked_total}")
    print(f"    实际发生切换: {len(changes)} 次")
    if changes:
        print(f"    两次切换的最小间隔: {min(changes)} 步 "
              f"（要求 >= {interval_steps:.0f} 步）")
        print(f"    间隔序列前 12 个: {changes[:12]}")
        small = [(i, g) for i, g in enumerate(changes) if g < interval_steps - 0.5]
        print(f"    间隔不足的样本: {len(small)} 个")
        if small:
            print(f"      前 5 个: {small[:5]}")

    ok = True
    if blocked_total == 0:
        print(f"  {FAIL} 屏蔽机制未生效：持续请求切换却一次都没被拦截")
        ok = False
    elif changes and min(changes) < interval_steps - 0.5:
        print(f"  {FAIL} 存在间隔不足的切换，屏蔽逻辑有误")
        ok = False
    else:
        print(f"  {PASS} 约束屏蔽机制正确工作")

    print(f"  {PASS if ok else FAIL} 约束检查")
    return ok


def check_metrics(env: SmartHomeEnv) -> bool:
    """检查 6：文档 5.3 节三类指标能否产出。"""
    section("检查 6：评价指标产出（跑完一个完整回合）")
    env.reset(seed=99)
    total_reward = 0.0
    while True:
        a = env.action_space.sample()
        obs, r, term, trunc, info = env.step(a)
        total_reward += r
        if term or trunc:
            break

    m = info.get("episode_metrics", {})
    print(f"  回合总步数: {m.get('total_steps')} "
          f"({m.get('total_steps', 0) / env.sim_cfg.steps_per_day:.1f} 天)")
    print(f"  回合累计奖励: {total_reward:.3f}")
    print("\n  (1) 能耗类指标")
    print(f"      总能耗            : {m.get('total_energy_kwh', 0):.3f} kWh")
    print(f"      日均能耗          : {m.get('energy_per_day_kwh', 0):.3f} kWh/天")
    print("  (2) 舒适度类指标")
    print(f"      不达标时长占比    : {m.get('discomfort_ratio', 0):.2%}")
    print(f"      温度偏差 RMSE     : {m.get('temp_dev_rmse', 0):.3f} °C")
    print("  (3) 响应效率类指标")
    print(f"      平均响应延迟      : {m.get('response_delay_mean', 0):.2f} 步")
    print(f"      平均恢复时间      : {m.get('recovery_time_mean', 0):.2f} 步")
    print(f"      状态切换总次数    : {m.get('switch_count', 0)}")
    print(f"      日均切换次数      : {m.get('switch_per_day', 0):.2f} 次/天")

    missing = [k for k in ["total_energy_kwh", "discomfort_ratio",
                           "response_delay_mean", "switch_count"]
               if k not in m]
    if missing:
        print(f"  {FAIL} 缺失指标: {missing}")
        return False
    print(f"\n  {PASS} 三类指标均能正常产出")
    return True


def show_trajectory(env: SmartHomeEnv, days: int = 3) -> None:
    """打印一段温度轨迹，直观检查环境行为。"""
    section(f"附：{days} 天温度轨迹（随机策略，每分钟一格）")
    env.reset(seed=2026)
    steps = days * env.sim_cfg.steps_per_day

    temps, occs, acs, tlabels = [], [], [], []
    for _ in range(steps):
        a = env.action_space.sample()
        obs, _, _, _, _ = env.step(a)
        o = env.get_obs_dict()
        temps.append(float(o["temp_in"]))
        occs.append(1 if (o["occ_a"] or o["occ_b"]) else 0)
        acs.append(int(o["ac_idx"]))
        tlabels.append(env.sim.sim_time)

    # 按小时聚合，每天打印 24 个点
    print(f"  {'时间':<14}{'温度°C':>8}  {'人':>3}  {'空调':>5}   温度曲线")
    print("  " + "-" * 70)
    for i in range(0, steps, env.sim_cfg.steps_per_day // 8):
        t = temps[i]
        bar = "#" * int(max(0, min(40, (t - 15) * 2)))
        print(f"  {tlabels[i]:%m-%d %H:%M}  {t:>8.1f}  {occs[i]:>3}  {acs[i]:>5}   {bar}")

    print(f"\n  温度范围: {min(temps):.1f} ~ {max(temps):.1f} °C")
    print(f"  有人占比: {sum(occs) / len(occs):.1%}")


def main():
    print("=" * 62)
    print(" MDP 环境自检 —— 智能家居自适应决策系统")
    print("=" * 62)

    env = SmartHomeEnv(seed=42)
    results = {}

    results["1 接口正确性"] = check_interface(env)
    results["2 观测合法性"] = check_observation_bounds(env)
    results["3 动力学合理性"] = check_dynamics(env)
    results["4 奖励合理性"] = check_reward(env)
    results["5 约束有效性"] = check_constraints(env)
    results["6 指标产出"] = check_metrics(env)

    show_trajectory(env, days=2)

    section("自检汇总")
    all_ok = True
    for name, ok in results.items():
        mark = PASS if ok else FAIL
        print(f"  {mark} {name}")
        all_ok = all_ok and ok

    print()
    if all_ok:
        print("  全部检查通过，环境可用于强化学习训练。")
    else:
        print("  存在未通过项，请根据上面的提示排查后再继续。")
    print("=" * 62)

    env.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
