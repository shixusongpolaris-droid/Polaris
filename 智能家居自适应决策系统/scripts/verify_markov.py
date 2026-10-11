r"""
马尔可夫性检验 —— 验证策略确实"只看状态"

背景（对应 MDP 文档第 9.5 节）：
    MDP 框架要求策略具有 π(a|s) 的形式，即动作只依赖当前状态，
    不依赖历史。所有主流强化学习算法都建立在这一形式上。

    但策略由神经网络实现，无法直接断言其依赖关系，
    只能通过"行为统计"来检验：
        在相似状态下，策略输出的动作应当一致（或分布稳定）。

本脚本的做法：
    1. 让某个策略跑若干回合，收集 (状态, 动作) 数据对
    2. 把状态按关键维度分箱（室温 × 小时）
    3. 统计每个箱内动作的分布，计算熵与一致率
    4. 与"随机策略"对比，说明方法能区分二者

对照设计（三组，用于说明检验方法的有效性）：
    策略 A  规则控制      —— 确定性策略，理论上应完全一致
    策略 B  随机策略      —— 与状态无关，熵应为最大值
    策略 C  状态相关随机  —— 动作依赖状态但带随机性（模拟训练后的策略）

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate
    python scripts\verify_markov.py
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import numpy as np
from matplotlib import font_manager
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.env import SmartHomeEnv, encode_action

_avail = {f.name for f in font_manager.fontManager.ttflist}
for _c in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if _c in _avail:
        plt.rcParams["font.sans-serif"] = [_c]
        break
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = PROJECT_ROOT / "models" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 待检验的三个策略
# ============================================================
def policy_rule(env):
    """策略 A：规则控制（确定性，只依赖状态）。"""
    obs = env.get_obs_dict()
    occupied = bool(obs["occ_a"] or obs["occ_b"])
    if not occupied:
        return encode_action(0, 0, 0, env.act_cfg)
    temp = obs["temp_in"]
    if temp > 25.0:
        ac = 1
    elif temp < 23.0:
        ac = 0
    else:
        ac = env.sim.state.ac_idx
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(ac, light, 0, env.act_cfg)


def policy_random(env):
    """策略 B：随机动作（与状态无关）。"""
    return env.action_space.sample()


def policy_state_random(env):
    """策略 C：动作依赖状态但带随机性。

    模拟"训练后的神经网络策略"：以状态为主要依据，但存在一定随机性
    （例如 PPO 的随机采样、策略网络的软输出）。
    """
    obs = env.get_obs_dict()
    temp = obs["temp_in"]
    occupied = bool(obs["occ_a"] or obs["occ_b"])

    # 以温度为主依据，按温度带决定空调档位的概率分布
    if not occupied:
        ac_choices, probs = [0, 1], [0.9, 0.1]
    elif temp > 27:
        ac_choices, probs = [1, 2, 3], [0.7, 0.2, 0.1]
    elif temp > 25:
        ac_choices, probs = [0, 1, 2], [0.2, 0.6, 0.2]
    elif temp > 23:
        ac_choices, probs = [0, 1, 2], [0.5, 0.4, 0.1]
    else:
        ac_choices, probs = [0, 1], [0.9, 0.1]

    ac = env.np_random.choice(ac_choices, p=probs)
    light = 1 if obs["light_in"] < 300 else 0
    return encode_action(int(ac), light, 0, env.act_cfg)


# ============================================================
# 数据采集与统计
# ============================================================
KEY_DIMS = ["temp_in", "hour"]      # 用于分箱的关键维度
BIN_TEMP = 2.0                      # 温度分箱宽度（°C）
BIN_HOUR = 3                        # 小时分箱宽度

# 是否把"占用状态"纳入分箱维度。
# 说明：规则控制策略的决策同时依赖温度与占用，若分箱时漏掉占用，
# 同一箱内会同时存在"有人开空调"与"无人关空调"两种动作，
# 导致一致率被人为压低。这一点本身也说明：一致率检验能够
# 暴露出"策略依赖了分箱未覆盖的状态变量"。
USE_OCCUPANCY_IN_BIN = True


def state_key(obs_dict) -> tuple:
    """把观测字典映射为状态箱编号。

    注意 key 只由"真实状态变量"构成，不包含策略的内部逻辑，
    否则就变成了循环论证。
    """
    key = (round(obs_dict["temp_in"] / BIN_TEMP),
           int(obs_dict["hour"]) // BIN_HOUR)
    if USE_OCCUPANCY_IN_BIN:
        key = key + (1 if (obs_dict["occ_a"] or obs_dict["occ_b"]) else 0,)
    return key


def collect(env, policy_fn, n_episodes=2, seed0=1000):
    """采集 (状态键, 动作) 样本。"""
    samples = []
    for ep in range(n_episodes):
        env.reset(seed=seed0 + ep)
        while True:
            action = policy_fn(env)
            obs_dict = env.get_obs_dict()
            key = state_key(obs_dict)
            samples.append((key, int(action)))
            _, _, term, trunc, _ = env.step(action)
            if term or trunc:
                break
    return samples


def entropy(counter: Counter) -> float:
    """计算动作分布的香农熵（以 2 为底，单位 bit）。"""
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counter.values():
        p = c / total
        if p > 0:
            h -= p * np.log2(p)
    return float(h)


def analyse(samples, n_actions: int, min_count: int = 5):
    """按状态箱统计动作分布。

    返回：
        dict(
          n_states       唯一状态箱数量,
          n_samples      样本总数,
          mean_entropy   箱内平均熵（越低越确定性）,
          consistency    一致率 = 各箱内最常见动作占比的加权平均,
          max_entropy    理论最大熵 = log2(n_actions),
          bins           明细
        )
    """
    groups = defaultdict(Counter)
    for key, act in samples:
        groups[key][act] += 1

    bins = []
    weighted_consistency = 0.0
    weighted_entropy = 0.0
    total = 0
    for key, cnt in groups.items():
        n = sum(cnt.values())
        if n < min_count:
            continue
        most_common = cnt.most_common(1)[0][1]
        consistency = most_common / n
        h = entropy(cnt)
        bins.append({
            "key": key, "n": n, "consistency": consistency,
            "entropy": h, "dist": dict(cnt),
        })
        weighted_consistency += consistency * n
        weighted_entropy += h * n
        total += n

    if total == 0:
        return None

    return {
        "n_states": len(bins),
        "n_samples": total,
        "mean_entropy": weighted_entropy / total,
        "consistency": weighted_consistency / total,
        "max_entropy": float(np.log2(n_actions)),
        "bins": sorted(bins, key=lambda d: -d["n"]),
    }


def print_result(name: str, res, n_actions: int):
    if res is None:
        print(f"  {name}: 样本不足，无法统计（请增加回合数）")
        return
    print(f"\n  ── {name} ──")
    print(f"    样本数           : {res['n_samples']}")
    print(f"    唯一状态箱数     : {res['n_states']}")
    print(f"    箱内平均熵       : {res['mean_entropy']:.3f} bit "
          f"（理论最大 {res['max_entropy']:.3f} bit）")
    print(f"    动作一致率       : {res['consistency']:.1%}")
    print(f"    熵占比           : {res['mean_entropy'] / res['max_entropy']:.1%}"
          f"  （越低表示策略越依赖状态）")


def main():
    print("=" * 70)
    print(" 马尔可夫性检验 —— 策略是否只依赖当前状态")
    print("=" * 70)
    dims = f"室温每 {BIN_TEMP}°C 一箱 × 时段每 {BIN_HOUR} 小时一箱"
    if USE_OCCUPANCY_IN_BIN:
        dims += " × 是否有人"
    print(f"\n 分箱方式：{dims}")

    env = SmartHomeEnv(seed=42)
    n_actions = env.action_space.n

    policies = [
        ("策略 A：规则控制（确定性）", policy_rule),
        ("策略 C：状态相关 + 随机性（模拟神经网络策略）", policy_state_random),
        ("策略 B：随机策略（与状态无关，对照）", policy_random),
    ]

    results = {}
    for name, fn in policies:
        res = analyse(collect(env, fn, n_episodes=2, seed0=1000), n_actions)
        results[name] = res
        print_result(name, res, n_actions)

    # ---------- 结论 ----------
    print("\n" + "=" * 70)
    print(" 结论")
    print("=" * 70)

    res_rule = results["策略 A：规则控制（确定性）"]
    res_state = results["策略 C：状态相关 + 随机性（模拟神经网络策略）"]
    res_rand = results["策略 B：随机策略（与状态无关，对照）"]

    if res_rule and res_state and res_rand:
        print(f"\n  一致率对比：")
        print(f"    规则控制（完全依赖状态）      : {res_rule['consistency']:.1%}")
        print(f"    状态相关+随机性               : {res_state['consistency']:.1%}")
        print(f"    随机策略（完全不依赖状态）    : {res_rand['consistency']:.1%}")

        print(f"\n  解读：")
        if res_rule["consistency"] > 0.98:
            print(f"    · 规则控制的一致率接近 100%，符合'确定性策略只依赖状态'的预期。")
            print(f"      这说明：只要策略确实是 π(a|s) 形式，检验就能识别出来。")
        if res_rand["consistency"] < res_state["consistency"]:
            print(f"    · 随机策略的一致率明显低于状态相关策略，")
            print(f"      说明本检验方法能有效区分'依赖状态'与'不依赖状态'。")
        print(f"    · 状态相关+随机策略的一致率介于两者之间，")
        print(f"      对应强化学习策略的实际情况：动作由状态决定，但含采样随机性。")

    # ---------- 画图 ----------
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))

    # 左图：一致率对比
    ax = axes[0]
    names = ["规则控制\n(确定性)", "状态相关\n+随机", "随机策略\n(无关)"]
    cons = [res_rule["consistency"], res_state["consistency"],
            res_rand["consistency"]]
    bars = ax.bar(names, [c * 100 for c in cons],
                  color=["#2ca02c", "#1f77b4", "#d62728"])
    for b, c in zip(bars, cons):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5,
                f"{c:.1%}", ha="center", fontsize=10)
    ax.set_ylabel("动作一致率 (%)")
    ax.set_ylim(0, 115)
    ax.set_title("同一状态下的动作一致率", fontsize=12)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    # 中图：熵对比
    ax = axes[1]
    ents = [res_rule["mean_entropy"], res_state["mean_entropy"],
            res_rand["mean_entropy"]]
    ax.bar(names, ents, color=["#2ca02c", "#1f77b4", "#d62728"])
    ax.axhline(res_rand["max_entropy"], color="black", linestyle="--",
               label=f"理论最大熵 {res_rand['max_entropy']:.2f} bit")
    for i, e in enumerate(ents):
        ax.text(i, e + 0.05, f"{e:.3f}", ha="center", fontsize=10)
    ax.set_ylabel("箱内平均熵 (bit)")
    ax.set_title("状态箱内的动作不确定性", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y", linestyle=":")

    # 右图：状态相关策略的动作分布热图（温度 × 小时）
    # 说明：分析箱中包含占用维度（共 3 维），而热图只能展示 2 维，
    # 因此按 (温度, 小时) 聚合，各格颜色表示"开空调概率"的期望值。
    ax = axes[2]
    if res_state:
        temps = sorted({b["key"][0] for b in res_state["bins"]})
        hours = sorted({b["key"][1] for b in res_state["bins"]})
        mat = np.full((len(temps), len(hours)), np.nan)
        for b in res_state["bins"]:
            i = temps.index(b["key"][0])
            j = hours.index(b["key"][1])
            n = b["n"]
            ac_on = sum(c for a, c in b["dist"].items() if (a // 6) > 0)
            p = ac_on / n
            if np.isnan(mat[i, j]):
                mat[i, j] = p
            else:
                mat[i, j] = 0.5 * (mat[i, j] + p)
        im = ax.imshow(mat, aspect="auto", cmap="RdYlBu_r",
                       vmin=0, vmax=1, origin="lower")
        ax.set_xticks(range(len(hours)))
        ax.set_xticklabels([f"{h * BIN_HOUR}h" for h in hours], fontsize=8)
        ax.set_yticks(range(len(temps)))
        ax.set_yticklabels([f"{t * BIN_TEMP:.0f}°C" for t in temps], fontsize=8)
        ax.set_xlabel("时段")
        ax.set_ylabel("室温")
        ax.set_title("状态相关策略：开空调概率\n（颜色越红概率越高）", fontsize=11)
        fig.colorbar(im, ax=ax, fraction=0.046)

    fig.suptitle("马尔可夫性检验：策略动作对状态（室温 × 时段）的依赖程度",
                 fontsize=13)
    fig.tight_layout()
    out = FIG_DIR / "markov_check.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"\n  图已保存: {out}")
    print("=" * 70)

    env.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
