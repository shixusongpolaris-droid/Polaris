"""3 房间 Phase 0 环境：一人在 A/B/C/外出；三台 AC 共享总功率上限。

目的：不是训练 RL，而是先看“多房间 + 总功率上限”下，
独立规则 vs 协调规则 vs 全开 的协调间隙有多大。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

AC_SETPOINTS = [23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0]
ROOMS = ("A", "B", "C")


@dataclass
class Config3:
    dt_minutes: int = 15
    ua: tuple = (0.15175, 0.160, 0.140)
    gain: tuple = (0.8925, 0.900, 0.880)
    solar: tuple = (0.215, 0.150, 0.120)
    temp_init: tuple = (26.0, 28.0, 27.0)
    g_closed: float = 0.01
    g_open: float = 0.12
    temp_outdoor_mean: float = 22.0
    temp_outdoor_swing: float = 3.0
    ac_coef: float = 1.83
    ac_max_cooling: float = 2.1175
    cop: float = 3.0
    fan_kw: float = 0.04
    rated_kw: float = 1.1
    cooling_capacity_kw: float = 3.5
    ac_startup_kwh: float = 0.010
    total_power_cap_kw: float = 1.6
    price_peak: float = 0.617
    price_valley: float = 0.307
    # 每间舒适带：A/B/C 的 (awake_lo, awake_hi, sleep_lo, sleep_hi)
    comfort: tuple = ((22.0, 26.0, 24.0, 27.0),
                      (23.0, 27.0, 25.0, 28.0),
                      (22.5, 26.5, 24.5, 27.5))
    sleep_start_hour: int = 23
    sleep_end_hour: int = 7
    temp_safe_min: float = 16.0
    temp_safe_max: float = 30.0


class WeekSchedule3:
    """一人在 A/B/C/外出；offset_min 是全局偏移（0~59 分钟）。"""

    def __init__(self, start: datetime, offset_min: int = 0):
        self.start = start
        self.offset = timedelta(minutes=int(offset_min))
        self._cache = None

    def _template(self, t: datetime):
        tt = t - self.offset
        if tt.weekday() < 5:
            return [(0, 7, "A"), (7, 9, "A"), (9, 12, "C"),
                    (12, 14, "out"), (14, 18, "C"), (18, 22, "A"),
                    (22, 24, "B")]
        return [(0, 9, "A"), (9, 12, "A"), (12, 14, "out"),
                (14, 17, "C"), (17, 19, "B"), (19, 22, "A"),
                (22, 24, "B")]

    def state_at(self, t: datetime) -> str:
        h = t.hour + t.minute / 60.0
        for a, b, s in self._template(t):
            if a <= h < b:
                return s
        return "out"

    def next_transition(self, t: datetime):
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


def is_sleep(t: datetime, cfg: Config3) -> bool:
    h = t.hour
    return h >= cfg.sleep_start_hour or h < cfg.sleep_end_hour


def comfort_band(room: int, t: datetime, cfg: Config3):
    lo, hi = cfg.comfort[room][:2]
    if is_sleep(t, cfg):
        lo, hi = cfg.comfort[room][2:]
    return lo, hi


class Sim3:
    def __init__(self, cfg: Config3, start: datetime, offset_min: int = 0):
        self.cfg = cfg
        self.start = start
        self.schedule = WeekSchedule3(start, offset_min)
        self.reset()

    def reset(self):
        cfg = self.cfg
        self.t = self.start
        self.temp = list(cfg.temp_init)
        self.energy = [0.0, 0.0, 0.0]
        self.cost = 0.0
        self.occ_steps = [0, 0, 0]
        self.bad = [0, 0, 0]
        self.switch = 0
        self.peak_power = 0.0
        self.cap_binds = 0
        self.unserved = 0.0
        self.prev_ac = [0, 0, 0]
        self.prev_comp = [False, False, False]

    def _outdoor(self, t: datetime) -> float:
        h = t.hour + t.minute / 60.0
        return (self.cfg.temp_outdoor_mean
                + self.cfg.temp_outdoor_swing
                * math.cos(2 * math.pi * (h - 15.0) / 24.0))

    def _solar(self, t: datetime) -> float:
        h = t.hour + t.minute / 60.0
        if 6.0 <= h <= 18.0:
            return math.sin(math.pi * (h - 6.0) / 12.0)
        return 0.0

    def _cooling(self, temp: float, ac_idx: int) -> float:
        if ac_idx <= 0:
            return 0.0
        sp = AC_SETPOINTS[ac_idx - 1]
        return min(max(self.cfg.ac_coef * (temp - sp), 0.0),
                   self.cfg.ac_max_cooling)

    def step(self, ac: tuple, door_open: bool = False):
        """ac = (a,b,c)，每项 0~7。"""
        cfg = self.cfg
        t = self.t
        ac = list(ac)
        # C3
        for i in range(3):
            if self.temp[i] > cfg.temp_safe_max:
                ac[i] = 1
            elif self.temp[i] < cfg.temp_safe_min:
                ac[i] = 0

        c = [self._cooling(self.temp[i], ac[i]) for i in range(3)]
        p_comp = [min(cfg.cooling_capacity_kw * c[i] / cfg.ac_max_cooling
                      / cfg.cop, cfg.rated_kw) if c[i] > 1e-9 else 0.0
                  for i in range(3)]
        p_fan = [cfg.fan_kw if ac[i] > 0 else 0.0 for i in range(3)]
        p_total = sum(p_comp) + sum(p_fan)
        cap_bind = p_total > cfg.total_power_cap_kw + 1e-12
        if cap_bind:
            self.cap_binds += 1
            fan_total = sum(p_fan)
            comp_total = sum(p_comp)
            room = cfg.total_power_cap_kw - fan_total
            f = max(0.0, min(1.0, room / comp_total)) if comp_total > 1e-9 else 0.0
            self.unserved += sum(c[i] * (1.0 - f) for i in range(3))
            c = [ci * f for ci in c]
            p_comp = [pi * f for pi in p_comp]

        e = [0.0, 0.0, 0.0]
        comp = [False, False, False]
        for i in range(3):
            if ac[i] <= 0:
                continue
            e[i] = (p_comp[i] + p_fan[i]) * cfg.dt_minutes / 60.0
            comp[i] = c[i] > 1e-9
            if comp[i] and not self.prev_comp[i]:
                e[i] += cfg.ac_startup_kwh

        t_out = self._outdoor(t)
        solar = self._solar(t)
        g = cfg.g_open if door_open else cfg.g_closed
        # 门：A-B, B-C（链式）
        d_a = (-cfg.ua[0] * (self.temp[0] - t_out)
               - g * (self.temp[0] - self.temp[1])
               + cfg.gain[0] + cfg.solar[0] * solar - c[0])
        d_b = (-cfg.ua[1] * (self.temp[1] - t_out)
               - g * (self.temp[1] - self.temp[0])
               - g * (self.temp[1] - self.temp[2])
               + cfg.gain[1] + cfg.solar[1] * solar - c[1])
        d_c = (-cfg.ua[2] * (self.temp[2] - t_out)
               - g * (self.temp[2] - self.temp[1])
               + cfg.gain[2] + cfg.solar[2] * solar - c[2])
        self.temp[0] = max(-10.0, min(45.0, self.temp[0] + d_a))
        self.temp[1] = max(-10.0, min(45.0, self.temp[1] + d_b))
        self.temp[2] = max(-10.0, min(45.0, self.temp[2] + d_c))

        price = cfg.price_peak if 6 <= t.hour < 22 else cfg.price_valley
        for i in range(3):
            self.energy[i] += e[i]
        self.cost += price * sum(e)
        self.peak_power = max(self.peak_power, sum(p_comp) + sum(p_fan))
        self.switch += sum(1 for i in range(3) if ac[i] != self.prev_ac[i])

        t2 = t + timedelta(minutes=cfg.dt_minutes)
        state2 = self.schedule.state_at(t2)
        for i, room in enumerate(ROOMS):
            if state2 == room:
                self.occ_steps[i] += 1
                lo, hi = comfort_band(i, t2, cfg)
                if self.temp[i] < lo or self.temp[i] > hi:
                    self.bad[i] += 1
        self.prev_ac = ac
        self.prev_comp = comp
        self.t = t2
        return {"t": t2, "state": state2, "temp": list(self.temp),
                "energy": e, "price": price, "cap_bind": cap_bind}

    @property
    def occ_discomfort(self) -> float:
        return sum(self.bad) / max(sum(self.occ_steps), 1)
