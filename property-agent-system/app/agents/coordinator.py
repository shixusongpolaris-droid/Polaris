"""工单协调器：管理 4 个 Agent 的状态流转、决策日志与人工接管。

状态机：
submitted → understood → assigned → accepted/processing → completed
        → closed（验收通过） | returned（验收退回）→ processing
任一步均可人工改派；超时由监控 Agent 生成预警并升级主管。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import OPEN_STATUSES, PRIORS_PATH, status_label
from ..db import now_str
from ..repository import Repository
from .base import AgentDecision
from .dispatch import DispatchAgent, load_priors
from .monitoring import MonitoringAgent
from .understanding import UnderstandingAgent
from .verification import VerificationAgent

EVENT_AGENT = {
    "ticket_created": ("coordinator", "工单编排中心"),
    "understanding_completed": ("understanding", "报修理解 Agent"),
    "ticket_assigned": ("dispatch", "派单决策 Agent"),
    "monitoring_alert": ("monitoring", "过程监控 Agent"),
    "ticket_escalated": ("monitoring", "过程监控 Agent"),
    "ticket_accepted": ("coordinator", "工单编排中心"),
    "processing_started": ("coordinator", "工单编排中心"),
    "work_completed": ("coordinator", "工单编排中心"),
    "verification_passed": ("verification", "闭环验证 Agent"),
    "verification_returned": ("verification", "闭环验证 Agent"),
    "ticket_closed": ("coordinator", "工单编排中心"),
    "ticket_returned": ("coordinator", "工单编排中心"),
    "manual_reassign": ("coordinator", "工单编排中心"),
}


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _minutes_between(start: Any, end: datetime) -> float:
    start_dt = _parse(start)
    if not start_dt:
        return 0.0
    return round(max(0.0, (end - start_dt).total_seconds() / 60.0), 1)


def _fmt(dt: datetime) -> str:
    return dt.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


class WorkOrderCoordinator:
    def __init__(self, repo: Optional[Repository] = None, priors: Optional[Dict[str, Any]] = None):
        self.repo = repo or Repository()
        self.priors = priors if priors is not None else load_priors()
        self.understanding = UnderstandingAgent()
        self.dispatcher = DispatchAgent(self.repo, self.priors)
        self.monitor = MonitoringAgent(self.repo)
        self.verifier = VerificationAgent()

    # ---------------- helpers ----------------
    def _log(self, decision: AgentDecision, ticket_id: str, trace_id: str, now: datetime, input_summary: str = "") -> int:
        return self.repo.add_agent_log(
            agent=decision.agent,
            agent_name=decision.agent_name,
            action=decision.action,
            output=decision.output,
            rationale=decision.rationale,
            confidence=decision.confidence,
            duration_ms=decision.duration_ms,
            ticket_id=ticket_id,
            trace_id=trace_id,
            input_summary=input_summary or decision.input_summary,
            created_at=now,
        )

    def _event(self, ticket_id: str, trace_id: str, now: datetime, event_type: str, title: str, summary: str,
               from_status: Optional[str] = None, to_status: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        actor = EVENT_AGENT.get(event_type, ("coordinator", "工单编排中心"))[0]
        self.repo.add_event(
            ticket_id=ticket_id,
            trace_id=trace_id,
            event_type=event_type,
            actor=actor,
            title=title,
            summary=summary,
            from_status=from_status,
            to_status=to_status,
            detail=detail,
            ts=now,
        )

    def _new_trace_id(self, now: datetime) -> str:
        return f"TRC-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    # ---------------- 主流程 ----------------
    def submit(
        self,
        description: str,
        reporter_name: str = "业主",
        reporter_phone: str = "",
        building: str = "",
        unit: str = "",
        room: str = "",
        now: Optional[datetime] = None,
        is_demo: bool = False,
        source: str = "在线报修",
    ) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        description = (description or "").strip()
        if not description:
            raise ValueError("报修描述不能为空")
        ticket_id = self.repo.next_ticket_id(now)
        trace_id = self._new_trace_id(now)
        title = description[:32] + ("…" if len(description) > 32 else "")
        ticket = self.repo.create_ticket(
            {
                "id": ticket_id,
                "trace_id": trace_id,
                "title": title,
                "description": description,
                "reporter_name": reporter_name or "业主",
                "reporter_phone": reporter_phone,
                "building": building,
                "unit": unit,
                "room": room,
                "location": " ".join(p for p in [building, unit, room] if p),
                "status": "submitted",
                "created_at": _fmt(now),
                "updated_at": _fmt(now),
                "auto_dispatched": 1,
                "is_demo": 1 if is_demo else 0,
                "source": source,
            }
        )
        self._event(ticket_id, trace_id, now, "ticket_created", "工单创建",
                    f"业主提交报修：{description[:60]}", to_status="submitted",
                    detail={"reporter": reporter_name, "channel": source, "description": description})

        # Agent 1 理解
        t0 = time.perf_counter()
        decision = self.understanding.run(description, {"building": building, "unit": unit, "room": room})
        decision.duration_ms = (time.perf_counter() - t0) * 1000
        log_id = self._log(decision, ticket_id, trace_id, now, input_summary=description[:120])
        u = decision.output
        self.repo.update_ticket(
            ticket_id,
            repair_type=u["repair_type"],
            facility_type=u["facility_type"],
            urgency=u["urgency"],
            urgency_score=u["urgency_score"],
            sla_minutes=u["sla_minutes"],
            building=u["location"]["building"] or building,
            unit=u["location"]["unit"] or unit,
            room=u["location"]["room"] or room,
            location=u["location"]["text"],
            status="understood",
            understood_at=_fmt(now),
            updated_at=_fmt(now),
        )
        self._event(ticket_id, trace_id, now, "understanding_completed", "报修理解完成",
                    f"{u['repair_type']} · {u['location']['text']} · 紧急程度{u['urgency']}",
                    from_status="submitted", to_status="understood",
                    detail={"output": u, "confidence": decision.confidence, "log_id": log_id, "rationale": decision.rationale})

        # Agent 2 派单
        ticket = self.repo.get_ticket(ticket_id) or ticket
        t0 = time.perf_counter()
        dispatch_decision = self.dispatcher.run(ticket, u, now=now)
        dispatch_decision.duration_ms = (time.perf_counter() - t0) * 1000
        dispatch_log_id = self._log(dispatch_decision, ticket_id, trace_id, now,
                                    input_summary=f"{u['repair_type']} / {u['urgency']} / {u['location']['text']}")
        d = dispatch_decision.output
        self.repo.update_ticket(
            ticket_id,
            worker_id=d["worker_id"],
            worker_name=d["worker_name"],
            trade=d["trade"],
            priority=d["priority"],
            sla_minutes=d["sla_minutes"],
            deadline=d["deadline"],
            status="assigned",
            assigned_at=_fmt(now),
            auto_dispatched=0 if d.get("fallback") else 1,
            updated_at=_fmt(now),
        )
        self._event(ticket_id, trace_id, now, "ticket_assigned", "自动派单完成",
                    f"派给 {d['worker_name']}（{d['trade']}），优先级 {d['priority']}，要求 {d['sla_minutes']} 分钟内完成",
                    from_status="understood", to_status="assigned",
                    detail={"output": d, "rationale": dispatch_decision.rationale,
                            "confidence": dispatch_decision.confidence, "log_id": dispatch_log_id})
        return self.repo.get_trace(ticket_id)

    def accept(self, ticket_id: str, worker_id: Optional[str] = None, now: Optional[datetime] = None,
               actor: str = "worker") -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        ticket = self.repo.get_ticket(ticket_id)
        if not ticket:
            raise KeyError("工单不存在")
        if ticket["status"] != "assigned":
            raise ValueError(f"当前状态「{status_label(ticket['status'])}」不能接单")
        worker = self.repo.get_worker(worker_id) if worker_id else self.repo.get_worker(ticket["worker_id"])
        if not worker:
            raise ValueError("维修工不存在")
        response = _minutes_between(ticket.get("assigned_at"), now)
        accept_sla = {"紧急": 10, "高": 15, "中": 30, "低": 60}.get(ticket.get("urgency"), 30)
        overdue_note = f"，接单超时 {max(0, int(response - accept_sla))} 分钟" if response > accept_sla else ""
        self.repo.update_ticket(
            ticket_id,
            status="processing",
            worker_id=worker["id"],
            worker_name=worker["name"],
            trade=worker["trade"],
            accepted_at=_fmt(now),
            response_minutes=response,
            updated_at=_fmt(now),
        )
        self._event(ticket_id, ticket["trace_id"], now, "ticket_accepted", "维修工接单",
                    f"{worker['name']} 已接单，响应 {response:g} 分钟{overdue_note}",
                    from_status="assigned", to_status="accepted",
                    detail={"worker_id": worker["id"], "worker_name": worker["name"], "response_minutes": response})
        self._event(ticket_id, ticket["trace_id"], now, "processing_started", "开始处理",
                    f"{worker['name']} 已到达现场，工单进入处理中",
                    from_status="accepted", to_status="processing")
        return self.repo.get_trace(ticket_id)

    def complete(
        self,
        ticket_id: str,
        result_note: str,
        photos_count: int = 0,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        ticket = self.repo.get_ticket(ticket_id)
        if not ticket:
            raise KeyError("工单不存在")
        if ticket["status"] not in ("accepted", "processing", "returned"):
            raise ValueError(f"当前状态「{status_label(ticket['status'])}」不能提交完工")
        old_status = ticket["status"]
        duration = _minutes_between(ticket.get("assigned_at") or ticket.get("created_at"), now)
        deadline = _parse(ticket.get("deadline"))
        is_timeout = 1 if (deadline and now > deadline) else 0
        self.repo.update_ticket(
            ticket_id,
            status="completed",
            completed_at=_fmt(now),
            duration_minutes=duration,
            is_timeout=is_timeout,
            result_note=result_note,
            photos_count=int(photos_count or 0),
            updated_at=_fmt(now),
        )
        self._event(ticket_id, ticket["trace_id"], now, "work_completed", "维修完工提交",
                    f"{ticket.get('worker_name') or '维修工'} 提交完工，上传照片 {int(photos_count or 0)} 张，处理时长 {duration:g} 分钟",
                    from_status=old_status, to_status="completed",
                    detail={"result_note": result_note, "photos_count": int(photos_count or 0),
                            "duration_minutes": duration, "is_timeout": bool(is_timeout)})
        ticket = self.repo.get_ticket(ticket_id) or ticket

        # Agent 4 自动验收
        t0 = time.perf_counter()
        decision = self.verifier.evaluate(ticket, result_note, int(photos_count or 0), now=now)
        decision.duration_ms = (time.perf_counter() - t0) * 1000
        log_id = self._log(decision, ticket_id, ticket["trace_id"], now,
                           input_summary=f"{ticket['id']} 完工验收 / 照片{photos_count}张")
        v = decision.output
        if v["verdict"] == "pass":
            self.repo.update_ticket(
                ticket_id,
                status="closed",
                closed_at=_fmt(now),
                verification_verdict="pass",
                verification_note=v["note"],
                is_timeout=is_timeout,
                updated_at=_fmt(now),
            )
            self._event(ticket_id, ticket["trace_id"], now, "verification_passed", "闭环验收通过",
                        v["note"], from_status="completed", to_status="closed",
                        detail={"output": v, "confidence": decision.confidence, "log_id": log_id,
                                "rationale": decision.rationale})
            self._event(ticket_id, ticket["trace_id"], now, "ticket_closed", "工单闭环",
                        f"工单 {ticket_id} 已完成验收并关闭，闭环率与超时率看板同步刷新",
                        from_status="closed", to_status="closed",
                        detail={"closed_at": _fmt(now), "is_timeout": bool(is_timeout)})
        else:
            self.repo.update_ticket(
                ticket_id,
                status="returned",
                verification_verdict="return",
                verification_note=v["note"],
                updated_at=_fmt(now),
            )
            self._event(ticket_id, ticket["trace_id"], now, "verification_returned", "验收退回整改",
                        v["note"], from_status="completed", to_status="returned",
                        detail={"output": v, "confidence": decision.confidence, "log_id": log_id,
                                "rationale": decision.rationale})
            self._event(ticket_id, ticket["trace_id"], now, "ticket_returned", "工单退回",
                        "已通知维修工补充材料并重新提交完工", from_status="returned", to_status="returned",
                        detail={"required_actions": v.get("required_actions", [])})
        return {"ticket": self.repo.get_ticket(ticket_id), "verification": v,
                "trace": self.repo.get_trace(ticket_id)}

    # ---------------- 监控 ----------------
    def scan(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        tickets, _ = self.repo.list_tickets(status="assigned,accepted,processing,returned", limit=200)
        t0 = time.perf_counter()
        decision = self.monitor.run(tickets, now)
        decision.duration_ms = (time.perf_counter() - t0) * 1000
        ticket_for_log = tickets[0]["id"] if tickets else None
        trace_for_log = tickets[0].get("trace_id") if tickets else None
        log_id = self._log(decision, ticket_for_log, trace_for_log, now,
                           input_summary=f"巡检 {len(tickets)} 条在途工单")
        created = 0
        for alert in decision.output.get("alerts", []):
            self.repo.add_warning(
                ticket_id=alert["ticket_id"],
                level=alert["level"],
                warning_type=alert["warning_type"],
                message=alert["message"],
                trace_id=(self.repo.get_ticket(alert["ticket_id"]) or {}).get("trace_id"),
                escalated_to=alert.get("escalated_to"),
                ts=now,
            )
            created += 1
            self._event(alert["ticket_id"], (self.repo.get_ticket(alert["ticket_id"]) or {}).get("trace_id", ""),
                        now, "monitoring_alert", alert["title"], alert["message"],
                        from_status="", to_status="",
                        detail={"level": alert["level"], "warning_type": alert["warning_type"],
                                "escalate": alert["escalate"], "log_id": log_id})
            if alert.get("escalate"):
                self.repo.update_ticket(alert["ticket_id"], escalated=1, updated_at=_fmt(now))
                self._event(alert["ticket_id"], (self.repo.get_ticket(alert["ticket_id"]) or {}).get("trace_id", ""),
                            now, "ticket_escalated", "升级物业主管", alert["message"],
                            detail={"escalated_to": alert.get("escalated_to"), "reason": alert["warning_type"]})
        result = {
            "scanned": len(tickets),
            "warnings_created": created,
            "escalated": decision.output.get("escalated_count", 0),
            "items": decision.output.get("alerts", []),
            "agent_log_id": log_id,
            "scanned_at": _fmt(now),
        }
        return result

    # ---------------- 人工接管 ----------------
    def reassign(self, ticket_id: str, worker_id: str, reason: str = "人工改派",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        ticket = self.repo.get_ticket(ticket_id)
        if not ticket:
            raise KeyError("工单不存在")
        if ticket["status"] not in OPEN_STATUSES:
            raise ValueError("已闭环工单不能改派")
        worker = self.repo.get_worker(worker_id)
        if not worker:
            raise ValueError("维修工不存在")
        old_worker = ticket.get("worker_name")
        sla = int(ticket.get("sla_minutes") or 120)
        deadline = _fmt(now + timedelta(minutes=sla))
        self.repo.update_ticket(
            ticket_id,
            worker_id=worker["id"],
            worker_name=worker["name"],
            trade=worker["trade"],
            status="assigned",
            accepted_at=None,
            completed_at=None,
            assigned_at=_fmt(now),
            deadline=deadline,
            auto_dispatched=0,
            reassigned=1,
            updated_at=_fmt(now),
        )
        self._event(ticket_id, ticket["trace_id"], now, "manual_reassign", "人工接管改派",
                    f"由 {old_worker or '未分配'} 改派给 {worker['name']}（{worker['trade']}），原因：{reason}",
                    from_status=ticket["status"], to_status="assigned",
                    detail={"old_worker": old_worker, "new_worker": worker["name"], "reason": reason,
                            "new_deadline": deadline})
        self.repo.add_agent_log(
            agent="coordinator", agent_name="工单编排中心", action="human_takeover",
            output={"ticket_id": ticket_id, "old_worker": old_worker, "new_worker": worker["name"],
                    "new_deadline": deadline, "reason": reason},
            rationale=f"人工接管：{reason}；改派后重置接单与处理时限。",
            confidence=1.0, ticket_id=ticket_id, trace_id=ticket["trace_id"], created_at=now,
            input_summary=f"{ticket_id} 改派给 {worker['name']}",
        )
        return self.repo.get_trace(ticket_id)

    # ---------------- 演示 ----------------
    def _build_steps(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.repo.get_ticket(ticket_id) or {}
        base = _parse(ticket.get("created_at")) or datetime.now()
        events = self.repo.list_events(ticket_id)
        steps = []
        current_status = "submitted"
        for index, event in enumerate(events, start=1):
            ts = _parse(event.get("ts")) or base
            total_min = int((ts - base).total_seconds() // 60)
            agent_code, agent_name = EVENT_AGENT.get(event["event_type"], ("coordinator", "工单编排中心"))
            if event.get("to_status"):
                current_status = event["to_status"]
            status = current_status
            steps.append(
                {
                    "seq": index,
                    "time": f"T+{total_min // 60:02d}:{total_min % 60:02d}",
                    "timestamp": event.get("ts"),
                    "agent": agent_code,
                    "agent_name": agent_name,
                    "event_type": event["event_type"],
                    "title": event["title"],
                    "summary": event.get("summary") or "",
                    "status": status,
                    "status_label": status_label(status),
                    "payload": event.get("detail") or {},
                }
            )
        return steps

    def run_demo(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        self.repo.delete_demo_tickets()
        metrics_before = self.repo.stats_live()
        base = now - timedelta(minutes=60)
        trace = self.submit(
            description="5栋1单元水管爆裂，楼道积水，水已经流到电梯口了，麻烦快点来处理！",
            reporter_name="王女士",
            reporter_phone="138****6688",
            building="5栋",
            unit="1单元",
            now=base,
            is_demo=True,
            source="业主App",
        )
        ticket_id = trace["ticket"]["id"]
        # T+25 接单超时提醒
        self.scan(base + timedelta(minutes=25))
        # T+26 维修工接单
        self.accept(ticket_id, now=base + timedelta(minutes=26))
        # T+35 处理超时，升级主管
        self.scan(base + timedelta(minutes=35))
        # T+38 第一次完工：缺少照片，验收退回
        self.complete(
            ticket_id,
            result_note="已更换爆裂阀门，关闭总阀，现场已处理。",
            photos_count=0,
            now=base + timedelta(minutes=38),
        )
        # T+45 补充照片后重新提交，验收通过并闭环
        self.complete(
            ticket_id,
            result_note="已更换爆裂阀门并清理楼道积水，完成压力测试，现场照片2张已上传，电梯口积水清理完毕。",
            photos_count=2,
            now=base + timedelta(minutes=45),
        )
        ticket = self.repo.get_ticket(ticket_id)
        steps = self._build_steps(ticket_id)
        return {
            "ticket": ticket,
            "steps": steps,
            "warnings": self.repo.list_warnings(ticket_id=ticket_id),
            "metrics_before": metrics_before,
            "metrics_after": self.repo.stats_live(),
            "case": {
                "title": "5栋1单元水管爆裂",
                "description": ticket.get("description") if ticket else "",
                "repair_type": ticket.get("repair_type") if ticket else "水管爆裂",
                "urgency": ticket.get("urgency") if ticket else "紧急",
                "sla_minutes": ticket.get("sla_minutes") if ticket else 30,
                "worker": ticket.get("worker_name") if ticket else "王建国",
                "storyline": "T+25 接单超时提醒 → T+35 超时升级主管 → T+38 缺照片退回 → T+45 补充材料验收通过",
            },
        }

    def latest_demo(self) -> Optional[Dict[str, Any]]:
        tickets, _ = self.repo.list_tickets(is_demo=True, limit=1)
        if not tickets:
            return None
        return self.repo.get_trace(tickets[0]["id"])
