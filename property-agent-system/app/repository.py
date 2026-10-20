"""数据访问层：工单、事件、Agent 日志、预警、维修工。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import db
from .config import (
    ACCEPT_SLA_MINUTES,
    OPEN_STATUSES,
    STATUS_COLORS,
    STATUS_LABELS,
    type_meta,
)

TICKET_FIELDS = {
    "id", "trace_id", "title", "description", "reporter_name", "reporter_phone",
    "repair_type", "facility_type", "building", "unit", "room", "location",
    "urgency", "urgency_score", "status", "worker_id", "worker_name", "trade",
    "priority", "sla_minutes", "deadline", "auto_dispatched", "reassigned",
    "escalated", "is_demo", "created_at", "understood_at", "assigned_at",
    "accepted_at", "completed_at", "closed_at", "response_minutes",
    "duration_minutes", "is_timeout", "photos_count", "result_note",
    "verification_verdict", "verification_note", "satisfaction", "updated_at",
}


def _decode_ticket(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    data["status_label"] = STATUS_LABELS.get(data.get("status"), data.get("status"))
    data["status_color"] = STATUS_COLORS.get(data.get("status"), "#8c8c8c")
    data["urgency_color"] = {"紧急": "#ff4d4f", "高": "#fa8c16", "中": "#fadb14", "低": "#52c41a"}.get(
        data.get("urgency"), "#8c8c8c"
    )
    for key in ("auto_dispatched", "reassigned", "escalated", "is_demo", "is_timeout"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


def _decode_json(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


class Repository:
    def __init__(self, db_path: Optional[str] = None, load_history: bool = True):
        self.db_path = db_path
        db.initialize(db_path, seed_history=load_history)

    def ensure_history(self, force: bool = False) -> int:
        """当历史数据 CSV 后于数据库生成时，补充导入。"""
        if self.history_count() and not force:
            return 0
        with db.connection(self.db_path) as conn:
            return db.import_history(conn, force=force)

    # ---------------- workers ----------------
    def list_workers(self, active_only: bool = True) -> List[Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            sql = "SELECT * FROM workers"
            if active_only:
                sql += " WHERE active=1"
            sql += " ORDER BY id"
            rows = conn.execute(sql).fetchall()
            loads = self.worker_loads()
        result = []
        for row in rows:
            w = dict(row)
            w["skills"] = _decode_json(w.get("skills")) or []
            w["active_tickets"] = loads.get(w["id"], 0)
            w["load_ratio"] = round(w["active_tickets"] / max(w.get("max_daily") or 1, 1), 2)
            result.append(w)
        return result

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        if not row:
            return None
        worker = dict(row)
        worker["skills"] = _decode_json(worker.get("skills")) or []
        return worker

    def worker_loads(self) -> Dict[str, int]:
        with db.connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT worker_id, COUNT(*) AS c FROM tickets WHERE worker_id IS NOT NULL "
                "AND status IN ('assigned','accepted','processing','returned') GROUP BY worker_id"
            ).fetchall()
        return {r["worker_id"]: r["c"] for r in rows}

    # ---------------- tickets ----------------
    def next_ticket_id(self, now: Optional[datetime] = None) -> str:
        now = now or datetime.now()
        prefix = f"WO{now.strftime('%Y%m%d')}"
        with db.connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM tickets WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",)
            ).fetchone()
        seq = 1
        if row:
            try:
                seq = int(row["id"].replace(prefix, "")) + 1
            except ValueError:
                seq = 1
        return f"{prefix}{seq:04d}"

    def create_ticket(self, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = {k: v for k, v in data.items() if k in TICKET_FIELDS}
        payload.setdefault("created_at", db.now_str())
        payload.setdefault("updated_at", payload["created_at"])
        payload.setdefault("status", "submitted")
        payload.setdefault("trace_id", payload.get("trace_id") or f"TRC-{payload.get('id', '')}")
        columns = ",".join(payload.keys())
        placeholders = ",".join("?" for _ in payload)
        with db.connection(self.db_path) as conn:
            conn.execute(f"INSERT INTO tickets ({columns}) VALUES ({placeholders})", list(payload.values()))
        return self.get_ticket(payload.get("id"))  # type: ignore[return-value]

    def update_ticket(self, ticket_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {k: v for k, v in fields.items() if k in TICKET_FIELDS}
        if not allowed:
            return self.get_ticket(ticket_id)
        allowed["updated_at"] = fields.get("updated_at") or db.now_str()
        set_sql = ",".join(f"{k}=?" for k in allowed)
        with db.connection(self.db_path) as conn:
            conn.execute(f"UPDATE tickets SET {set_sql} WHERE id=?", [*allowed.values(), ticket_id])
        return self.get_ticket(ticket_id)

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return _decode_ticket(row)

    def list_tickets(
        self,
        status: Optional[str] = None,
        urgency: Optional[str] = None,
        q: Optional[str] = None,
        worker_id: Optional[str] = None,
        is_demo: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where, params = [], []
        if status:
            if status == "open":
                where.append(f"status IN ({','.join('?' for _ in OPEN_STATUSES)})")
                params.extend(sorted(OPEN_STATUSES))
            elif "," in status:
                values = [s.strip() for s in status.split(",") if s.strip()]
                where.append(f"status IN ({','.join('?' for _ in values)})")
                params.extend(values)
            else:
                where.append("status=?")
                params.append(status)
        if urgency:
            where.append("urgency=?")
            params.append(urgency)
        if worker_id:
            where.append("worker_id=?")
            params.append(worker_id)
        if is_demo is not None:
            where.append("is_demo=?")
            params.append(1 if is_demo else 0)
        if q:
            like = f"%{q.strip()}%"
            where.append("(id LIKE ? OR description LIKE ? OR location LIKE ? OR worker_name LIKE ? OR repair_type LIKE ?)")
            params.extend([like] * 5)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with db.connection(self.db_path) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM tickets{clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM tickets{clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, max(1, min(limit, 200)), max(0, offset)],
            ).fetchall()
        return [_decode_ticket(r) for r in rows], total  # type: ignore[misc]

    def stats_live(self) -> Dict[str, Any]:
        with db.connection(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            rows = conn.execute("SELECT status, COUNT(*) c FROM tickets GROUP BY status").fetchall()
            timeout = conn.execute("SELECT COUNT(*) FROM tickets WHERE is_timeout=1").fetchone()[0]
            escalated = conn.execute("SELECT COUNT(*) FROM tickets WHERE escalated=1").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE date(created_at)=date('now','localtime')"
            ).fetchone()[0]
        by_status = {r["status"]: r["c"] for r in rows}
        closed = by_status.get("closed", 0)
        return {
            "total": total,
            "today": today,
            "open": sum(by_status.get(s, 0) for s in OPEN_STATUSES),
            "closed": closed,
            "returned": by_status.get("returned", 0),
            "timeout": timeout,
            "escalated": escalated,
            "closed_rate": round(closed / total * 100, 1) if total else 0.0,
            "by_status": by_status,
        }

    def delete_demo_tickets(self) -> int:
        with db.connection(self.db_path) as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE is_demo=1").fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM ticket_events WHERE ticket_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM agent_logs WHERE ticket_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM warnings WHERE ticket_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM tickets WHERE id IN ({placeholders})", ids)
        return len(ids)

    # ---------------- events ----------------
    def add_event(
        self,
        ticket_id: str,
        event_type: str,
        actor: str,
        title: str,
        summary: str = "",
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> int:
        with db.connection(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO ticket_events
                (ticket_id, trace_id, ts, event_type, actor, from_status, to_status, title, summary, detail_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticket_id,
                    trace_id,
                    db.now_str(ts),
                    event_type,
                    actor,
                    from_status,
                    to_status,
                    title,
                    summary,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                ),
            )
            return int(cur.lastrowid)

    def list_events(self, ticket_id: str) -> List[Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id", (ticket_id,)
            ).fetchall()
        events = []
        for r in rows:
            item = dict(r)
            item["detail"] = _decode_json(item.pop("detail_json", None))
            events.append(item)
        return events

    # ---------------- agent logs ----------------
    def add_agent_log(
        self,
        agent: str,
        action: str,
        output: Dict[str, Any],
        rationale: str,
        confidence: float = 0.0,
        duration_ms: float = 0.0,
        ticket_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        input_summary: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        with db.connection(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO agent_logs
                (trace_id, ticket_id, agent, agent_name, action, input_summary, output_json,
                 rationale, confidence, duration_ms, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace_id,
                    ticket_id,
                    agent,
                    agent_name or agent,
                    action,
                    input_summary,
                    json.dumps(output, ensure_ascii=False),
                    rationale,
                    round(float(confidence), 3),
                    round(float(duration_ms), 2),
                    db.now_str(created_at),
                ),
            )
            return int(cur.lastrowid)

    def list_agent_logs(
        self,
        ticket_id: Optional[str] = None,
        agent: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where, params = [], []
        if ticket_id:
            where.append("ticket_id=?")
            params.append(ticket_id)
        if agent:
            where.append("agent=?")
            params.append(agent)
        if trace_id:
            where.append("trace_id=?")
            params.append(trace_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with db.connection(self.db_path) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM agent_logs{clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM agent_logs{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, max(1, min(limit, 500)), max(0, offset)],
            ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item["output"] = _decode_json(item.pop("output_json", None))
            items.append(item)
        return items, total

    # ---------------- warnings ----------------
    def add_warning(
        self,
        ticket_id: str,
        level: str,
        warning_type: str,
        message: str,
        trace_id: Optional[str] = None,
        escalated_to: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> int:
        with db.connection(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO warnings
                (ticket_id, trace_id, level, warning_type, message, status, created_at, escalated_to)
                VALUES (?,?,?,?,?,?,?,?)""",
                (ticket_id, trace_id, level, warning_type, message, "open", db.now_str(ts), escalated_to),
            )
            return int(cur.lastrowid)

    def has_open_warning(self, ticket_id: str, warning_type: str) -> bool:
        with db.connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM warnings WHERE ticket_id=? AND warning_type=? AND status='open' LIMIT 1",
                (ticket_id, warning_type),
            ).fetchone()
        return bool(row)

    def list_warnings(
        self, ticket_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        where, params = [], []
        if ticket_id:
            where.append("ticket_id=?")
            params.append(ticket_id)
        if status:
            where.append("status=?")
            params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with db.connection(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM warnings{clause} ORDER BY id DESC LIMIT ?", [*params, max(1, min(limit, 500))]
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- dashboard helpers ----------------
    def recent_tickets(self, limit: int = 8) -> List[Dict[str, Any]]:
        return self.list_tickets(limit=limit)[0]

    def agent_activity(self, limit: int = 20) -> List[Dict[str, Any]]:
        logs, _ = self.list_agent_logs(limit=limit)
        return logs

    def get_trace(self, ticket_id: str) -> Dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return {}
        logs, _ = self.list_agent_logs(ticket_id=ticket_id, limit=200)
        logs.reverse()
        return {
            "ticket": ticket,
            "events": self.list_events(ticket_id),
            "logs": logs,
            "warnings": self.list_warnings(ticket_id=ticket_id),
        }

    def get_trace_by_trace_id(self, trace_id: str) -> List[Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM tickets WHERE trace_id=?", (trace_id,)).fetchall()
        return [_decode_ticket(r) for r in rows]  # type: ignore[misc]

    # ---------------- history ----------------
    def history_count(self) -> int:
        with db.connection(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM historical_orders").fetchone()[0])

    def history_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM historical_orders ORDER BY created_at"
        params: Iterable[Any] = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with db.connection(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def worker_performance(self) -> Dict[str, Dict[str, Any]]:
        with db.connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT worker_id, COUNT(*) AS orders, AVG(duration_minutes) AS avg_duration,
                   AVG(satisfaction) AS satisfaction, AVG(is_timeout) AS timeout_rate
                   FROM historical_orders GROUP BY worker_id"""
            ).fetchall()
        return {
            r["worker_id"]: {
                "orders": r["orders"],
                "avg_duration": round(r["avg_duration"] or 0, 1),
                "satisfaction": round(r["satisfaction"] or 4.5, 2),
                "timeout_rate": round((r["timeout_rate"] or 0) * 100, 1),
            }
            for r in rows
        }


def accept_deadline(ticket: Dict[str, Any]) -> datetime:
    """根据工单派单时间与紧急程度计算接单截止时间。"""
    base = ticket.get("assigned_at") or ticket.get("created_at")
    dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S") if isinstance(base, str) else (base or datetime.now())
    minutes = ACCEPT_SLA_MINUTES.get(ticket.get("urgency"), 30)
    return dt + timedelta(minutes=minutes)


def sla_for_type(repair_type: str) -> int:
    return int(type_meta(repair_type)[2])
