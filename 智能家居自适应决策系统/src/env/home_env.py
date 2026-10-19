"""
Gymnasium 环境包装 —— 把仿真器包装成标准强化学习环境

职责：
    实现 Gymnasium 标准接口（reset / step / render / close），
    使 stable-baselines3 能直接使用本环境，无需额外适配层。

分工说明：
    simulator.py  负责状态转移（物理演变）
    reward.py     负责奖励计算
    home_env.py   只负责接口包装与数据流转

对应文档：
    docs/MDP定义_v1.0.md 第 1 节（状态空间）、第 2 节（动作空间）、
    第 3 节（奖励）、第 3.4 节（约束）、第 7 节（实现清单）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .config import ActionConfig, ConstraintConfig, RewardConfig, SimConfig
from .reward import RewardCalculator
from .simulator import HomeSimulator


class SmartHomeEnv(gym.Env):
    """智能家居环境自适应控制环境。

    观测空间：Box，22 维连续/离散混合特征（统一转为 float32）
    动作空间：Discrete(24)，即 (空调4档) × (照明3档) × (通风2档)

    使用示例：
        env = SmartHomeEnv(seed=42)
        obs, info = env.reset()
        for _ in range(100):
            action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)

    与 stable-baselines3 配合：
        model = PPO("MlpPolicy", env, verbose=1)
        model.learn(total_timesteps=100_000)
    """

    metadata = {"render_modes": ["human", "ansi"], "render_fps": 4}

    def __init__(
        self,
        sim_cfg: Optional[SimConfig] = None,
        act_cfg: Optional[ActionConfig] = None,
        con_cfg: Optional[ConstraintConfig] = None,
        rew_cfg: Optional[RewardConfig] = None,
        episode_steps: Optional[int] = None,
        start_datetime: Optional[datetime] = None,
        seed: Optional[int] = None,
        render_mode: Optional[str] = None,
    ):
        """
        参数：
            episode_steps   单个回合的步数。默认 7 天 = 7 * 96 = 672 步。
                            设为一个较长周期，使策略能感知"周内不同日"的差异，
                            这也是本项目"时间场景化"机制能生效的前提。
            start_datetime  回合起始时刻，用于指定星期几
            seed            随机种子
        """
        super().__init__()

        self.sim_cfg = sim_cfg or SimConfig()
        self.act_cfg = act_cfg or ActionConfig()
        self.con_cfg = con_cfg or ConstraintConfig()
        self.rew_cfg = rew_cfg or RewardConfig()

        self.episode_steps = episode_steps or (7 * self.sim_cfg.steps_per_day)
        # S9 修复：原默认 2026-01-05（周一）但室外参数是 10 月；改为
        # 2026-10-12（同为周一），让日历与"10 月供冷季"口径一致。
        # 周内序列不变，只改变日期标签，不影响只依赖 hour/dow 的特征。
        self.start_datetime = start_datetime or datetime(2026, 10, 12, 0, 0)
        self.render_mode = render_mode

        # ---- 构造组件 ----
        self.sim = HomeSimulator(self.sim_cfg, self.act_cfg,
                                 self.con_cfg, self.rew_cfg, seed=seed)
        self.reward_calc = RewardCalculator(self.rew_cfg, self.sim_cfg,
                                            self.act_cfg)

        # ---- 定义空间 ----
        self.action_space = spaces.Discrete(self.act_cfg.n_actions)
        self.observation_space = spaces.Box(
            low=np.array(self._obs_low(), dtype=np.float32),
            high=np.array(self._obs_high(), dtype=np.float32),
            dtype=np.float32,
        )

        # ---- 运行状态 ----
        self._step_in_episode = 0
        self._last_obs: Optional[Dict[str, Any]] = None
        self._episode_rewards: list = []

    # ------------------------------------------------------------
    # 观测空间边界
    # ------------------------------------------------------------
    def _obs_low(self) -> list:
        """观测下界。取值给出宽松但有限的边界，覆盖所有可能的策略行为。"""
        low = [
            -10.0,    # temp_in
            0.0,      # hum_in
            0.0,      # light_in
            0.0, 0.0, 0.0,          # occ_a, occ_b, occ_dist
            0.0, 0.0, 0.0, 0.0,     # hour, dow, is_weekend, is_holiday
            -1.0, -1.0,             # hour_sin, hour_cos
            -20.0, 0.0,             # temp_out, light_out
            0.0, 0.0, 0.0, 0.0,     # ac_idx, light_idx, vent_idx, ac_on
            0.0, 0.0, 0.0, 0.0,     # ev_enter, ev_leave, dur_occ, p_occ_slot
            0.0, 0.0, 0.0,          # elec_price, price_tier, elec_price_forward
        ]
        # v4 储能状态：电量比例与归一化功率（未启用储能时恒为 0）
        if self._storage_in_obs():
            low += [0.0, -1.0]      # battery_soc, battery_power
        # gen7 回归预测（仅 semi_markov 模式）：需与 OBS_NAMES 的末尾顺序一致
        if self._arrival_in_obs():
            low += [0.0, 0.0]       # empty_steps, p_arrive_soon
        return low

    def _obs_high(self) -> list:
        """观测上界。"""
        high = [
            60.0,     # temp_in（含安全上限余量）
            100.0,    # hum_in
            30000.0,  # light_in
            1.0, 1.0, 3.0,          # occ_a, occ_b, occ_dist
            23.0, 6.0, 1.0, 1.0,    # hour, dow, is_weekend, is_holiday
            1.0, 1.0,               # hour_sin, hour_cos
            60.0, 30000.0,          # temp_out, light_out
            # ac_idx 的上界必须跟随动作空间。2026-10-17 空调档位由
            # [25..29]（ac_idx 0~5）扩展为 [23..29]（ac_idx 0~7），
            # 此处原先写死 5.0，导致观测值 6/7 超出声明上界，
            # scripts/test_env.py 自检失败（353 处越界）。
            float(self.act_cfg.ac_choices - 1),
            2.0, 1.0, 1.0,          # light_idx, vent_idx, ac_on
            1.0, 1.0, 480.0, 1.0,   # ev_enter, ev_leave, dur_occ, p_occ_slot
            5.0, 2.0, 5.0,          # elec_price, price_tier, price_forward
        ]
        if self._storage_in_obs():
            high += [1.0, 1.0]
        # gen7 回归预测（仅 semi_markov 模式）：需与 OBS_NAMES 末尾顺序一致
        if self._arrival_in_obs():
            # empty_steps 的上界取一个回合的总步数（实际远小于它）
            high += [float(self.episode_steps), 1.0]
        return high

    def _storage_in_obs(self) -> bool:
        """储能状态是否进入观测空间。

        仅在启用储能时加入，以保证与旧实验（25 维观测）的兼容性：
        未启用储能时观测维度仍为 25，已训练的模型仍可加载。
        """
        return bool(getattr(self.rew_cfg, "battery_enabled", False))

    def _arrival_in_obs(self) -> bool:
        """回归预测特征是否进入观测空间。

        仅在占用模型为 semi_markov 时加入。iid 模式下"已离开多久"对
        下一步的人口分布没有信息量，加进去反而误导策略；因此该模式
        保持 25 维，与 gen5/gen6 的模型兼容。
        """
        return self.sim_cfg.occupancy_mode == "semi_markov"

    # ------------------------------------------------------------
    # Gymnasium 接口
    # ------------------------------------------------------------
    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """重置环境。

        返回：(观测向量, 附加信息)
        """
        super().reset(seed=seed)
        if seed is not None:
            self.sim.seed(seed)
            # S9b：gymnasium 1.4.0 的 reset(seed) 不播种 action_space，
            # 导致随机策略自检不可复现；这里一并播种。
            self.action_space.seed(seed)

        obs_dict = self.sim.reset(start_time=self.start_datetime)

        self._step_in_episode = 0
        self._last_obs = obs_dict
        self._episode_rewards = []

        return self._to_array(obs_dict), {"obs_dict": obs_dict}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """执行一步。

        返回：(观测, 奖励, 终止, 截断, 信息)
        """
        # 记录动作前的设备状态，用于判断"动作是否真正改变了设备状态"
        ac_before = self.sim.state.ac_idx
        light_before = self.sim.state.light_idx
        vent_before = self.sim.state.vent_idx

        obs_dict, sim_info = self.sim.step(int(action))

        # 判断是否存在有效动作变化（用于响应延迟统计与切换惩罚）
        ac_after = self.sim.state.ac_idx
        light_after = self.sim.state.light_idx
        vent_after = self.sim.state.vent_idx
        changed = (ac_after != ac_before
                   or light_after != light_before
                   or vent_after != vent_before)

        # 计算奖励
        # 储能明细是"动作结果"而非"可观测状态"，故不进入观测向量，
        # 而是用浅拷贝附加到奖励计算的输入上（v4）。
        reward_obs = obs_dict
        if getattr(self.rew_cfg, "battery_enabled", False):
            reward_obs = dict(obs_dict)
            reward_obs["battery_charge_kwh"] = self.sim.state.battery_charge_kwh
            reward_obs["battery_discharge_kwh"] = \
                self.sim.state.battery_discharge_kwh

        reward, components = self.reward_calc.compute(
            reward_obs,
            energy_kwh_step=sim_info["energy_kwh_step"],
            rng=self.np_random,
            action_changed=changed,
        )

        # 更新指标（含响应效率）
        self._update_metrics(obs_dict, components, changed=changed)

        self._step_in_episode += 1
        self._last_obs = obs_dict
        self._episode_rewards.append(reward)

        terminated = False
        truncated = self._step_in_episode >= self.episode_steps

        info = {
            "obs_dict": obs_dict,
            "step_in_episode": self._step_in_episode,
            "components": components,
            **{k: v for k, v in sim_info.items()},
        }

        if truncated:
            info["episode_metrics"] = self.get_episode_metrics()

        return self._to_array(obs_dict), float(reward), terminated, truncated, info

    # ------------------------------------------------------------
    # 指标统计（文档 5.3 节）
    # ------------------------------------------------------------
    def _update_metrics(self, obs_dict: Dict[str, Any],
                        components: Dict[str, Any],
                        changed: bool = False) -> None:
        """更新评价指标（文档 5.3 节的响应效率部分）。"""
        m = self.sim.metrics
        m["total_steps"] += 1
        m["temp_dev_sq_sum"] += float(components["temp_dev"]) ** 2

        dev = float(components["temp_dev"])
        if dev > 0:
            m["discomfort_steps"] += 1

        # ---- 响应延迟统计 ----
        # 定义：环境越界后，策略首次使其动作发生变化所经过的步数
        if dev > 0:
            if m.get("_pending_delay") is None:
                m["_pending_delay"] = 0
            elif changed:
                m["response_delays"].append(m["_pending_delay"])
                m["_pending_delay"] = None
            else:
                m["_pending_delay"] += 1
        else:
            # 已回到舒适区间
            if m.get("_pending_delay") is not None:
                m["response_delays"].append(m["_pending_delay"])
                m["_pending_delay"] = None

        # ---- 恢复时间统计 ----
        # 定义：出现有效动作后，环境回到舒适区间所经过的步数
        if changed and dev > 0 and m.get("_pending_recovery") is None:
            m["_pending_recovery"] = 1
        elif m.get("_pending_recovery") is not None:
            if dev > 0:
                m["_pending_recovery"] += 1
            else:
                m["recovery_times"].append(m["_pending_recovery"])
                m["_pending_recovery"] = None

    def get_episode_metrics(self) -> Dict[str, Any]:
        """返回本回合的评价指标（文档 5.3 节的三类指标）。"""
        m = self.sim.metrics
        total = max(m["total_steps"], 1)
        dt_h = self.sim_cfg.dt_minutes / 60.0

        delays = m["response_delays"]
        recoveries = m["recovery_times"]

        return {
            # (1) 能耗类
            "total_energy_kwh": m["total_energy_kwh"],
            "energy_per_day_kwh": m["total_energy_kwh"] / max(
                total * dt_h / 24.0, 1e-6),
            # (2) 舒适度类
            "discomfort_ratio": m["discomfort_steps"] / total,
            "temp_dev_rmse": (m["temp_dev_sq_sum"] / total) ** 0.5,
            # (3) 响应效率类
            "response_delay_mean": (sum(delays) / len(delays)) if delays else 0.0,
            "recovery_time_mean": (sum(recoveries) / len(recoveries)
                                   if recoveries else 0.0),
            "switch_count": m["switch_count"],
            "switch_per_day": m["switch_count"] / max(
                total * dt_h / 24.0, 1e-6),
            "ac_start_count": m["ac_start_count"],
            "ac_start_per_day": m["ac_start_count"] / max(
                total * dt_h / 24.0, 1e-6),
            # 其他
            "total_steps": total,
            "mean_reward": (sum(self._episode_rewards) / len(self._episode_rewards)
                            if self._episode_rewards else 0.0),
        }

    # ------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------
    def render(self):
        """以文本方式输出当前状态（调试用）。"""
        if self._last_obs is None:
            return None
        o = self._last_obs
        line = (
            f"[{self.sim.sim_time:%m-%d %H:%M} 周{['一','二','三','四','五','六','日'][self.sim.sim_time.weekday()]}] "
            f"T={o['temp_in']:5.1f}°C  RH={o['hum_in']:5.1f}%  "
            f"L={o['light_in']:6.0f}lx  "
            f"人={'有' if (o['occ_a'] or o['occ_b']) else '无'}  "
            f"空调={int(o['ac_idx'])}  灯={int(o['light_idx'])}  风={int(o['vent_idx'])}  "
            f"能耗={self.sim.state.energy_kwh:6.3f}kWh"
        )
        if self.render_mode == "ansi":
            return line
        print(line)
        return None

    def close(self) -> None:
        return None

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------
    def _to_array(self, obs_dict: Dict[str, Any]) -> np.ndarray:
        return np.array(self.sim.to_vector(obs_dict), dtype=np.float32)

    def get_obs_dict(self) -> Dict[str, Any]:
        """返回最近一次观测的字典形式（便于按名称调试）。"""
        return dict(self._last_obs) if self._last_obs else {}

    @property
    def obs_names(self):
        """观测特征名（顺序与观测向量一致）。"""
        return list(self.sim.active_obs_names())
