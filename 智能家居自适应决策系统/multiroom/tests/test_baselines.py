"""Phase 1 基线合理性测试：independent / coord / always-on 的关系。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "multiroom" / "src"))
sys.path.insert(0, str(ROOT / "multiroom" / "scripts"))

from multiroom_env import MultiRoomConfig, MultiRoomSim  # noqa: E402
import phase0_diagnostic as p0  # noqa: E402


def run(policy):
    sim = MultiRoomSim(MultiRoomConfig(), p0.START)
    for _ in range(7 * 96):
        a, b = policy(sim)
        sim.step(a, b)
    return sim


def main():
    ind = run(p0.independent)
    c15 = run(lambda s: p0.coordinated(s, 15))
    c45 = run(lambda s: p0.coordinated(s, 45))
    always = run(p0.always_on)

    assert ind.cost > 0 and ind.occ_discomfort > 0
    assert c15.cost >= ind.cost * 0.99
    assert c15.occ_discomfort <= ind.occ_discomfort + 1e-9
    assert c45.occ_discomfort <= c15.occ_discomfort + 1e-9
    assert always.cost > ind.cost
    assert always.occ_discomfort <= ind.occ_discomfort + 1e-9
    print(f"independent cost={ind.cost:.2f} occ={ind.occ_discomfort:.2%}")
    print(f"coord15     cost={c15.cost:.2f} occ={c15.occ_discomfort:.2%}")
    print(f"coord45     cost={c45.cost:.2f} occ={c45.occ_discomfort:.2%}")
    print(f"always_on   cost={always.cost:.2f} occ={always.occ_discomfort:.2%}")
    print("PASS phase1 baselines")


if __name__ == "__main__":
    main()
