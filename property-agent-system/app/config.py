"""系统配置：路径、SLA 规则、工种映射等。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PROPERTY_DATA_DIR", BASE_DIR / "data"))
REPORT_DIR = Path(os.environ.get("PROPERTY_REPORT_DIR", BASE_DIR / "reports"))
DB_PATH = Path(os.environ.get("PROPERTY_DB_PATH", DATA_DIR / "property.db"))
HISTORY_CSV = DATA_DIR / "historical_orders.csv"
PRIORS_PATH = DATA_DIR / "dispatch_priors.json"
EVAL_PATH = REPORT_DIR / "agent_evaluation.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_NAME = "智修云 · 小区物业智能体"
APP_VERSION = "1.1.0"

# 报修类型 -> (设施类型, 默认紧急程度, 处理时限分钟, 责任工种)
TYPE_RULES = {
    "电梯困人": ("电梯", "紧急", 30, ["电梯维保"]),
    "电梯故障": ("电梯", "高", 60, ["电梯维保"]),
    "水管爆裂": ("给排水", "紧急", 30, ["水电工"]),
    "水管漏水": ("给排水", "高", 120, ["水电工"]),
    "下水道堵塞": ("给排水", "中", 120, ["水电工"]),
    "电路故障": ("电气", "高", 60, ["电工"]),
    "燃气泄漏": ("燃气", "紧急", 15, ["燃气抢修"]),
    "消防隐患": ("消防", "紧急", 15, ["秩序维护"]),
    "门禁故障": ("门禁", "中", 240, ["综合维修"]),
    "公共照明": ("照明", "中", 240, ["电工"]),
    "墙面地面破损": ("土建", "低", 480, ["土建维修", "综合维修"]),
    "环境卫生": ("环境", "低", 480, ["保洁"]),
    "其他报修": ("其他", "中", 240, ["综合维修"]),
}

URGENCY_ORDER = {"紧急": 4, "高": 3, "中": 2, "低": 1}
URGENCY_COLOR = {"紧急": "#ff4d4f", "高": "#fa8c16", "中": "#fadb14", "低": "#52c41a"}

# 接单时限（分钟）：紧急工单必须在更短时间内响应
ACCEPT_SLA_MINUTES = {"紧急": 10, "高": 15, "中": 30, "低": 60}
# 到达/处理超时提前预警（分钟）
WARNING_BEFORE_DEADLINE = 10

TRADE_TO_ZONE_HINT = {
    "电梯维保": "A",
    "水电工": "A",
    "电工": "A",
    "燃气抢修": "A",
    "秩序维护": "A",
    "综合维修": "B",
    "保洁": "C",
    "土建维修": "B",
}

STATUS_LABELS = {
    "submitted": "已提交",
    "understood": "已理解",
    "assigned": "待接单",
    "accepted": "已接单",
    "processing": "处理中",
    "completed": "已完工",
    "returned": "退回整改",
    "closed": "已闭环",
    "cancelled": "已取消",
}

STATUS_COLORS = {
    "submitted": "#8c8c8c",
    "understood": "#597ef7",
    "assigned": "#fa8c16",
    "accepted": "#13c2c2",
    "processing": "#2f54eb",
    "completed": "#722ed1",
    "returned": "#ff4d4f",
    "closed": "#52c41a",
    "cancelled": "#bfbfbf",
}

OPEN_STATUSES = {"submitted", "understood", "assigned", "accepted", "processing", "completed", "returned"}


def type_meta(repair_type: str):
    return TYPE_RULES.get(repair_type or "", TYPE_RULES["其他报修"])


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "未知")

# ===================== 小区守护 =====================
# 连续未出门阈值（天）：普通住户 3 天；独居老人 1 天；慢病/行动不便/高龄 2 天
INACTIVITY_RULES = {
    "default": 3.0,
    "独居老人": 1.0,
    "独居": 1.0,
    "慢病": 2.0,
    "行动不便": 2.0,
    "高龄": 2.0,
    "残障": 2.0,
}
INACTIVITY_ALERT_LEVELS = [
    (3.0, "L3", "三级紧急"),
    (1.0, "L2", "二级预警"),
    (0.0, "L1", "一级提醒"),
]
COMMUNITY_SCAN_INTERVAL_SECONDS = int(os.environ.get("COMMUNITY_SCAN_INTERVAL_SECONDS", "120"))
COMMUNITY_MONITOR_ENABLED = os.environ.get("COMMUNITY_MONITOR_ENABLED", "1") != "0"

# ===================== 停车计费 =====================
PARKING_RULES = {
    "free_minutes": 30,
    "rate_per_hour": 2.0,
    "daily_cap": 30.0,
    "monthly_fee": 300.0,
}
