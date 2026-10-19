"""
家庭环境仿真器 —— 状态转移模型

职责（单一职责原则）：
    本文件只负责"环境如何演变"，即 MDP 中的状态转移 P(s' | s, a)。
    它不知道强化学习的存在，不导入任何 RL 库。

    奖励的计算放在 reward.py，Gymnasium 接口包装放在 home_env.py。
    这样分工的好处：改物理模型参数不用动环境和奖励代码。

对应文档：
    docs/MDP定义_v1.0.md 第 4 节（状态转移）
    问题 Q6（热模型参数待用实测数据标定）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from .config import ActionConfig, ConstraintConfig, RewardConfig, SimConfig


# ============================================================
# 动作解码工具
# ============================================================
def decode_action(action: int, act_cfg: ActionConfig) -> Tuple[int, int, int]:
    """把联合动作索引解码为 (空调档, 照明档, 通风档)。

    动作编号方式（笛卡尔积）：
        action = ac_idx * (n_light * n_vent) + light_idx * n_vent + vent_idx

    其中 ac_idx 取值 0..3，light_idx 取值 0..2，vent_idx 取值 0..1。
    """
    n_light = act_cfg.light_choices
    n_vent = act_cfg.vent_choices
    block = n_light * n_vent

    ac_idx = action // block
    rest = action % block
    light_idx = rest // n_vent
    vent_idx = rest % n_vent
    return ac_idx, light_idx, vent_idx


def encode_action(ac_idx: int, light_idx: int, vent_idx: int,
                  act_cfg: ActionConfig) -> int:
    """把 (空调档, 照明档, 通风档) 编码为联合动作索引。"""
    n_light = act_cfg.light_choices
    n_vent = act_cfg.vent_choices
    return ac_idx * (n_light * n_vent) + light_idx * n_vent + vent_idx


# ============================================================
# 环境物理状态
# ============================================================
@dataclass
class EnvState:
    """环境的瞬时物理状态（不含派生特征，不含时间）。"""

    temp_in: float = 28.0        # 室内温度（°C）
    hum_in: float = 55.0         # 室内相对湿度（%）
    light_in: float = 0.0        # 室内照度（lx）

    # 人员占用
    occ_a: int = 0               # 区域 A 是否有人（PIR1 + 雷达融合）
    occ_b: int = 0               # 区域 B 是否有人（PIR2）
    occ_dist: int = 0            # 雷达距离档位 0-3

    # 设备状态
    ac_idx: int = 0              # 空调档位，0=关闭
    light_idx: int = 0           # 照明档位，0=关 1=弱 2=强
    vent_idx: int = 0            # 通风档位，0=关 1=开

    # 累计量
    energy_kwh: float = 0.0      # 累计能耗（kWh）

    # 储能系统（v4 新增；未启用时保持默认值）
    battery_soc_kwh: float = 0.0     # 电池当前电量（kWh）
    battery_power_kw: float = 0.0    # 上一步充放电功率（正=充电，负=放电）
    battery_charge_kwh: float = 0.0  # 本步从电网取入用于充电的电量（kWh）
    battery_discharge_kwh: float = 0.0  # 本步放电抵扣的电量（kWh）

    @property
    def occupied(self) -> int:
        """任一区域有人即认为"有人"。"""
        return 1 if (self.occ_a or self.occ_b) else 0


# ============================================================
# 仿真器
# ============================================================
class HomeSimulator:
    """家庭环境仿真器。

    使用方式：
        sim = HomeSimulator()
        sim.reset()
        for _ in range(100):
            s = sim.step(action=5)
            print(s["temp_in"], s["light_in"], s["energy_kwh"])
    """

    # 观测向量的特征名（顺序固定，务必与 to_observation 保持一致）
    #
    # ⚠️ 两组"条件特征"必须放在列表**末尾**，且顺序为：
    #        ... battery_soc, battery_power, empty_steps, p_arrive_soon
    #    因为 HomeEnv._obs_low()/_obs_high() 是按位置追加条件边界的，
    #    放在中间会让边界数组与向量错位。
    OBS_NAMES = [
        # 室内环境（3）
        "temp_in", "hum_in", "light_in",
        # 人员占用（3）
        "occ_a", "occ_b", "occ_dist",
        # 时间上下文（6）
        "hour", "dow", "is_weekend", "is_holiday", "hour_sin", "hour_cos",
        # 室外环境（2）
        "temp_out", "light_out",
        # 设备状态（4）
        "ac_idx", "light_idx", "vent_idx", "ac_on",
        # 用户行为特征（4）
        "ev_enter", "ev_leave", "dur_occ", "p_occ_slot",
        # 电价（2）—— 分时电价场景下的关键决策依据
        "elec_price", "price_tier",
        # 前视电价（1）—— 未来一段时间平均电价，用于跨时段调度
        "elec_price_forward",
        # 储能状态（2）—— v4 新增：使策略能感知储能水平并据此调度
        "battery_soc", "battery_power",
        # 回归预测（2）—— gen7 新增：仅 semi_markov 模式下进入观测
        #   empty_steps     已连续无人多少步（0 = 有人）
        #   p_arrive_soon   由风险表算出的未来 30 分钟内回归概率
        "empty_steps", "p_arrive_soon",
    ]
    OBS_DIM = len(OBS_NAMES)

    def __init__(
        self,
        sim_cfg: Optional[SimConfig] = None,
        act_cfg: Optional[ActionConfig] = None,
        con_cfg: Optional[ConstraintConfig] = None,
        rew_cfg: Optional[RewardConfig] = None,
        seed: Optional[int] = None,
    ):
        self.sim_cfg = sim_cfg or SimConfig()
        self.act_cfg = act_cfg or ActionConfig()
        self.con_cfg = con_cfg or ConstraintConfig()
        # 功率参数（能耗模型）定义在 RewardConfig 中，这里引用它
        self.rew_cfg = rew_cfg or RewardConfig()

        self._rng = None
        self.seed(seed)

        # 运行状态
        self.state = EnvState()
        self.sim_time: datetime = datetime(2026, 1, 5, 8, 0)  # 默认周一 08:00
        self.step_count: int = 0

        # 用于派生特征的历史记录
        self._prev_occupied: int = 0
        self._prev_occ_a: int = 0
        self._prev_occ_b: int = 0
        self._dur_occ_steps: int = 0
        self._last_ac_change_step: int = -999
        self._prev_ac_idx: int = 0
        self._empty_steps: int = 0
        self._temp_out: float = self.sim_cfg.temp_outdoor_mean
        self._light_out: float = 0.0

        # 响应效率统计（文档 5.3 节）
        self.metrics = self._new_metrics()

    # ------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------
    def seed(self, seed: Optional[int]) -> None:
        import random
        self._rng = random.Random(seed)

    def _new_metrics(self) -> Dict[str, Any]:
        return {
            "total_energy_kwh": 0.0,
            "discomfort_steps": 0,
            "total_steps": 0,
            "temp_dev_sq_sum": 0.0,
            "switch_count": 0,
            "ac_start_count": 0,       # 压缩机启动次数（用于启动损耗核算）
            "response_delays": [],     # 每次越界事件的响应延迟（步）
            "recovery_times": [],      # 每次动作后的恢复时间（步）
            "ac_blocked_count": 0,     # 空调动作被约束屏蔽的次数
            # 以下为内部统计用的临时状态，不对外输出
            "_pending_delay": None,
            "_pending_recovery": None,
        }

    def set_datetime(self, dt: datetime) -> None:
        """设置仿真起始时刻，用于指定星期几与小时。"""
        self.sim_time = dt

    # ------------------------------------------------------------
    # 储能系统（v4：实验性功能）
    # ------------------------------------------------------------
    def _battery_active(self) -> bool:
        """储能是否启用。"""
        return bool(getattr(self.rew_cfg, "battery_enabled", False))

    def _battery_soc_ratio(self) -> float:
        """电池电量比例（0~1）。未启用时返回 0。"""
        if not self._battery_active():
            return 0.0
        cap = max(self.rew_cfg.battery_capacity_kwh, 1e-9)
        return float(min(max(self.state.battery_soc_kwh / cap, 0.0), 1.0))

    def _battery_power_norm(self) -> float:
        """上一步充放电功率，归一化到 [-1, 1]（正=充电）。"""
        if not self._battery_active():
            return 0.0
        pmax = max(self.rew_cfg.battery_max_power_kw, 1e-9)
        return float(min(max(self.state.battery_power_kw / pmax, -1.0), 1.0))

    def _reset_battery(self) -> None:
        """初始化电池电量。"""
        if not self._battery_active():
            self.state.battery_soc_kwh = 0.0
            self.state.battery_power_kw = 0.0
            self.state.battery_charge_kwh = 0.0
            self.state.battery_discharge_kwh = 0.0
            return
        cap = self.rew_cfg.battery_capacity_kwh
        self.state.battery_soc_kwh = cap * self.rew_cfg.battery_init_soc
        self.state.battery_power_kw = 0.0
        self.state.battery_charge_kwh = 0.0
        self.state.battery_discharge_kwh = 0.0

    def _update_battery(self, ac_energy_kwh: float) -> None:
        """按电价分档规则执行充放电，并更新状态中的充放电量。

        规则（与人工电价响应策略同源，使储能本身不依赖学习）：
            谷段充电；峰段优先放电，平段也可放电（若电量充足）。

        设计说明：
            初版实现仅在**峰段**放电，且放电量不超过**空调**本步耗电。
            实测发现这样过于保守——峰段占比仅 3 小时（占全天 12.5%），
            电池大部分时间闲置。改进为：
              · 峰段放电抵扣**全部**负载（空调 + 照明 + 通风）
              · 平段在电量高于阈值时也放电（电价仍高于谷段）
            这更符合真实家庭储能的用法（电价高时用电池，低时充电）。

        物理约束：
            · 充/放电功率不超过 battery_max_power_kw
            · 充电不超过剩余容量 / 充电效率
            · 放电不超过当前可用电量 × 放电效率
            · 放电不超过本步总负载（不能向电网倒送）
        """
        if not self._battery_active():
            self.state.battery_charge_kwh = 0.0
            self.state.battery_discharge_kwh = 0.0
            self.state.battery_power_kw = 0.0
            return

        rcfg = self.rew_cfg
        dt_h = self.sim_cfg.dt_minutes / 60.0
        cap = rcfg.battery_capacity_kwh
        pmax = rcfg.battery_max_power_kw
        eta_c = max(rcfg.battery_charge_eff, 1e-6)
        eta_d = max(rcfg.battery_discharge_eff, 1e-6)

        tier = rcfg.get_tier_name(self.sim_time.hour)
        soc = self.state.battery_soc_kwh

        # 本步总负载（用于限制放电不超过实际需求）。
        # 传进来的 ac_energy_kwh 已含照明与通风（见 step 中的调用），
        # 因此这里直接用即可。
        load_kwh = max(ac_energy_kwh, 0.0)

        charge_kwh = 0.0
        discharge_kwh = 0.0

        if rcfg.battery_rule_based:
            # 平段放电的电量阈值：低于此比例不再放电，保留电量给峰段
            flat_reserve = getattr(rcfg, "battery_flat_reserve_ratio", 0.6)

            if tier == "valley":
                headroom = max(cap - soc, 0.0)          # 可再存入的电量
                p_grid = min(pmax, headroom / (eta_c * dt_h) if dt_h > 0 else 0.0)
                charge_kwh = max(p_grid * dt_h, 0.0)
                soc += charge_kwh * eta_c
                self.state.battery_power_kw = charge_kwh / dt_h
            elif tier == "peak" or (tier == "flat"
                                    and soc > flat_reserve * cap):
                avail = max(soc - rcfg.battery_min_soc * cap, 0.0)
                p_need = min(pmax, load_kwh / dt_h if dt_h > 0 else 0.0)
                deliver = min(p_need * dt_h, avail * eta_d)
                discharge_kwh = max(deliver, 0.0)
                soc -= discharge_kwh / eta_d
                self.state.battery_power_kw = -discharge_kwh / dt_h
            else:
                self.state.battery_power_kw = 0.0

        self.state.battery_soc_kwh = float(min(max(soc, 0.0), cap))
        self.state.battery_charge_kwh = float(charge_kwh)
        self.state.battery_discharge_kwh = float(discharge_kwh)

    # ------------------------------------------------------------
    # 室外环境（简化的确定性模型）
    # ------------------------------------------------------------
    def _update_outdoor(self) -> None:
        """更新室外温度与室外光照。

        采用简化模型：温度按正弦日变化，光照按太阳高度近似。
        真实场景应由天气 API 提供，对应文档第 1.4 节。
        """
        hour_frac = self.sim_time.hour + self.sim_time.minute / 60.0
        # 温度：最低点约在 05:00，最高点约在 15:00
        phase = 2 * math.pi * (hour_frac - 15.0) / 24.0
        self._temp_out = self.sim_cfg.temp_outdoor_mean + \
            self.sim_cfg.temp_outdoor_swing * math.cos(phase)

        # 光照：6:00-18:00 之间按正弦变化，其余为 0
        if 6.0 <= hour_frac <= 18.0:
            sun = math.sin(math.pi * (hour_frac - 6.0) / 12.0)
            self._light_out = max(0.0, self.sim_cfg.light_outdoor_max * sun)
        else:
            self._light_out = 0.0

    # ------------------------------------------------------------
    # 人员占用模型
    # ------------------------------------------------------------
    def _occupancy_prob(self, hour: int, dow: int, is_weekend: bool) -> float:
        """查询该时段的占用基础概率（含周末修正）。"""
        base = self.sim_cfg.occupancy_prob[dow][hour]
        if is_weekend:
            base = min(1.0, base + self.sim_cfg.weekend_boost)
        return base

    # ------------------------------------------------------------
    # 占用：i.i.d. 模式 与 半马尔可夫模式（gen7）
    # ------------------------------------------------------------
    def _reset_semi_markov(self) -> None:
        """初始化半马尔可夫占用过程的内部状态。

        初始占用状态从周表按该时刻的概率抽取，而不是固定为"无人"——
        否则每个回合都从"人不在家"开始，会系统性偏离真实分布。
        """
        hour = self.sim_time.hour
        dow = self.sim_time.weekday()
        p = self._occupancy_prob(hour, dow, self._is_weekend())
        self._sm_occupied = 1 if self._rng.random() < p else 0
        self._sm_elapsed = 0
        self._sm_duration = (self._sm_draw_stay_steps() if self._sm_occupied
                             else self._sm_draw_away_steps())

    def _sm_draw_away_steps(self) -> int:
        """抽一个"本次外出会持续多少步"。

        分支选择依据：实测数据里，10 点前的那次外出（09:16）是全天外出，
        其余 9 次都是在其他时段发生的短途。因此以出发时刻为条件：

            出发时刻 < 10:00  -> 以 p_long_before_10 的概率进入长途分支
            其余时刻          -> 以 p_long_other  的概率进入长途分支
        """
        cfg = self.sim_cfg
        hour = self.sim_time.hour
        p_long = (cfg.sm_p_long_before_10 if hour < 10
                  else cfg.sm_p_long_other)
        pool = (cfg.sm_long_pool if self._rng.random() < p_long
                else cfg.sm_short_pool)
        minutes = float(self._rng.choice(pool))
        return max(1, int(round(minutes / cfg.dt_minutes)))

    def _sm_draw_stay_steps(self) -> int:
        """抽一个"本次在室会持续多少步"。

        夜间（22:00~06:00）用长时间池（对应睡眠），其余时段用一般池。
        这样保留了实测中"夜里长时间在室、白天频繁进出"的结构。
        """
        cfg = self.sim_cfg
        hour = self.sim_time.hour
        pool = (cfg.sm_stay_long_pool if (hour >= 22 or hour < 6)
                else cfg.sm_stay_pool)
        minutes = float(self._rng.choice(pool))
        return max(1, int(round(minutes / cfg.dt_minutes)))

    def _advance_semi_markov(self) -> int:
        """推进半马尔可夫占用一步，返回是否有人（0/1）。"""
        self._sm_elapsed += 1
        if self._sm_elapsed >= self._sm_duration:
            # 切换状态并抽新的持续时间
            self._sm_occupied = 1 - self._sm_occupied
            self._sm_elapsed = 0
            self._sm_duration = (self._sm_draw_stay_steps()
                                 if self._sm_occupied
                                 else self._sm_draw_away_steps())
        return self._sm_occupied

    def _hazard_30(self, away_steps: int) -> float:
        """查"未来 30 分钟内回归的概率"（按已离开步数索引）。

        表由 `scripts/measure_hazard_from_env.py` **从生成器本身实测**得到，
        因此与仿真里真正的回归过程一致。早先用连续分布拟合的表与生成器
        不符（60 分钟处低估 32pp、705 分钟处低估 92pp），会误导策略，
        也会骗到按该值做阈值判断的规则基线。

        表以步为单位（1 步 = 15 分钟，30 分钟 = 2 步），超出表长时
        返回表尾值。
        """
        tab = self.sim_cfg.sm_hazard_by_step
        if not tab:
            return 0.0
        k = int(away_steps)
        if k <= 0:
            return 0.0
        if k >= len(tab):
            return float(tab[-1])
        return float(tab[k - 1])

    def _update_occupancy(self, is_weekend: bool) -> None:
        """更新人员占用状态，并分配人员所在区域。

        两种模式：
          i.i.d.（默认，兼容旧模型）—— 每步按周表独立抽样
          semi_markov（gen7）—— 按"已停留时长"切换，时长由实测分布抽
        """
        cfg = self.sim_cfg
        if cfg.occupancy_mode == "semi_markov":
            occupied = self._advance_semi_markov()
        else:
            hour = self.sim_time.hour
            dow = self.sim_time.weekday()
            p = self._occupancy_prob(hour, dow, is_weekend)
            occupied = 1 if self._rng.random() < p else 0

        if occupied:
            # 进入后以较高概率停留在 A 区（主要活动区）
            if self._rng.random() < 0.75:
                self.state.occ_a, self.state.occ_b = 1, 0
            else:
                self.state.occ_a, self.state.occ_b = 0, 1
            # 距离档位：有人时随机给一个合理档位
            self.state.occ_dist = self._rng.choice([1, 2, 3])
        else:
            self.state.occ_a, self.state.occ_b = 0, 0
            self.state.occ_dist = 0

    # ------------------------------------------------------------
    # 物理演变
    # ------------------------------------------------------------
    def _solar_factor(self) -> float:
        """当前时刻的太阳因子（0~1），与 `_update_outdoor` 的口径一致。

        6:00-18:00 之间取 sin(pi*(hour-6)/12)，其余为 0。
        日照增益项使用该因子，而非室内光照值——这样在复演外部光照时
        仍然按"真实太阳高度"计热，且与联合标定的输入完全一致。
        """
        hf = self.sim_time.hour + self.sim_time.minute / 60.0
        if 6.0 <= hf <= 18.0:
            return math.sin(math.pi * (hf - 6.0) / 12.0)
        return 0.0

    def _update_temperature(
        self, ac_cooling_cmd: float, ac_on: bool, vent_on: bool
    ) -> float:
        """按一阶热模型更新室内温度，并返回空调本步的**实际制冷量**。

        返回值 gross_cooling 是 AC 自身贡献的降温量（°C/步），
        不含围护传热、日照、通风、人员得热。它用于能耗计算：
        压缩机功率应与 gross_cooling 成正比，而不是与"净温度变化"成正比。

        S1 修复（2026-10-20）：此前 step() 用 `temp_before - temp_after`
        作为制冷量，稳态时净变化≈0，导致 23°C 与 29°C 能耗几乎一样。
        现在由本函数返回 AC 的 gross cooling，step() 直接使用它。

        温度变化由五部分组成：
            1. 围护结构传热：向室外温度靠拢
            2. 基础得热：楼板/邻户传热、电器待机、墙体蓄热释放
               （与是否有人无关）
            3. 日照得热：按太阳因子加权
            4. 空调制冷：向设定温度靠拢（受最大能力限制）
            5. 通风换热 + 人员得热

        说明（2026-10-17）：第 2、3 项为本次新增。此前模型只有一个
        按"有人"生效的热增益，缺少与人员无关的基础得热，导致标定结果
        在物理上不自洽（详见 SimConfig.gain_base 的注释）。
        """
        cfg = self.sim_cfg
        T = self.state.temp_in

        # 1) 围护结构传热
        dT = -cfg.ua_coef * (T - self._temp_out)

        # 2) 基础得热（与人员无关）
        dT += cfg.gain_base

        # 3) 日照得热
        dT += cfg.solar_coef * self._solar_factor()

        # 4) 空调制冷
        cooling = 0.0
        if ac_on:
            # 制冷需求：室内与设定温度之差，乘以等效降温系数
            demand = cfg.ac_coef * (T - ac_cooling_cmd)
            # 只有室内高于设定温度时才制冷
            cooling = min(max(demand, 0.0), cfg.ac_max_cooling)
            dT -= cooling

        # 5) 通风换热 + 人员得热
        if vent_on:
            dT -= cfg.vent_coef * (T - self._temp_out)
        if self.state.occupied:
            dT += cfg.internal_gain

        T_new = T + dT
        # 安全裁剪，防止仿真发散
        T_new = max(-10.0, min(cfg.temp_clip_max, T_new))
        self.state.temp_in = T_new
        return cooling

    def _update_humidity(self, ac_on: bool, vent_on: bool) -> None:
        """更新室内湿度。"""
        cfg = self.sim_cfg
        H = self.state.hum_in

        if ac_on:
            H -= cfg.hum_ac_dehumid
        if vent_on:
            H += cfg.hum_vent_rate * (cfg.hum_outdoor - H)
        if self.state.occupied:
            H += cfg.hum_occupant_gain * (self.state.occ_a + self.state.occ_b)

        self.state.hum_in = max(cfg.hum_clip_min, min(cfg.hum_clip_max, H))

    def _update_light(self) -> None:
        """更新室内照度：自然采光 + 照明贡献。"""
        cfg = self.sim_cfg
        natural = self._light_out * cfg.light_window_ratio
        if self.state.light_idx == 0:
            artificial = 0.0
        elif self.state.light_idx == 1:
            artificial = cfg.light_low_level
        else:
            artificial = cfg.light_high_level
        self.state.light_in = natural + artificial

    # ------------------------------------------------------------
    # 能耗计算（文档 3.2 节）
    # ------------------------------------------------------------
    def _step_energy_kwh(
        self, ac_idx: int, light_idx: int, vent_idx: int,
        gross_cooling: float, ac_started: bool = False
    ) -> float:
        """计算本时间步的耗电量（kWh）。

        注意：能耗为仿真模型计算值，不是实测值（文档 3.2 节已说明）。

        S1 修复（2026-10-20）：参数从"净降温"改为 AC 的 **gross cooling**
        （见 `_update_temperature` 的返回值）。压缩机功率与制冷量成正比；
        只有 gross_cooling > 0 时才应用最低压缩机功率；风机功率单列。
        这样 23°C 与 29°C 的能耗会显著不同，而不是被最低功率拍平。

        参数 ac_started 表示本步空调是否从"关闭"变为"开启"，
        用于计入压缩机启动损耗（见 RewardConfig.ac_startup_penalty_kwh）。
        """
        cfg = self.sim_cfg        # 时间与物理参数
        rcfg = self.rew_cfg       # 功率参数
        dt_h = cfg.dt_minutes / 60.0

        # 空调：按实际制冷量换算电功率
        if ac_idx == 0:
            # 关闭状态：仅待机功耗（控制电路等）
            p_ac = rcfg.ac_standby_kw
        else:
            # 制冷量（kW）= 名义制冷量 × 实际制冷量 / 最大制冷能力
            cooling_kw = rcfg.ac_cooling_capacity_kw * \
                (gross_cooling / max(cfg.ac_max_cooling, 1e-6))

            if cooling_kw > 1e-9:
                # 压缩机在制冷：功率 = 制冷量 / COP，并受最低/额定功率约束
                p_compressor = cooling_kw / max(rcfg.ac_cop, 1e-6)
                p_compressor = max(
                    p_compressor,
                    rcfg.ac_min_power_ratio * rcfg.ac_rated_kw)
                p_compressor = min(p_compressor, rcfg.ac_rated_kw)
            else:
                # 室温已低于设定温度：压缩机不制冷，只收风机/控制电路功率
                p_compressor = 0.0

            p_ac = p_compressor + rcfg.ac_fan_kw

        # 照明
        p_light = rcfg.light_rated_kw * rcfg.light_power_ratio[light_idx]

        # 通风
        p_vent = rcfg.vent_rated_kw if vent_idx == 1 else 0.0

        energy = (p_ac + p_light + p_vent) * dt_h

        # 压缩机启动损耗
        if ac_started:
            energy += rcfg.ac_startup_penalty_kwh

        return energy

    # ------------------------------------------------------------
    # 接口：reset
    # ------------------------------------------------------------
    def reset(self, start_time: Optional[datetime] = None) -> Dict[str, Any]:
        """重置环境到初始状态，返回初始观测字典。"""
        cfg = self.sim_cfg
        if start_time is not None:
            self.sim_time = start_time

        self.state = EnvState(
            temp_in=cfg.temp_initial,
            hum_in=cfg.hum_initial,
            light_in=0.0,
            ac_idx=0, light_idx=0, vent_idx=0,
            energy_kwh=0.0,
        )
        self.step_count = 0
        self._prev_occupied = 0
        self._prev_occ_a = 0
        self._prev_occ_b = 0
        self._dur_occ_steps = 0
        self._last_ac_change_step = -999
        self._prev_ac_idx = 0
        self._empty_steps = 0
        self.metrics = self._new_metrics()
        self._reset_battery()          # v4：初始化储能状态
        self._reset_semi_markov()      # gen7：初始化半马尔可夫占用状态

        self._update_outdoor()
        self._update_occupancy(self._is_weekend() or self._is_holiday())
        self._update_light()

        # 与 step() 保持一致地更新派生特征。否则 reset 后若初始为"无人"，
        # empty_steps 仍为 0，观测会自相矛盾（无人却显示刚离开）。
        if self.state.occupied:
            self._dur_occ_steps = 1
            self._empty_steps = 0
        else:
            self._dur_occ_steps = 0
            self._empty_steps = 1

        return self.get_observation()

    # ------------------------------------------------------------
    # 接口：step
    # ------------------------------------------------------------
    def step(self, action: int) -> Dict[str, Any]:
        """执行一个动作，推进一个时间步。

        参数：
            action  联合动作索引（0 ~ n_actions-1）

        返回：观测字典（含派生特征），
              以及本步的辅助信息放在 "info" 键中。
        """
        cfg = self.sim_cfg
        ac_idx, light_idx, vent_idx = decode_action(action, self.act_cfg)

        # ---------- 约束处理（文档 3.4 节，动作屏蔽）----------
        # C4：无人超过设定时长后强制关闭空调。
        # 但该强制动作同样必须遵守 C1/C2 的最小切换间隔约束，
        # 否则会出现"刚开机就被强制关机"的震荡。
        ac_blocked = False
        empty_steps_needed = self.con_cfg.ac_auto_off_empty / cfg.dt_minutes
        force_off = (self._empty_steps >= empty_steps_needed)

        elapsed = self.step_count - self._last_ac_change_step
        min_steps = self.con_cfg.ac_min_switch_interval / cfg.dt_minutes

        # S2 修复（2026-10-20）：C4 必须在"动作是否变化"之外执行。
        # 原逻辑只在 ac_idx != state.ac_idx 时检查，策略保持开机能绕过约束。
        #
        # 同时处理 C4 与"无人预冷"的冲突：
        #   precool 模式：若 p_arrive_soon >= 阈值，允许预冷（不强制关机）
        #   hard   模式：无条件强制关机
        #   off    模式：不执行 C4（仅用于复现旧结果）
        c4_mode = getattr(self.con_cfg, "c4_mode", "precool")
        allow_precool = False
        if force_off and c4_mode == "precool":
            if self.sim_cfg.occupancy_mode == "semi_markov":
                p_arrive = self._hazard_30(self._empty_steps)
            else:
                p_arrive = 0.0
            allow_precool = p_arrive >= getattr(
                self.con_cfg, "c4_precool_threshold", 0.02)

        if force_off and not allow_precool and c4_mode != "off":
            if elapsed >= min_steps:
                if ac_idx != 0:
                    ac_idx = 0
                    ac_blocked = True
            elif ac_idx > 0 and ac_idx != self.state.ac_idx:
                # 间隔未满足时也不允许在无人超时后新开机
                ac_idx = 0
                ac_blocked = True

        if ac_idx != self.state.ac_idx and elapsed < min_steps:
            # 距上次切换时间不足，屏蔽本次空调状态改变（含强制关机）
            ac_idx = self.state.ac_idx
            ac_blocked = True

        # 记录空调切换
        if ac_idx != self.state.ac_idx:
            self._last_ac_change_step = self.step_count
            self.metrics["switch_count"] += 1
        if light_idx != self.state.light_idx or vent_idx != self.state.vent_idx:
            self.metrics["switch_count"] += 1

        # ---------- 保存切换前的状态（用于计算派生特征）----------
        prev_occ = self.state.occupied
        # 保存切换前的空调档位。必须在下面写入新档位之前取，
        # 否则无法判断本步是否为"压缩机启动"。
        #
        # 修复记录（2026-10-17）：原先在第 577 行用
        #     ac_started = (ac_idx > 0 and self.state.ac_idx == 0)
        # 判断启动，但那时 `self.state.ac_idx` 已被赋为 `ac_idx`
        # （见下方 562 行），条件退化为 `ac_idx > 0 and ac_idx == 0`，
        # 恒为 False。后果是 `ac_startup_penalty_kwh` 从未计入能耗、
        # `ac_start_count` 恒为 0，规则控制频繁启停的代价被完全忽略。
        prev_ac_idx = self.state.ac_idx

        # ---------- 执行动作并推进物理状态 ----------
        self.state.ac_idx = ac_idx
        self.state.light_idx = light_idx
        self.state.vent_idx = vent_idx

        ac_on = ac_idx > 0
        ac_setpoint = (self.act_cfg.ac_setpoints[ac_idx - 1]
                       if ac_on else 0.0)
        vent_on = vent_idx == 1

        # S1 修复：使用 AC 自身的 gross cooling 计费，而不是净温度变化
        gross_cooling = self._update_temperature(ac_setpoint, ac_on, vent_on)
        self._update_humidity(ac_on, vent_on)

        # ---------- 计算并累加能耗 ----------
        # 本步是否发生"压缩机启动"：上一步关闭、本步开启
        ac_started = (ac_idx > 0 and prev_ac_idx == 0)
        e_load_only = self._step_energy_kwh(ac_idx, light_idx, vent_idx,
                                            gross_cooling,
                                            ac_started=ac_started)

        # ---------- 储能充放电（v4）----------
        # 充电量从电网取电（计入本步能耗）；放电量抵扣本步负载。
        # 传入的是"本步负载耗电"（空调 + 照明 + 通风，且不含电池），
        # 用于限制放电不超过实际需求（不能向电网倒送）。
        self._update_battery(e_load_only)
        e = e_load_only + self.state.battery_charge_kwh \
            - self.state.battery_discharge_kwh
        e = max(e, 0.0)      # 电量不可能为负

        self.state.energy_kwh += e
        self.metrics["total_energy_kwh"] += e
        if ac_started:
            self.metrics["ac_start_count"] += 1
        if self._battery_active():
            self.metrics["battery_charge_kwh_total"] = \
                self.metrics.get("battery_charge_kwh_total", 0.0) \
                + self.state.battery_charge_kwh
            self.metrics["battery_discharge_kwh_total"] = \
                self.metrics.get("battery_discharge_kwh_total", 0.0) \
                + self.state.battery_discharge_kwh

        # ---------- 时间推进 ----------
        self.step_count += 1
        self.sim_time += timedelta(minutes=cfg.dt_minutes)

        # ---------- 更新室外、占用、光照 ----------
        self._update_outdoor()
        is_weekend = self._is_weekend() or self._is_holiday()
        self._update_occupancy(is_weekend)
        self._update_light()

        # ---------- 更新派生特征的历史 ----------
        occ_now = self.state.occupied
        if occ_now:
            self._dur_occ_steps += 1
            self._empty_steps = 0
        else:
            self._dur_occ_steps = 0
            self._empty_steps += 1

        self._prev_occupied = prev_occ
        self._prev_occ_a = self.state.occ_a
        self._prev_occ_b = self.state.occ_b

        # ---------- 组装观测 ----------
        obs = self.get_observation()

        info = {
            "energy_kwh_step": e,
            "energy_kwh_cum": self.state.energy_kwh,
            "ac_blocked": ac_blocked,
            "ac_setpoint": ac_setpoint,
            "sim_time": self.sim_time,
        }
        if ac_blocked:
            self.metrics["ac_blocked_count"] += 1
        return obs, info

    # ------------------------------------------------------------
    # 观测构造
    # ------------------------------------------------------------
    def _is_weekend(self) -> bool:
        return self.sim_time.weekday() >= 5

    def _is_holiday(self) -> bool:
        """是否法定节假日（S9 修复：原来 is_holiday 永久为 0）。"""
        return self.sim_time.strftime("%Y-%m-%d") in self.sim_cfg.holiday_dates

    def _hour_sin_cos(self) -> Tuple[float, float]:
        """小时的周期编码（文档 1.3 节）。"""
        h = self.sim_time.hour + self.sim_time.minute / 60.0
        return (math.sin(2 * math.pi * h / 24.0),
                math.cos(2 * math.pi * h / 24.0))

    def _p_occ_slot(self) -> float:
        """历史同时段占用率（文档 1.6 节）。

        这里用占用模型的基础概率作为"历史统计值"的代理。
        真实系统运行时，该值应由历史数据统计得到。
        """
        hour = self.sim_time.hour
        dow = self.sim_time.weekday()
        return self._occupancy_prob(hour, dow, self._is_weekend())

    def get_observation(self) -> Dict[str, Any]:
        """返回当前观测（字典形式）。

        字典键与 OBS_NAMES 一一对应，便于按名称取用与调试。
        """
        s = self.state
        hour_sin, hour_cos = self._hour_sin_cos()

        # 进入/离开事件（相对上一时刻）
        occ_now = s.occupied
        ev_enter = 1.0 if (occ_now and not self._prev_occupied) else 0.0
        ev_leave = 1.0 if ((not occ_now) and self._prev_occupied) else 0.0

        return {
            # 室内环境
            "temp_in": float(s.temp_in),
            "hum_in": float(s.hum_in),
            "light_in": float(s.light_in),
            # 人员占用
            "occ_a": float(s.occ_a),
            "occ_b": float(s.occ_b),
            "occ_dist": float(s.occ_dist),
            # 时间上下文
            "hour": float(self.sim_time.hour),
            "dow": float(self.sim_time.weekday()),
            "is_weekend": 1.0 if self._is_weekend() else 0.0,
            "is_holiday": 1.0 if self._is_holiday() else 0.0,
            "hour_sin": float(hour_sin),
            "hour_cos": float(hour_cos),
            # 室外环境
            "temp_out": float(self._temp_out),
            "light_out": float(self._light_out),
            # 设备状态
            "ac_idx": float(s.ac_idx),
            "light_idx": float(s.light_idx),
            "vent_idx": float(s.vent_idx),
            "ac_on": 1.0 if s.ac_idx > 0 else 0.0,
            # 用户行为特征
            "ev_enter": ev_enter,
            "ev_leave": ev_leave,
            "dur_occ": float(min(self._dur_occ_steps * self.sim_cfg.dt_minutes,
                                 480)),
            "p_occ_slot": float(self._p_occ_slot()),
            # 回归预测（gen7）：仅在 semi_markov 模式下进入观测向量
            #   empty_steps    已连续无人多少步（0 = 有人）
            #   p_arrive_soon  由风险表算出的未来 30 分钟内回归概率
            # 有人时 p_arrive_soon 记 0（不需要预测回来）。
            "empty_steps": float(self._empty_steps),
            "p_arrive_soon": (0.0 if s.occupied else
                              float(self._hazard_30(self._empty_steps))),
            # 电价（分时电价场景下的决策依据）
            "elec_price": float(self.rew_cfg.get_price(self.sim_time.hour)),
            "price_tier": float(
                {"valley": 0.0, "flat": 1.0, "peak": 2.0}[
                    self.rew_cfg.get_tier_name(self.sim_time.hour)]),
            # 前视电价：未来一段时间平均电价，为跨时段调度提供趋势信息
            "elec_price_forward": float(
                self.rew_cfg.get_forward_price(self.sim_time.hour)),
            # 储能状态（v4）：电量比例与上一步功率（归一化到 ±1）
            "battery_soc": float(self._battery_soc_ratio()),
            "battery_power": float(self._battery_power_norm()),
        }

    def to_vector(self, obs: Optional[Dict[str, Any]] = None) -> list:
        """把观测字典转成固定顺序的列表，用于构造 Gymnasium 观测向量。

        注意：储能状态（battery_soc / battery_power）**仅在启用储能时**
        才进入向量。这样未启用储能的实验仍保持 25 维观测，
        与 v1~v3 已训练的模型兼容（否则旧模型无法加载）。
        """
        if obs is None:
            obs = self.get_observation()
        names = self.active_obs_names()
        return [obs[name] for name in names]

    def active_obs_names(self) -> list:
        """当前生效的观测特征名列表。

        两组特征按需启用，以保持向后兼容：
          · 储能状态：仅在启用储能时加入（否则 25 维）
          · 回归预测：仅在 semi_markov 占用模式下加入（否则 25 维）
        两者都关闭时观测为 25 维，与 gen5/gen6 的模型兼容。
        """
        skip = set()
        if not self._battery_active():
            skip |= {"battery_soc", "battery_power"}
        if self.sim_cfg.occupancy_mode != "semi_markov":
            skip |= {"empty_steps", "p_arrive_soon"}
        return [n for n in self.OBS_NAMES if n not in skip]
