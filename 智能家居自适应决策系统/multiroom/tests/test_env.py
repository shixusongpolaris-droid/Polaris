"""Phase 1 smoke test：Gym 2 房间环境。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))

import numpy as np  # noqa: E402

from gym_env import GymMultiRoomEnv  # noqa: E402


def main():
    env = GymMultiRoomEnv(energy_scale=30.0, seed=9100)
    obs, info = env.reset(seed=9100)
    assert obs.shape == (15,), obs.shape
    assert env.action_space.nvec.tolist() == [8, 8]
    assert np.isfinite(obs).all()

    total_r = 0.0
    steps = 0
    while True:
        a = env.action_space.sample()
        obs, r, term, trunc, info = env.step(a)
        total_r += r
        steps += 1
        assert obs.shape == (15,)
        assert np.isfinite(obs).all()
        assert info["energy"] >= 0
        assert 0.0 <= info["occ_discomfort"] <= 1.0
        if term or trunc:
            break
    assert steps == 672, steps
    print(f"random policy: steps={steps} reward={total_r:.2f} "
          f"cost={info['cost']:.4f} occ={info['occ_discomfort']:.2%}")

    # C3：31°C 时强制最低档
    env.reset(seed=1)
    env.sim.temp_a = 31.0
    env.step([0, 0])
    assert env.sim.prev_ac_a == 1, env.sim.prev_ac_a
    print("C3 hot override: OK")

    # C3：15°C 时强制关
    env.reset(seed=2)
    env.sim.temp_b = 15.0
    env.step([1, 1])
    assert env.sim.prev_ac_b == 0, env.sim.prev_ac_b
    print("C3 cold override: OK")

    print("PASS phase1 smoke")


if __name__ == "__main__":
    main()
