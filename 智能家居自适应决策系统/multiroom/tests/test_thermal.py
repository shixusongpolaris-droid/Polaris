"""双区热模型的基本守恒/耦合测试。"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))

from multiroom_env import MultiRoomConfig, MultiRoomSim  # noqa: E402


def main():
    # 只留房间间耦合：A 30, B 20，一步后 A 降、B 升，总和不变
    cfg = MultiRoomConfig(
        ua_a=0.0, ua_b=0.0, gain_a=0.0, gain_b=0.0,
        solar_a=0.0, solar_b=0.0, g_closed=0.1,
        temp_init_a=30.0, temp_init_b=20.0,
    )
    sim = MultiRoomSim(cfg, datetime(2026, 10, 12, 0, 0))
    s = sim.temp_a + sim.temp_b
    sim.step(0, 0)
    assert sim.temp_a < 30.0 and sim.temp_b > 20.0
    assert abs((sim.temp_a + sim.temp_b) - s) < 1e-9, (sim.temp_a, sim.temp_b)
    print(f"coupling: 30/20 -> {sim.temp_a:.3f}/{sim.temp_b:.3f}, sum ok")

    # AC 开：能量 > 0，A 温度不高于关空调
    cfg2 = MultiRoomConfig(
        ua_a=0.0, ua_b=0.0, gain_a=0.0, gain_b=0.0,
        solar_a=0.0, solar_b=0.0, g_closed=0.0,
        temp_init_a=28.0, temp_init_b=28.0,
    )
    sim2 = MultiRoomSim(cfg2, datetime(2026, 10, 12, 0, 0))
    sim2.step(0, 0)
    t_off = sim2.temp_a
    e_off = sim2.energy_a
    sim3 = MultiRoomSim(cfg2, datetime(2026, 10, 12, 0, 0))
    sim3.step(1, 0)          # A 开 23°C
    assert sim3.energy_a > e_off
    assert sim3.temp_a < t_off
    print(f"AC: off T={t_off:.3f} E={e_off:.4f}; "
          f"on T={sim3.temp_a:.3f} E={sim3.energy_a:.4f}")

    # C3：A 31°C，即使动作 0 也强制开
    sim4 = MultiRoomSim(MultiRoomConfig(temp_init_a=31.0), datetime(2026, 10, 12, 0, 0))
    sim4.step(0, 0)
    assert sim4.prev_ac_a == 1
    print("C3 hot override: OK")

    print("PASS thermal/coupling")


if __name__ == "__main__":
    main()
