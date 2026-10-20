"""社区场景 Agent：小区守护 Agent 与停车收费 Agent。"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import INACTIVITY_RULES
from .base import AgentDecision, BaseAgent

RISK_LABELS = {
    "normal": "正常",
    "watch": "关注",
    "warning": "预警",
    "critical": "紧急",
}
RISK_LEVELS = {"watch": "L1", "warning": "L2", "critical": "L3"}
LEVEL_LABELS = {"L1": "一级提醒", "L2": "二级预警", "L3": "三级紧急"}


def parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


class CommunityGuardAgent(BaseAgent):
    """根据住户连续未出门天数、年龄与标签判断风险，生成关怀预警。"""

    code = "community_guard"
    name = "小区守护 Agent"
    action = "inactivity_scan"

    def threshold_for(self, tags: List[str]) -> float:
        values = [INACTIVITY_RULES["default"]]
        for tag in tags:
            for key, value in INACTIVITY_RULES.items():
                if key != "default" and key in tag:
                    values.append(float(value))
        return min(values)

    def evaluate(self, resident: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        tags = resident.get("tags") or []
        if isinstance(tags, str):
            tags = parse_tags(tags)
        presence = resident.get("presence") or "home"
        last_exit = parse_dt(resident.get("last_exit_at"))
        last_entry = parse_dt(resident.get("last_entry_at"))
        created = parse_dt(resident.get("created_at")) or now
        now = now.replace(microsecond=0)

        if presence == "away":
            inactive_days = round(max(0.0, (now - (last_entry or created)).total_seconds() / 86400), 1)
            return {
                "presence": "away",
                "presence_label": "外出",
                "inactive_days": inactive_days,
                "threshold_days": self.threshold_for(tags),
                "risk_level": "normal",
                "risk_label": "正常",
                "should_alert": False,
                "alert_level": None,
                "level_label": None,
                "message": f"住户当前在外，已外出 {inactive_days:.1f} 天，暂不触发未出门预警",
                "rationale": "出入口事件显示住户最近一次为外出，按外出状态处理。",
                "confidence": 0.9,
            }

        since = last_exit or last_entry or created
        inactive_days = round(max(0.0, (now - since).total_seconds() / 86400), 1)
        threshold = self.threshold_for(tags)
        if inactive_days < threshold:
            risk_level = "normal"
        elif inactive_days < threshold + 1:
            risk_level = "watch"
        elif inactive_days < threshold + 3:
            risk_level = "warning"
        else:
            risk_level = "critical"

        away_until = parse_dt(resident.get("away_until"))
        planned_absence = bool(away_until and now < away_until)
        should_alert = risk_level != "normal" and not planned_absence
        alert_level = RISK_LEVELS.get(risk_level)
        tag_text = "、".join(tags) if tags else "普通住户"
        if planned_absence:
            message = f"住户已登记外出（预计 {resident.get('away_until')} 返回），不触发预警"
        elif risk_level == "critical":
            message = f"{resident.get('name', '住户')}已连续 {inactive_days:.1f} 天未出门（阈值 {threshold:.0f} 天，{tag_text}），建议立即上门探访并联系家属"
        elif risk_level == "warning":
            message = f"{resident.get('name', '住户')}已连续 {inactive_days:.1f} 天未出门（阈值 {threshold:.0f} 天），建议安排网格员上门核实"
        elif risk_level == "watch":
            message = f"{resident.get('name', '住户')}已连续 {inactive_days:.1f} 天未出门，超过关注阈值 {threshold:.0f} 天，请留意"
        else:
            message = f"连续未出门 {inactive_days:.1f} 天，低于关注阈值 {threshold:.0f} 天"

        rationale = (
            f"最近一次出门：{resident.get('last_exit_at') or '无记录'}；"
            f"标签：{tag_text}，触发阈值 {threshold:.0f} 天；"
            f"当前连续未出门 {inactive_days:.1f} 天，判定为{RISK_LABELS[risk_level]}。"
        )
        return {
            "presence": "home",
            "presence_label": "在家",
            "inactive_days": inactive_days,
            "threshold_days": threshold,
            "risk_level": risk_level,
            "risk_label": RISK_LABELS[risk_level],
            "should_alert": should_alert,
            "alert_level": alert_level if should_alert else None,
            "level_label": LEVEL_LABELS.get(alert_level) if should_alert else None,
            "message": message,
            "rationale": rationale,
            "confidence": 0.95,
            "planned_absence": planned_absence,
        }

    def run(self, residents: List[Dict[str, Any]], now: datetime) -> AgentDecision:
        alerts = []
        risk_counts = {"normal": 0, "watch": 0, "warning": 0, "critical": 0}
        for resident in residents:
            result = self.evaluate(resident, now)
            risk_counts[result["risk_level"]] += 1
            if result["should_alert"]:
                alerts.append({"resident": resident, **result})
        output = {
            "scanned": len(residents),
            "alert_count": len(alerts),
            "risk_counts": risk_counts,
            "alerts": [
                {
                    "resident_id": a["resident"]["id"],
                    "resident_name": a["resident"].get("name"),
                    "level": a["alert_level"],
                    "inactive_days": a["inactive_days"],
                    "threshold_days": a["threshold_days"],
                    "message": a["message"],
                }
                for a in alerts
            ],
        }
        rationale = (
            f"巡检 {len(residents)} 名住户：正常 {risk_counts['normal']}，关注 {risk_counts['watch']}，"
            f"预警 {risk_counts['warning']}，紧急 {risk_counts['critical']}；生成关怀预警 {len(alerts)} 条。"
        )
        return self.make_decision(output, rationale, confidence=0.95,
                                  input_summary=f"巡检 {len(residents)} 名住户")


class ParkingFeeAgent(BaseAgent):
    """根据停车时长、免费时长、小时费率与单日封顶计算应收停车费。"""

    code = "parking"
    name = "停车收费 Agent"
    action = "parking_charge"

    def calculate(self, duration_minutes: float, is_monthly: bool, rules: Dict[str, Any]) -> Dict[str, Any]:
        duration = max(0.0, float(duration_minutes or 0))
        free = int(rules.get("free_minutes", 30))
        rate = float(rules.get("rate_per_hour", 2.0))
        daily_cap = float(rules.get("daily_cap", 30.0))
        if is_monthly:
            return {
                "duration_minutes": round(duration, 1),
                "billable_hours": 0,
                "free_minutes": free,
                "rate_per_hour": rate,
                "daily_cap": daily_cap,
                "fee": 0.0,
                "is_monthly": True,
                "breakdown": "月卡有效，出场不另收费",
                "rationale": "车辆为有效月卡，按规则免收临时停车费。",
            }
        if duration <= free:
            fee = 0.0
            billable_hours = 0
            breakdown = f"停车 {int(duration)} 分钟，处于 {free} 分钟免费时段，收费 0 元"
        else:
            billable_hours = int(math.ceil((duration - free) / 60.0))
            days = max(1, int(math.ceil(duration / 1440.0)))
            cap = daily_cap * days
            fee = round(min(billable_hours * rate, cap), 2)
            breakdown = (
                f"停车 {int(duration)} 分钟，免费 {free} 分钟，计费 {billable_hours} 小时 × "
                f"{rate:g} 元 = {billable_hours * rate:g} 元"
            )
            if billable_hours * rate > cap:
                breakdown += f"，按 {days} 天封顶 {cap:g} 元收取"
            breakdown += f"，应收 {fee:.2f} 元"
        return {
            "duration_minutes": round(duration, 1),
            "billable_hours": billable_hours,
            "free_minutes": free,
            "rate_per_hour": rate,
            "daily_cap": daily_cap,
            "fee": fee,
            "is_monthly": False,
            "breakdown": breakdown,
            "rationale": (
                f"停车 {int(duration)} 分钟，免费 {free} 分钟，计费 {billable_hours} 小时，"
                f"小时费率 {rate:g} 元，单日封顶 {daily_cap:g} 元，应收 {fee:.2f} 元。"
            ),
        }

    def run(self, plate: str, duration_minutes: float, is_monthly: bool, rules: Dict[str, Any]) -> AgentDecision:
        result = self.calculate(duration_minutes, is_monthly, rules)
        return self.make_decision(
            result,
            result["rationale"],
            confidence=0.99,
            input_summary=f"{plate} / {duration_minutes:.0f} 分钟 / {'月卡' if is_monthly else '临时车'}",
        )
