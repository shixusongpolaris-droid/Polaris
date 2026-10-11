"""MDP 环境子包。

模块分工：
    config.py      所有可调参数（与 MDP 文档一一对应）
    simulator.py   状态转移：环境物理演变
    reward.py      奖励函数：舒适度代价 + 场景化加权的能耗代价
    home_env.py    Gymnasium 接口包装
"""

from .config import ActionConfig, ConstraintConfig, RewardConfig, SimConfig
from .home_env import SmartHomeEnv
from .reward import RewardCalculator
from .simulator import HomeSimulator, decode_action, encode_action

__all__ = [
    "SimConfig",
    "ActionConfig",
    "ConstraintConfig",
    "RewardConfig",
    "HomeSimulator",
    "RewardCalculator",
    "SmartHomeEnv",
    "decode_action",
    "encode_action",
]
