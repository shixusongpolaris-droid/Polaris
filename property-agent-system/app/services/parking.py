"""停车管理服务：车辆进出、停车计费、缴费与经营看板。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import db
from ..agents.community import ParkingFeeAgent, parse_dt
from ..config import PARKING_RULES
from ..repository import Repository

STATUS_LABELS = {"parked": "停放中", "unpaid": "待缴费", "paid": "已支付"}
PAYMENT_LABELS = {"wechat": "微信", "alipay": "支付宝", "cash": "现金", "monthly": "月卡", "free": "免费放行"}
PAYMENT_CODES = {"微信": "wechat", "微信支付": "wechat", "支付宝": "alipay", "现金": "cash", "月卡": "monthly", "免费放行": "free"}


def normalize_payment_method(method: Optional[str]) -> str:
    if not method:
        return ""
    method = str(method).strip()
    return PAYMENT_CODES.get(method, method.lower())


class ParkingService:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.agent = ParkingFeeAgent()

    # ---------------- 工具 ----------------
    def _rows(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with db.connection(self.repo.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with db.connection(self.repo.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def lot(self) -> Dict[str, Any]:
        lot = self._row("SELECT * FROM parking_lots WHERE id='LOT-B1'")
        if not lot:
            lot = {"id": "LOT-B1", "name": "地下一层停车场", "total_spaces": 120,
                   "free_minutes": 30, "rate_per_hour": 2.0, "daily_cap": 30.0, "monthly_fee": 300.0}
        return lot

    def rules(self) -> Dict[str, Any]:
        lot = self.lot()
        return {
            "free_minutes": int(lot.get("free_minutes") or PARKING_RULES["free_minutes"]),
            "rate_per_hour": float(lot.get("rate_per_hour") or PARKING_RULES["rate_per_hour"]),
            "daily_cap": float(lot.get("daily_cap") or PARKING_RULES["daily_cap"]),
            "monthly_fee": float(lot.get("monthly_fee") or PARKING_RULES["monthly_fee"]),
            "description": (
                f"{int(lot.get('free_minutes') or 30)} 分钟内免费；超出后 "
                f"{float(lot.get('rate_per_hour') or 2):g} 元/小时，单日封顶 "
                f"{float(lot.get('daily_cap') or 30):g} 元；月卡 "
                f"{float(lot.get('monthly_fee') or 300):g} 元/月"
            ),
        }

    def _vehicle(self, plate: str) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM parking_vehicles WHERE plate=?", (plate,))

    def _is_monthly(self, vehicle: Optional[Dict[str, Any]], now: datetime) -> bool:
        if not vehicle or vehicle.get("pass_type") != "monthly":
            return False
        expire = parse_dt(vehicle.get("pass_expire_at"))
        return expire is None or expire >= now

    def _format_session(self, row: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        entry = parse_dt(row.get("entry_at"))
        exit_at = parse_dt(row.get("exit_at"))
        duration = row.get("duration_minutes")
        if duration is None and entry:
            end = exit_at or now
            duration = round(max(0.0, (end - entry).total_seconds() / 60), 1)
        duration = float(duration or 0)
        fee = row.get("fee")
        estimated = None
        if row.get("status") == "parked":
            estimated = self.agent.calculate(duration, bool(row.get("is_monthly")), self.rules())["fee"]
            fee = estimated
        owner = row.get("owner_name") or "临时车主"
        location = row.get("location") or ""
        return {
            **row,
            "duration_minutes": round(duration, 1),
            "fee": round(float(fee or 0), 2),
            "estimated_fee": round(float(estimated or 0), 2),
            "status_label": STATUS_LABELS.get(row.get("status"), row.get("status")),
            "payment_label": PAYMENT_LABELS.get(row.get("payment_method"), row.get("payment_method") or "—"),
            "owner_name": owner,
            "location": location or "临时车辆",
            "is_monthly": bool(row.get("is_monthly")),
            "is_overstay": duration > 1440,
        }

    def _session_by_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM parking_sessions WHERE id=?", (session_id,))

    def _session_prefix(self, now: datetime) -> str:
        return f"PS{now.strftime('%Y%m%d')}"

    def _next_session_id(self, now: datetime) -> str:
        prefix = self._session_prefix(now)
        row = self._row("SELECT id FROM parking_sessions WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",))
        seq = 1
        if row:
            try:
                seq = int(row["id"].replace(prefix, "")) + 1
            except ValueError:
                seq = 1
        return f"{prefix}{seq:04d}"

    def _next_space(self, total: int) -> str:
        occupied = {r["space_no"] for r in self._rows("SELECT space_no FROM parking_sessions WHERE status='parked'")}
        for i in range(1, total + 1):
            code = f"B1-{i:03d}"
            if code not in occupied:
                return code
        return f"B1-{total:03d}"

    def _log(self, action: str, output: Dict[str, Any], rationale: str,
             now: datetime, input_summary: str) -> None:
        self.repo.add_agent_log(
            agent="parking", agent_name="停车收费 Agent", action=action,
            output=output, rationale=rationale, confidence=0.99,
            created_at=now, input_summary=input_summary,
        )

    # ---------------- 看板 ----------------
    def dashboard(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        lot = self.lot()
        total = int(lot["total_spaces"])
        occupied = int(self._row("SELECT COUNT(*) AS c FROM parking_sessions WHERE status='parked'")["c"])
        occupied = min(total, occupied)
        today_entries = int(self._row(
            "SELECT COUNT(*) AS c FROM parking_sessions WHERE date(entry_at)=?", (today,))["c"])
        today_exits = int(self._row(
            "SELECT COUNT(*) AS c FROM parking_sessions WHERE date(exit_at)=?", (today,))["c"])
        today_paid = int(self._row(
            "SELECT COUNT(*) AS c FROM parking_sessions WHERE status='paid' AND date(paid_at)=?", (today,))["c"])
        today_revenue = float(self._row(
            "SELECT COALESCE(SUM(fee),0) AS s FROM parking_sessions WHERE status='paid' AND date(paid_at)=?", (today,))["s"])
        month_revenue = float(self._row(
            "SELECT COALESCE(SUM(fee),0) AS s FROM parking_sessions WHERE status='paid' AND substr(paid_at,1,7)=?", (month,))["s"])
        unpaid = self._row(
            "SELECT COUNT(*) AS c, COALESCE(SUM(fee),0) AS s FROM parking_sessions WHERE status='unpaid'") or {"c": 0, "s": 0}
        avg_duration = self._row(
            "SELECT AVG(duration_minutes) AS a FROM parking_sessions WHERE duration_minutes IS NOT NULL AND entry_at>=?",
            ((now - timedelta(days=30)).strftime("%Y-%m-%d"),))["a"]
        monthly_pass = int(self._row(
            "SELECT COUNT(*) AS c FROM parking_vehicles WHERE pass_type='monthly' AND (pass_expire_at IS NULL OR pass_expire_at>=?)",
            (today,))["c"])
        temporary_pass = int(self._row(
            "SELECT COUNT(*) AS c FROM parking_vehicles WHERE pass_type!='monthly' OR pass_expire_at<?",
            (today,))["c"])
        stats = {
            "today_revenue": round(today_revenue, 2),
            "month_revenue": round(month_revenue, 2),
            "today_entries": today_entries,
            "today_exits": today_exits,
            "today_paid_sessions": today_paid,
            "avg_duration_minutes": round(float(avg_duration or 0), 1),
            "monthly_pass": monthly_pass,
            "temporary_pass": temporary_pass,
            "unpaid_count": int(unpaid["c"]),
            "unpaid_amount": round(float(unpaid["s"]), 2),
        }
        parked_rows = self._rows(
            "SELECT * FROM parking_sessions WHERE status='parked' ORDER BY entry_at DESC LIMIT 40")
        recent_rows = self._rows(
            "SELECT * FROM parking_sessions ORDER BY entry_at DESC LIMIT 15")
        return {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "lot": {
                "name": lot["name"], "total_spaces": total, "occupied": occupied,
                "available": total - occupied,
                "occupancy_rate": round(occupied / total * 100, 1) if total else 0,
            },
            "stats": stats,
            "revenue_trend": self._revenue_trend(now),
            "hourly_entries": self._hourly_entries(today),
            "payment_mix": self._payment_mix(now),
            "parked": [self._format_session(r, now) for r in parked_rows],
            "recent_sessions": [self._format_session(r, now) for r in recent_rows],
            "rules": self.rules(),
        }

    def _revenue_trend(self, now: datetime) -> List[Dict[str, Any]]:
        start = (now - timedelta(days=13)).strftime("%Y-%m-%d")
        rows = self._rows(
            """SELECT date(paid_at) AS d, COUNT(*) AS sessions, COALESCE(SUM(fee),0) AS revenue
               FROM parking_sessions WHERE status='paid' AND date(paid_at)>=? GROUP BY date(paid_at)""",
            (start,),
        )
        by_day = {r["d"]: r for r in rows}
        result = []
        for i in range(14):
            day = (now - timedelta(days=13 - i)).strftime("%Y-%m-%d")
            row = by_day.get(day, {"sessions": 0, "revenue": 0})
            result.append({"date": day, "sessions": int(row["sessions"]), "revenue": round(float(row["revenue"]), 2)})
        return result

    def _hourly_entries(self, today: str) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT CAST(substr(entry_at,12,2) AS INTEGER) AS h, COUNT(*) AS c FROM parking_sessions WHERE date(entry_at)=? GROUP BY h",
            (today,),
        )
        by_hour = {int(r["h"]): int(r["c"]) for r in rows}
        return [{"hour": h, "entries": by_hour.get(h, 0)} for h in range(24)]

    def _payment_mix(self, now: datetime) -> List[Dict[str, Any]]:
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = self._rows(
            """SELECT COALESCE(payment_method,'unknown') AS method, COUNT(*) AS c
               FROM parking_sessions WHERE status='paid' AND date(paid_at)>=? GROUP BY method""",
            (start,),
        )
        order = ["wechat", "alipay", "monthly", "cash"]
        by_method = {r["method"]: int(r["c"]) for r in rows}
        result = [{"name": PAYMENT_LABELS.get(m, m), "value": by_method.get(m, 0)} for m in order if by_method.get(m, 0)]
        for method, value in by_method.items():
            if method not in order:
                result.append({"name": PAYMENT_LABELS.get(method, method), "value": value})
        return result

    # ---------------- 进出场 ----------------
    def entry(self, plate: str, vehicle_type: Optional[str] = None,
              now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        plate = (plate or "").strip().upper().replace(" ", "")
        if len(plate) < 4:
            raise ValueError("车牌号不合法")
        lot = self.lot()
        total = int(lot["total_spaces"])
        occupied = int(self._row("SELECT COUNT(*) AS c FROM parking_sessions WHERE status='parked'")["c"])
        if occupied >= total:
            raise ValueError("车位已满，请引导车辆至临停区域")
        existing = self._row("SELECT * FROM parking_sessions WHERE plate=? AND status='parked'", (plate,))
        if existing:
            raise ValueError("该车辆已在场内，不能重复入场")
        vehicle = self._vehicle(plate)
        if not vehicle:
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            with db.connection(self.repo.db_path) as conn:
                conn.execute(
                    """INSERT INTO parking_vehicles
                    (plate, vehicle_type, owner_resident_id, owner_name, building, unit, room, pass_type, pass_expire_at, phone, created_at)
                    VALUES (?,?,NULL,'临时车主',NULL,NULL,NULL,'temporary',NULL,NULL,?)""",
                    (plate, vehicle_type or "小型车", now_str),
                )
            vehicle = self._vehicle(plate)
        is_monthly = self._is_monthly(vehicle, now)
        session_id = self._next_session_id(now)
        space = self._next_space(total)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        location = "".join(p for p in [vehicle.get("building") or "", vehicle.get("unit") or "", vehicle.get("room") or ""] if p)
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                """INSERT INTO parking_sessions
                (id, plate, vehicle_type, owner_resident_id, owner_name, location, entry_at,
                 status, space_no, is_monthly, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, plate, vehicle_type or vehicle.get("vehicle_type") or "小型车",
                 vehicle.get("owner_resident_id"), vehicle.get("owner_name"), location,
                 ts, "parked", space, 1 if is_monthly else 0, ts),
            )
        session = self._session_by_id(session_id) or {}
        billing_note = "月卡车辆，出场不另收费" if is_monthly else "临时车辆，出场按 30 分钟免费、2 元/小时计费"
        self._log(
            "parking_entry",
            {"session_id": session_id, "plate": plate, "space_no": space, "is_monthly": is_monthly},
            f"识别到车辆 {plate} 入场，分配车位 {space}；"
            + ("月卡有效，出场免收临时停车费。" if is_monthly else "按临时车规则计费。"),
            now, f"{plate} 入场",
        )
        return {"session": self._format_session(session, now), "lot": self.dashboard(now)["lot"], "billing_note": billing_note}

    def exit(self, plate: str, payment_method: Optional[str] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        plate = (plate or "").strip().upper().replace(" ", "")
        session = self._row(
            "SELECT * FROM parking_sessions WHERE plate=? AND status='parked' ORDER BY entry_at DESC LIMIT 1",
            (plate,),
        )
        if not session:
            raise KeyError("未找到该车辆的在场记录")
        entry = parse_dt(session["entry_at"]) or now
        duration = round(max(0.0, (now - entry).total_seconds() / 60), 1)
        billing = self.agent.calculate(duration, bool(session.get("is_monthly")), self.rules())
        fee = billing["fee"]
        payment_method = normalize_payment_method(payment_method)
        if session.get("is_monthly"):
            status, method, paid_at = "paid", "monthly", now.strftime("%Y-%m-%d %H:%M:%S")
        elif fee <= 0:
            # 免费时段出场：无需生成待缴费记录，直接放行
            status, method, paid_at = "paid", (payment_method or "free"), now.strftime("%Y-%m-%d %H:%M:%S")
        elif payment_method:
            status, method, paid_at = "paid", payment_method, now.strftime("%Y-%m-%d %H:%M:%S")
        else:
            status, method, paid_at = "unpaid", None, None
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                """UPDATE parking_sessions SET exit_at=?, duration_minutes=?, fee=?, status=?,
                   payment_method=?, paid_at=? WHERE id=?""",
                (ts, duration, fee, status, method, paid_at, session["id"]),
            )
        updated = self._session_by_id(session["id"]) or session
        log_output = {"session_id": session["id"], "plate": plate, "duration_minutes": duration, **billing,
                      "status": status, "payment_method": method}
        self._log(
            "parking_charge",
            log_output,
            f"车辆 {plate} 出场，{billing['rationale']}"
            + (" 月卡自动放行。" if status == "paid" and method == "monthly" else
               " 已现场缴费。" if status == "paid" else " 生成待缴费记录。"),
            now, f"{plate} 出场 / {duration:.0f} 分钟",
        )
        return {"session": self._format_session(updated, now), "billing": billing}

    def pay(self, session_id: str, payment_method: str = "wechat",
            now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        session = self._session_by_id(session_id)
        if not session:
            raise KeyError("停车记录不存在")
        if session.get("status") != "unpaid":
            raise ValueError("该记录不是待缴费状态")
        payment_method = normalize_payment_method(payment_method)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                "UPDATE parking_sessions SET status='paid', payment_method=?, paid_at=? WHERE id=?",
                (payment_method, ts, session_id),
            )
        updated = self._session_by_id(session_id) or session
        self._log(
            "parking_payment",
            {"session_id": session_id, "plate": session.get("plate"), "fee": session.get("fee"),
             "payment_method": payment_method, "paid_at": ts},
            f"收取 {session.get('plate')} 停车费 {float(session.get('fee') or 0):.2f} 元，支付方式 {PAYMENT_LABELS.get(payment_method, payment_method)}。",
            now, f"{session.get('plate')} 缴费",
        )
        return {"session": self._format_session(updated, now)}

    # ---------------- 列表 ----------------
    def list_sessions(self, status: Optional[str] = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._rows(
            f"SELECT * FROM parking_sessions{clause} ORDER BY entry_at DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 200)), max(0, offset)),
        )
        total = int(self._row(f"SELECT COUNT(*) AS c FROM parking_sessions{clause}", tuple(params))["c"])
        now = datetime.now().replace(microsecond=0)
        return {"items": [self._format_session(r, now) for r in rows], "total": total, "limit": limit, "offset": offset}

    def list_vehicles(self, q: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        where, params = [], []
        if q:
            like = f"%{q.strip()}%"
            where.append("(plate LIKE ? OR owner_name LIKE ? OR building LIKE ?)")
            params.extend([like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._rows(f"SELECT * FROM parking_vehicles{clause} ORDER BY plate LIMIT ?", (*params, min(max(limit, 1), 500)))
        total = int(self._row(f"SELECT COUNT(*) AS c FROM parking_vehicles{clause}", tuple(params))["c"])
        return {"items": rows, "total": total}
