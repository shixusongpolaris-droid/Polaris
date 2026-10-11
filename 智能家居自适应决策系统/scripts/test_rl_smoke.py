r"""
强化学习链路验证 —— 确认真能训练出模型

用途：
    环境自检只验证"包能导入"，本脚本进一步验证"真能跑训练"。
    用一个最简单的标准环境（平衡杆 CartPole）训练一小会儿，
    再和"纯随机动作"对比，确认训练确实学到了东西。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate
    python scripts\test_rl_smoke.py

说明：
    CartPole 与本项目无关，只是"能不能跑"的验证。
    本项目真正的环境要按 MDP 定义自己写，放在 src\env\。
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import numpy as np
import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.evaluation import evaluate_policy
from stable_baselines3.common.monitor import Monitor


def evaluate_random_actions(n_episodes=50, seed=0):
    """
    评估"纯随机动作"的表现，作为基准。

    为什么不直接用未训练的 PPO 当基准：
        未训练的网络在 CartPole 上偶尔能靠运气拿满分（500），
        用它当基准会导致结论不稳定、时而误报"训练无提升"。
        纯随机动作的期望分数稳定在 20 左右，是可靠的对照。
    """
    env = gym.make("CartPole-v1")
    rng = np.random.default_rng(seed)
    returns = []
    for _ in range(n_episodes):
        obs, _ = env.reset(seed=int(rng.integers(1_000_000)))
        total, done = 0.0, False
        while not done:
            action = env.action_space.sample()
            obs, reward, terminated, truncated, _ = env.step(action)
            total += reward
            done = terminated or truncated
        returns.append(total)
    env.close()
    return float(np.mean(returns))


def main():
    print("=" * 60)
    print(" 强化学习链路验证（CartPole 冒烟测试）")
    print("=" * 60)

    # 1. 创建环境
    env = Monitor(gym.make("CartPole-v1"))
    print("\n[1] 环境创建成功")
    print(f"    观测空间: {env.observation_space}")
    print(f"    动作空间: {env.action_space}")

    # 2. 基准：纯随机动作
    print("\n[2] 评估纯随机动作（基准，50 回合）")
    mean_random = evaluate_random_actions(n_episodes=50, seed=0)
    print(f"    随机动作平均回报: {mean_random:.1f}")

    # 3. 训练
    total_steps = 8000
    print(f"\n[3] 开始训练 PPO（{total_steps} 步，约十几秒）")
    model = PPO("MlpPolicy", env, verbose=0, seed=42)
    model.learn(total_timesteps=total_steps)
    print("    训练完成")

    # 4. 评估训练后的策略
    print("\n[4] 评估训练后的策略（10 回合）")
    mean_trained, std_trained = evaluate_policy(model, env, n_eval_episodes=10)
    print(f"    训练后平均回报: {mean_trained:.1f} (±{std_trained:.1f})")

    # 5. 结论
    print("\n[5] 结论")
    print(f"    随机动作 {mean_random:.1f}  ->  训练后 {mean_trained:.1f}")
    print(f"    CartPole 满分 500，随机动作约 20，训练后应达到 200 以上")

    if mean_trained >= 200:
        print("    [通过] 训练效果显著，强化学习链路可用。")
    elif mean_trained > mean_random * 2:
        print("    [通过] 训练有提升，但幅度一般（可增加训练步数再看看）。")
    else:
        print("    [异常] 训练没有提升，请把上面的完整输出发出来排查。")

    print("\n    注：本测试只验证环境可用，与本项目算法无关。")
    print("        本项目需要在 src\\env\\ 里按 MDP 定义编写自己的环境。")
    print("=" * 60)

    env.close()


if __name__ == "__main__":
    main()
