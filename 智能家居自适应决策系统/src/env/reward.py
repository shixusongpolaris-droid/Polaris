"""
奖励函数 —— MDP 的 R 部分

职责：
    根据"转移后的观测 + 本步动作 + 本步能耗"，计算奖励值，
    并更新响应效率相关指标。

对应文档：
    docs/MDP定义_v1.0.md 第 3 节
        3.1 舒适度代价 C_comfort
        3.2 能耗代价 E
        3.3 时间场景化权重 λ_e（本项目核心机制）
        3.4 约束（在 simulator 中以动作屏蔽实现）
    5.3 节评价指标（响应延迟、恢复时间、状态切换频率）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config import ActionConfig, RewardConfig, SimConfig


class RewardCalculator:
    """奖励计算器。

    使用方式：
        rc = RewardCalculator()
        reward, components = rc.compute(obs, action_idx, energy_kwh, info)

    说明：
        奖励采用负成本形式：R = − [ C_comfort + λ_e · E ]
    """

    def __init__(
        self,
        rew_cfg: Optional[RewardConfig] = None,
        sim_cfg: Optional[SimConfig] = None,
        act_cfg: Optional[ActionConfig] = None,
    ):
        self.cfg = rew_cfg or RewardConfig()
        self.sim_cfg = sim_cfg or SimConfig()
        self.act_cfg = act_cfg or ActionConfig()

    # ------------------------------------------------------------
    # 舒适区间判定
    # ------------------------------------------------------------
    def temp_band(self, occupied: bool, hour: int) -> tuple:
        """返回当前场景下的舒适温度区间 (T_low, T_high)。

        根据文档 3.1 节：
            有人（默认）      22 ~ 26 °C
            有人（睡眠时段）   24 ~ 27 °C
            无人              16 ~ 30 °C
        """
        c = self.cfg
        if not occupied:
            return c.temp_low_empty, c.temp_high_empty
        is_sleep = (hour >= c.sleep_start_hour) or (hour < c.sleep_end_hour)
        if is_sleep:
            return c.temp_low_sleep, c.temp_high_sleep
        return c.temp_low_occupied, c.temp_high_occupied

    # ------------------------------------------------------------
    # 舒适度代价（文档 3.1）
    # ------------------------------------------------------------
    def temp_cost(self, temp_in: float, occupied: bool, hour: int) -> tuple:
        """温度代价。

        采用"区间内小偏差 + 区间外线性惩罚"的形式：
            区间内：以区间中心为参考的轻微代价（权重较小）
            区间外：与偏离量成正比，且放大 temp_scale 倍

        为什么用线性而不是平方：
            平方项会让远离区间的代价迅速变得极大（例如偏离 4°C 代价达 48），
            使舒适度项完全支配奖励，能耗项失去作用。线性形式使两个目标
            处于可比量级，多目标权衡机制才能真正生效。
        """
        c = self.cfg
        T_low, T_high = self.temp_band(occupied, hour)

        if temp_in < T_low:
            dev = T_low - temp_in
            return c.alpha_cool * c.temp_scale * dev, dev
        if temp_in > T_high:
            dev = temp_in - T_high
            return c.alpha_hot * c.temp_scale * dev, dev

        # 区间内：轻微代价，提供"越接近中心越好"的梯度信号
        #
        # 修正（2026-10-17）：**无人时不施加该梯度**。
        #
        # 原因：无人区间的中心是 (16+30)/2 = 23 °C。若在无人时也施加
        # "趋向中心"的梯度，策略会主动把空房间压到约 23 °C，制造出
        # 虚假的能耗需求。
        #
        # 实测证据（docs/为何比全开更费电.md）：
        #   energy_scale=10 的 PPO 在无人时把室温维持在 23.67 °C、
        #   空调开机率 98.9%，无人时段耗电 3.454 kWh/天（占总电量 44.7%）；
        #   而"一直开 25 °C"的恒温规则无人时室温 25.34 °C、
        #   耗电 2.565 kWh/天。RL 比"一直开"多用 22% 的电，
        #   其中 63.7% 的差额来自给空房间降温。
        #   energy_scale=60 的同一算法则自动学到"无人时不开机"
        #   （开机率 9.3%），说明该行为是被奖励函数诱导出来的，
        #   而非算法的能力限制。
        #
        # 该梯度本意是辅助学习的 shaping，不是真实的舒适度代价；
        # 无人时既无人在场，"把温度压向区间中心"没有物理依据。
        # 越界惩罚（上方两个分支）仍然保留，以体现"无人时也应避免
        # 极端温度保护家具"。
        if not occupied:
            return 0.0, 0.0

        center = (T_low + T_high) / 2.0
        return 0.05 * c.alpha_hot * abs(temp_in - center), 0.0

    def humidity_cost(self, hum_in: float) -> float:
        """湿度代价（文档 3.1 节 (2)）。"""
        c = self.cfg
        if hum_in < c.hum_low:
            return c.beta_hum * (c.hum_low - hum_in)
        if hum_in > c.hum_high:
            return c.beta_hum * (hum_in - c.hum_high)
        return 0.0

    def light_cost(self, obs: Dict[str, Any]) -> float:
        """光照代价（文档 3.1 节 (3)）。

        与"是否有人"强相关：
            有人但光照不足且未开灯 -> 不舒适的代价
            无人却开着灯           -> 浪费的代价
        """
        c = self.cfg
        occupied = bool(obs.get("occ_a", 0) or obs.get("occ_b", 0))
        light_on = float(obs.get("light_idx", 0)) > 0
        light_in = float(obs.get("light_in", 0.0))

        if occupied and light_in < c.light_min_lux and not light_on:
            return c.gamma_light_off
        if (not occupied) and light_on:
            return c.gamma_light_waste
        return 0.0

    # ------------------------------------------------------------
    # 奖励主函数
    # ------------------------------------------------------------
    def compute(
        self,
        obs: Dict[str, Any],
        energy_kwh_step: float,
        rng=None,
        action_changed: bool = False,
        update_metrics: bool = True,
    ) -> tuple:
        """计算一步的奖励。

        参数：
            obs              转移后的观测（字典）
            energy_kwh_step  本步耗电量（kWh）
            rng              随机数发生器（weight_mode="random" 时用）
            action_changed   本步设备的运行状态是否发生了变化
            update_metrics   是否更新响应效率指标

        返回：
            (reward, components)
            components 是各项代价的明细，便于调试与画图分析

        奖励构成（文档第 3 节 + 量纲归一化 + 切换惩罚）：
            R = − [ C_comfort + λ_e · energy_scale · E + p_switch · 1{状态变化} ]
        """
        c = self.cfg

        occupied = bool(obs.get("occ_a", 0) or obs.get("occ_b", 0))
        hour = int(obs.get("hour", 12))
        dow = int(obs.get("dow", 0))
        is_weekend = bool(obs.get("is_weekend", 0))
        is_holiday = bool(obs.get("is_holiday", 0))

        # ---- 舒适度代价 ----
        c_temp, temp_dev = self.temp_cost(float(obs["temp_in"]), occupied, hour)
        c_hum = self.humidity_cost(float(obs["hum_in"]))
        c_light = self.light_cost(obs)

        comfort = (c.w_temp * c_temp
                   + c.w_hum * c_hum
                   + c.w_light * c_light)

        # ---- 能耗权重（文档 3.3，本项目核心机制）----
        lam = c.get_energy_weight(hour, dow, is_weekend, is_holiday,
                                  occupied, rng=rng)

        # ---- 切换惩罚 ----
        c_switch = c.switch_penalty if action_changed else 0.0

        # ---- 电价（分时电价场景）----
        # 使用**前视电价**而非瞬时电价：当未来电价更高时，前视电价上升，
        # 促使算法在低价时段多制冷（蓄冷），从而在后续高价时段减少用电。
        # 这是解决"跨时段调度的延迟收益被折扣因子削弱"的关键设计。
        price_now = c.get_price(hour)
        price_eff = c.get_forward_price(hour)
        price_tier = c.get_tier_name(hour) if c.tou_enabled else "flat"

        # ---- 总奖励 ----
        # 能耗项乘 energy_scale 做量纲归一化，否则能耗权重无法影响策略
        energy_term = lam * c.energy_scale * price_eff * energy_kwh_step
        reward = -(comfort + energy_term + c_switch)

        components = {
            "reward": reward,
            "comfort_total": comfort,
            "cost_temp": c_temp,
            "cost_hum": c_hum,
            "cost_light": c_light,
            "cost_switch": c_switch,
            "temp_dev": temp_dev,
            "lambda_energy": lam,
            "energy_kwh": energy_kwh_step,
            "energy_term": energy_term,
            # 真实电价（用于电费核算）与前视电价（用于奖励）分开记录
            "elec_price": price_now,
            "elec_price_eff": price_eff,
            "price_tier": price_tier,
            "occupied": 1.0 if occupied else 0.0,
        }

        # 储能明细（v4）：仅启用储能时记录，便于核算"充放电对电费的影响"
        if getattr(c, "battery_enabled", False):
            components["battery_charge_kwh"] = float(
                obs.get("battery_charge_kwh", 0.0))
            components["battery_discharge_kwh"] = float(
                obs.get("battery_discharge_kwh", 0.0))

        return reward, components
