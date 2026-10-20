"""Agent 3：过程监控 Agent。

巡检待接单/处理中工单：接单超时提醒、处理超时预警、超时升级主管。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import ACCEPT_SLA_MINUTES, WARNING_BEFORE_DEADLINE
from .base import AgentDecision, BaseAgent


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


class MonitoringAgent(BaseAgent):
    code = "monitoring"
    name = "过程监控 Agent"
    action = "monitor_tickets"

    def evaluate(self, ticket: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        status = ticket.get("status")
        if status not in ("assigned", "accepted", "processing", "returned"):
            return alerts
        urgency = ticket.get("urgency") or "中"
        accept_sla = ACCEPT_SLA_MINUTES.get(urgency, 30)
        assigned_at = _parse(ticket.get("assigned_at") or ticket.get("created_at"))
        deadline = _parse(ticket.get("deadline"))
        worker = ticket.get("worker_name") or "维修工"
        accepted_at = _parse(ticket.get("accepted_at"))

        # 1) 接单超时提醒
        if status == "assigned" and not accepted_at and assigned_at:
            accept_due = assigned_at + timedelta(minutes=accept_sla)
            if now > accept_due:
                overdue = _minutes(now - accept_due)
                alerts.append(
                    {
                        "ticket_id": ticket["id"],
                        "level": "L1",
                        "warning_type": "accept_timeout",
                        "message": f"工单已超过接单时限 {overdue} 分钟，已提醒{worker}尽快接单",
                        "escalate": False,
                        "escalated_to": None,
                        "title": "接单超时提醒",
                    }
                )

        # 2) 处理即将超时预警 / 已超时升级
        if deadline:
            if now > deadline:
                overdue = _minutes(now - deadline)
                alerts.append(
                    {
                        "ticket_id": ticket["id"],
                        "level": "L2",
                        "warning_type": "deadline_timeout",
                        "message": f"工单已超时 {overdue} 分钟仍未完工，已升级物业主管介入",
                        "escalate": True,
                        "escalated_to": "物业主管",
                        "title": "处理超时升级",
                    }
                )
            elif now >= deadline - timedelta(minutes=WARNING_BEFORE_DEADLINE) and status in ("accepted", "processing"):
                remaining = max(0, _minutes(deadline - now))
                alerts.append(
                    {
                        "ticket_id": ticket["id"],
                        "level": "L0",
                        "warning_type": "deadline_soon",
                        "message": f"工单距离处理时限仅剩 {remaining} 分钟，请{worker}加快处理",
                        "escalate": False,
                        "escalated_to": None,
                        "title": "即将超时预警",
                    }
                )
        return alerts

    def run(self, tickets: List[Dict[str, Any]], now: datetime) -> AgentDecision:
        alerts: List[Dict[str, Any]] = []
        for ticket in tickets:
            for alert in self.evaluate(ticket, now):
                # 同类型未关闭预警不重复生成
                if not self.repo.has_open_warning(ticket["id"], alert["warning_type"]):
                    alerts.append(alert)
        escalated = [a for a in alerts if a["escalate"]]
        output = {
            "scanned": len(tickets),
            "alerts": alerts,
            "warning_count": len(alerts),
            "escalated_count": len(escalated),
        }
        if alerts:
            rationale = "本轮巡检生成 {} 条预警，其中 {} 条升级主管：{}".format(
                len(alerts),
                len(escalated),
                "；".join(a["message"] for a in alerts[:3]),
            )
        else:
            rationale = f"本轮巡检 {len(tickets)} 条在途工单，未发现接单/处理超时。"
        return self.make_decision(output, rationale, confidence=0.97 if tickets else 0.8)

    def __init__(self, repo):
        self.repo = repo
