"""2 房间最小环境（Phase 0）：一阶双区热模型 + 单人在 A/B/外出。

设计原则：
- 不改冻结版 src/env；本文件是独立的多房间实验环境；
- B 房间没有实测，使用 A 的参数 + 固定初温 28°C，标注“合成”；
- 先只做 AC 动作（每房间 8 档），不引入灯/通风，降低 Phase 0 复杂度；
- 热量/能耗口径与单房间冻结版一致（gross cooling / COP / 风机 / 启动）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

AC_SETPOINTS = [23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]


@dataclass
class MultiRoomConfig:
    dt_minutes: int = 15
    steps_per_day: int = 96

    # 双区热参数（B 复用 A 的参数，作为“合成房间”）
    ua_a: float = 0.15175
    ua_b: float = 0.15175
    gain_a: float = 0.8925
    gain_b: float = 0.8925
    solar_a: float = 0.215
    solar_b: float = 0.15
    g_closed: float = 0.01       # 门关时房间间传热（°C/步）
    g_open: float = 0.12         # 门开时
    temp_init_a: float = 26.0
    temp_init_b: float = 28.0    # B 初始偏热，用来观察“预冷下一间”的价值
    temp_outdoor_mean: float = 22.0
    temp_outdoor_swing: float = 3.0
    temp_clip_max: float = 45.0
    # C3 安全区间（每房间，尽力保证；越界仍会记录到 metrics）
    temp_safe_min: float = 16.0
    temp_safe_max: float = 30.0
    c3_enforce: bool = True

    # 空调（与冻结版一致）
    ac_coef: float = 1.83
    ac_max_cooling: float = 2.1175
    cop: float = 3.0
    fan_kw: float = 0.04
    rated_kw: float = 1.1
    cooling_capacity_kw: float = 3.5
    ac_startup_kwh: float = 0.010

    # 电价（第一档分时）
    price_peak: float = 0.617
    price_valley: float = 0.307

    # 舒适带（与冻结版一致）
    temp_low_occupied: float = 22.0
    temp_high_occupied: float = 26.0
    temp_low_sleep: float = 24.0
    temp_high_sleep: float = 27.0
    sleep_start_hour: int = 23
    sleep_end_hour: int = 7


class WeekSchedule:
    """一周作息：工作日/周末的 A/B/外出；用于 Phase 0 的确定性比较。

    工作日：
        00-09 A（睡眠/起床）  09-18 外出  18-22 A  22-24 B（睡眠）
    周末：
        00-12 A（睡眠/起床）  12-14 外出  14-18 B  18-22 A  22-24 B
    """

    def __init__(self, start: datetime):
        self.start = start
        self._cache = None       # (查询时刻, 下次变化时刻, 新状态)

    def _template(self, t: datetime):
        if t.weekday() < 5:
            return [(0, 9, "A"), (9, 18, "out"), (18, 22, "A"),
                    (22, 24, "B")]
        return [(0, 12, "A"), (12, 14, "out"), (14, 18, "B"),
                (18, 22, "A"), (22, 24, "B")]

    def state_at(self, t: datetime) -> str:
        h = t.hour + t.minute / 60.0
        for a, b, s in self._template(t):
            if a <= h < b:
                return s
        return "out"

    def next_transition(self, t: datetime):
        """返回 (下一次状态变化时刻, 新状态)；找不到返回 (None, 当前状态)。

        缓存：同一段状态内重复查询直接返回上次结果，避免每步扫 672 次。
        """
        c = self._cache
        if c is not None:
            q_t, n_t, n_s = c
            if n_t is not None and q_t <= t < n_t:
                return n_t, n_s
        cur = self.state_at(t)
        cur_t = t
        for _ in range(7 * 96):
            cur_t += timedelta(minutes=15)
            s = self.state_at(cur_t)
            if s != cur:
                self._cache = (t, cur_t, s)
                return cur_t, s
        self._cache = (t, None, cur)
        return None, cur


def is_sleep(t: datetime, cfg: MultiRoomConfig) -> bool:
    h = t.hour
    return h >= cfg.sleep_start_hour or h < cfg.sleep_end_hour


def comfort_band(t: datetime, cfg: MultiRoomConfig):
    if is_sleep(t, cfg):
        return cfg.temp_low_sleep, cfg.temp_high_sleep
    return cfg.temp_low_occupied, cfg.temp_high_occupied


class MultiRoomSim:
    """两房间、单人的最小仿真；动作是 (ac_a, ac_b)，取值 0..7。"""

    def __init__(self, cfg: MultiRoomConfig, start: datetime):
        self.cfg = cfg
        self.start = start
        self.schedule = WeekSchedule(start)
        self.reset()

    def reset(self):
        cfg = self.cfg
        self.t = self.start
        self.temp_a = cfg.temp_init_a
        self.temp_b = cfg.temp_init_b
        self.energy_a = 0.0
        self.energy_b = 0.0
        self.cost = 0.0
        self.occ_steps_a = 0
        self.occ_steps_b = 0
        self.bad_a = 0
        self.bad_b = 0
        self.switch = 0
        self.peak_power = 0.0
        self.prev_ac_a = 0
        self.prev_ac_b = 0
        self.prev_comp_a = False
        self.prev_comp_b = False

    # ---------- 环境 ----------
    def _outdoor(self, t: datetime) -> float:
        h = t.hour + t.minute / 60.0
        phase = 2 * math.pi * (h - 15.0) / 24.0
        return self.cfg.temp_outdoor_mean + self.cfg.temp_outdoor_swing * math.cos(phase)

    def _solar(self, t: datetime) -> float:
        h = t.hour + t.minute / 60.0
        if 6.0 <= h <= 18.0:
            return math.sin(math.pi * (h - 6.0) / 12.0)
        return 0.0

    def _cooling(self, temp: float, ac_idx: int) -> float:
        if ac_idx <= 0:
            return 0.0
        sp = AC_SETPOINTS[ac_idx - 1]
        demand = self.cfg.ac_coef * (temp - sp)
        return min(max(demand, 0.0), self.cfg.ac_max_cooling)

    def _energy(self, cooling: float, ac_idx: int, prev_comp: bool):
        cfg = self.cfg
        if ac_idx <= 0:
            return 0.0, False, 0.0
        cooling_kw = (cfg.cooling_capacity_kw * cooling / cfg.ac_max_cooling
                      if cooling > 1e-9 else 0.0)
        p_comp = (min(cooling_kw / cfg.cop, cfg.rated_kw)
                  if cooling_kw > 1e-9 else 0.0)
        p = p_comp + cfg.fan_kw
        e = p * cfg.dt_minutes / 60.0
        comp_on = cooling > 1e-9
        if comp_on and not prev_comp:
            e += cfg.ac_startup_kwh
        return e, comp_on, p

    def step(self, ac_a: int, ac_b: int, door_open: bool = False):
        cfg = self.cfg
        t = self.t
        # C3：每房间安全区间（最高优先级；不受动作间隔约束）
        if cfg.c3_enforce:
            if self.temp_a > cfg.temp_safe_max:
                ac_a = 1
            elif self.temp_a < cfg.temp_safe_min:
                ac_a = 0
            if self.temp_b > cfg.temp_safe_max:
                ac_b = 1
            elif self.temp_b < cfg.temp_safe_min:
                ac_b = 0
        state = self.schedule.state_at(t)
        temp_out = self._outdoor(t)
        solar = self._solar(t)
        g = cfg.g_open if door_open else cfg.g_closed

        c_a = self._cooling(self.temp_a, ac_a)
        c_b = self._cooling(self.temp_b, ac_b)
        e_a, comp_a, p_a = self._energy(c_a, ac_a, self.prev_comp_a)
        e_b, comp_b, p_b = self._energy(c_b, ac_b, self.prev_comp_b)

        d_a = (-cfg.ua_a * (self.temp_a - temp_out)
               - g * (self.temp_a - self.temp_b)
               + cfg.gain_a + cfg.solar_a * solar - c_a)
        d_b = (-cfg.ua_b * (self.temp_b - temp_out)
               - g * (self.temp_b - self.temp_a)
               + cfg.gain_b + cfg.solar_b * solar - c_b)
        self.temp_a = max(-10.0, min(cfg.temp_clip_max, self.temp_a + d_a))
        self.temp_b = max(-10.0, min(cfg.temp_clip_max, self.temp_b + d_b))

        price = (cfg.price_peak if 6 <= t.hour < 22 else cfg.price_valley)
        self.energy_a += e_a
        self.energy_b += e_b
        self.cost += price * (e_a + e_b)
        self.peak_power = max(self.peak_power, p_a + p_b)
        if ac_a != self.prev_ac_a:
            self.switch += 1
        if ac_b != self.prev_ac_b:
            self.switch += 1

        # 舒适度按“下一步开始时人在哪”计
        t2 = t + timedelta(minutes=cfg.dt_minutes)
        state2 = self.schedule.state_at(t2)
        lo, hi = comfort_band(t2, cfg)
        if state2 == "A":
            self.occ_steps_a += 1
            if self.temp_a < lo or self.temp_a > hi:
                self.bad_a += 1
        elif state2 == "B":
            self.occ_steps_b += 1
            if self.temp_b < lo or self.temp_b > hi:
                self.bad_b += 1

        self.prev_ac_a = ac_a
        self.prev_ac_b = ac_b
        self.prev_comp_a = comp_a
        self.prev_comp_b = comp_b
        self.t = t2
        return {
            "t": t2,
            "state": state2,
            "state_before": state,
            "temp_a": self.temp_a,
            "temp_b": self.temp_b,
            "energy_a": e_a,
            "energy_b": e_b,
            "price": price,
        }

    # ---------- 汇总 ----------
    @property
    def occ_steps(self) -> int:
        return self.occ_steps_a + self.occ_steps_b

    @property
    def bad(self) -> int:
        return self.bad_a + self.bad_b

    @property
    def occ_discomfort(self) -> float:
        return self.bad / max(self.occ_steps, 1)

    @property
    def energy(self) -> float:
        return self.energy_a + self.energy_b
