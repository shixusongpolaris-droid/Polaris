"""小区守护服务：住户出入、连续未出门风险、自动预警。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import db
from ..agents.community import CommunityGuardAgent, parse_dt, parse_tags
from ..agents.profile import CARE_LEVELS, ResidentProfileAgent, parse_profile
from ..repository import Repository

STATUS_LABELS = {"open": "待处理", "ack": "已确认", "resolved": "已解除"}


class CommunityService:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.agent = CommunityGuardAgent()
        self.profile_agent = ResidentProfileAgent()
        self._scan_lock = threading.Lock()

    # ---------------- 工具 ----------------
    def _rows(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with db.connection(self.repo.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with db.connection(self.repo.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _resident(self, resident_id: str) -> Optional[Dict[str, Any]]:
        row = self._row("SELECT * FROM residents WHERE id=?", (resident_id,))
        if not row:
            return None
        row["tags"] = parse_tags(row.get("tags"))
        return row

    def _open_alerts_map(self) -> Dict[str, Dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM resident_alerts WHERE status IN ('open','ack') ORDER BY id DESC"
        )
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            result.setdefault(row["resident_id"], row)
        return result

    def _format_alert(self, row: Dict[str, Any]) -> Dict[str, Any]:
        resident = self._resident(row["resident_id"]) or {}
        tags = parse_tags(resident.get("tags")) if isinstance(resident.get("tags"), str) else (resident.get("tags") or [])
        return {
            **row,
            "resident_name": resident.get("name", "未知住户"),
            "location": _location(resident),
            "tags": tags,
            "family_contact": resident.get("family_contact"),
            "level_label": {"L1": "一级提醒", "L2": "二级预警", "L3": "三级紧急"}.get(row.get("level"), row.get("level")),
            "status_label": STATUS_LABELS.get(row.get("status"), row.get("status")),
        }

    def _format_resident(self, row: Dict[str, Any], now: datetime,
                         alerts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        resident = dict(row)
        resident["tags"] = parse_tags(resident.get("tags"))
        result = self.agent.evaluate(resident, now)
        profile = self.profile_agent.build(resident, result["risk_level"], result["inactive_days"])
        alert = (alerts or {}).get(resident["id"])
        return {
            **resident,
            "location": _location(resident),
            "presence": result["presence"],
            "presence_label": result["presence_label"],
            "inactive_days": result["inactive_days"],
            "threshold_days": result["threshold_days"],
            "risk_level": result["risk_level"],
            "risk_label": result["risk_label"],
            "should_alert": result["should_alert"],
            "message": result["message"],
            "care_level": profile["care_level"],
            "persona_label": profile["persona_label"],
            "profile": profile,
            "open_alert": self._format_alert(alert) if alert else None,
        }

    # ---------------- 查询 ----------------
    def overview(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        residents = self.list_all_residents()
        alerts_map = self._open_alerts_map()
        evaluated = [self._format_resident(r, now, alerts_map) for r in residents]
        stats = {
            "residents": len(evaluated),
            "home": sum(1 for r in evaluated if r["presence"] == "home"),
            "away": sum(1 for r in evaluated if r["presence"] == "away"),
            "inactive_3d": sum(1 for r in evaluated if r["presence"] == "home" and r["inactive_days"] >= 3),
            "inactive_7d": sum(1 for r in evaluated if r["presence"] == "home" and r["inactive_days"] >= 7),
            "open_alerts": len([a for a in alerts_map.values() if a.get("status") == "open"]),
            "critical_alerts": len([a for a in alerts_map.values() if a.get("status") == "open" and a.get("level") == "L3"]),
            "today_access_events": self._today_access_count(now),
        }
        risk_distribution = [
            {"name": "正常", "value": sum(1 for r in evaluated if r["risk_level"] == "normal"), "color": "#16a34a"},
            {"name": "关注", "value": sum(1 for r in evaluated if r["risk_level"] == "watch"), "color": "#f59e0b"},
            {"name": "预警", "value": sum(1 for r in evaluated if r["risk_level"] == "warning"), "color": "#f97316"},
            {"name": "紧急", "value": sum(1 for r in evaluated if r["risk_level"] == "critical"), "color": "#ef4444"},
        ]
        alerts = [self._format_alert(a) for a in alerts_map.values() if a.get("status") == "open"]
        alerts.sort(key=lambda a: ({"L3": 3, "L2": 2, "L1": 1}.get(a.get("level"), 0), a.get("inactive_days") or 0), reverse=True)
        top_residents = sorted(
            [r for r in evaluated if r["presence"] == "home"],
            key=lambda r: r["inactive_days"],
            reverse=True,
        )[:8]
        return {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "stats": stats,
            "risk_distribution": risk_distribution,
            "trend": self._access_trend(now),
            "building_ranking": self._building_ranking(evaluated),
            "alerts": alerts[:12],
            "inactive_top": top_residents,
            "recent_events": self._recent_events(),
        }

    def list_all_residents(self) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM residents ORDER BY id")
        for r in rows:
            r["tags"] = parse_tags(r.get("tags"))
        return rows

    def list_residents(self, risk: Optional[str] = None, presence: Optional[str] = None,
                       q: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        now = datetime.now().replace(microsecond=0)
        alerts_map = self._open_alerts_map()
        items = [self._format_resident(r, now, alerts_map) for r in self.list_all_residents()]
        risk_counts = {
            "normal": sum(1 for r in items if r["risk_level"] == "normal"),
            "watch": sum(1 for r in items if r["risk_level"] == "watch"),
            "warning": sum(1 for r in items if r["risk_level"] == "warning"),
            "critical": sum(1 for r in items if r["risk_level"] == "critical"),
        }
        filtered = items
        if risk:
            filtered = [r for r in filtered if r["risk_level"] == risk]
        if presence:
            filtered = [r for r in filtered if r["presence"] == presence]
        if q:
            query = q.strip()
            filtered = [
                r for r in filtered
                if query in (r.get("name") or "") or query in (r.get("location") or "")
                or query in (r.get("building") or "") or query in (r.get("room") or "")
            ]
        filtered.sort(key=lambda r: (r["risk_level"] != "normal", r["inactive_days"]), reverse=True)
        total = len(filtered)
        return {
            "items": filtered[max(0, offset):max(0, offset) + max(1, min(limit, 200))],
            "total": total,
            "limit": limit,
            "offset": offset,
            "risk_counts": risk_counts,
        }

    def get_resident(self, resident_id: str) -> Dict[str, Any]:
        now = datetime.now().replace(microsecond=0)
        resident = self._resident(resident_id)
        if not resident:
            raise KeyError("住户不存在")
        alerts_map = self._open_alerts_map()
        formatted = self._format_resident(resident, now, alerts_map)
        events = self._rows(
            "SELECT * FROM resident_access_events WHERE resident_id=? ORDER BY occurred_at DESC, id DESC LIMIT 30",
            (resident_id,),
        )
        alerts = self._rows(
            "SELECT * FROM resident_alerts WHERE resident_id=? ORDER BY id DESC LIMIT 20", (resident_id,)
        )
        return {
            "resident": formatted,
            "events": [{
                **e,
                "event_label": "出门" if e["event_type"] == "exit" else "进门",
            } for e in events],
            "alerts": [self._format_alert(a) for a in alerts],
        }

    def _next_resident_id(self) -> str:
        row = self._row("SELECT id FROM residents ORDER BY id DESC LIMIT 1")
        seq = 1
        if row:
            try:
                seq = int(str(row["id"]).replace("R", "")) + 1
            except ValueError:
                seq = 1
        return f"R{seq:04d}"

    def create_resident(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = str(data.get("name") or "").strip()
        building = str(data.get("building") or "").strip()
        if not name or not building:
            raise ValueError("姓名和楼栋不能为空")
        now = datetime.now().replace(microsecond=0)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        resident_id = str(data.get("id") or self._next_resident_id())
        age = int(data.get("age") or 60)
        if age < 0 or age > 120:
            raise ValueError("年龄需在 0-120 之间")
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        profile = parse_profile(data.get("profile") or {})
        care_level = str(data.get("care_level") or "").strip()
        if care_level not in CARE_LEVELS:
            care_level = ""
        row = {
            "id": resident_id,
            "name": name,
            "gender": data.get("gender") or "未知",
            "age": age,
            "phone": data.get("phone") or "",
            "building": building,
            "unit": data.get("unit") or "",
            "room": data.get("room") or "",
            "tags": json.dumps(tags, ensure_ascii=False),
            "family_contact": data.get("family_contact") or profile.get("family_members") or "",
            "presence": "home",
            "away_until": None,
            "absence_reason": None,
            "last_exit_at": ts,
            "last_entry_at": ts,
            "last_seen_at": ts,
            "inactive_days": 0,
            "risk_level": "normal",
            "profile_json": json.dumps(profile, ensure_ascii=False),
            "care_level": care_level,
            "emergency_contact": data.get("emergency_contact") or "",
            "profile_updated_at": ts,
            "created_at": ts,
            "updated_at": ts,
        }
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                """INSERT INTO residents
                (id,name,gender,age,phone,building,unit,room,tags,family_contact,presence,
                 away_until,absence_reason,last_exit_at,last_entry_at,last_seen_at,inactive_days,
                 risk_level,profile_json,care_level,emergency_contact,profile_updated_at,created_at,updated_at)
                VALUES (:id,:name,:gender,:age,:phone,:building,:unit,:room,:tags,:family_contact,:presence,
                 :away_until,:absence_reason,:last_exit_at,:last_entry_at,:last_seen_at,:inactive_days,
                 :risk_level,:profile_json,:care_level,:emergency_contact,:profile_updated_at,:created_at,:updated_at)""",
                row,
            )
        self.repo.add_agent_log(
            agent="resident_profile", agent_name="住户画像 Agent", action="create_resident",
            output={"resident_id": resident_id, "name": name, "tags": tags, "care_level": care_level or "自动评估"},
            rationale="新建住户档案并生成画像，后续由小区守护 Agent 持续更新未出门风险。",
            confidence=1.0, created_at=now, input_summary=f"{name} / {building}",
        )
        return {"resident": self._format_resident(self._resident(resident_id) or {}, now)}

    def update_resident(self, resident_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        current = self._resident(resident_id)
        if not current:
            raise KeyError("住户不存在")
        now = datetime.now().replace(microsecond=0)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        updates: Dict[str, Any] = {}
        for field in ("name", "gender", "phone", "building", "unit", "room", "family_contact", "emergency_contact"):
            if field in data:
                updates[field] = data.get(field) or ""
        if "age" in data:
            age = int(data.get("age") or 60)
            if age < 0 or age > 120:
                raise ValueError("年龄需在 0-120 之间")
            updates["age"] = age
        if "tags" in data:
            tags = data.get("tags") or []
            updates["tags"] = json.dumps(tags if isinstance(tags, list) else [str(tags)], ensure_ascii=False)
        if "profile" in data:
            profile = parse_profile(data.get("profile") or {})
            updates["profile_json"] = json.dumps(profile, ensure_ascii=False)
            updates["profile_updated_at"] = ts
            if profile.get("family_members") and "family_contact" not in data:
                updates["family_contact"] = profile["family_members"]
        if "care_level" in data:
            level = str(data.get("care_level") or "").strip()
            updates["care_level"] = level if level in CARE_LEVELS else ""
        if not updates:
            raise ValueError("没有需要更新的字段")
        updates["updated_at"] = ts
        set_sql = ", ".join(f"{k}=?" for k in updates)
        with db.connection(self.repo.db_path) as conn:
            conn.execute(f"UPDATE residents SET {set_sql} WHERE id=?", [*updates.values(), resident_id])
        self.repo.add_agent_log(
            agent="resident_profile", agent_name="住户画像 Agent", action="update_resident",
            output={"resident_id": resident_id, "fields": list(updates.keys())},
            rationale="更新住户档案与画像信息，风险预警继续由小区守护 Agent 按最新档案计算。",
            confidence=1.0, created_at=now, input_summary=f"{current.get('name')} / {resident_id}",
        )
        return {"resident": self._format_resident(self._resident(resident_id) or current, now)}

    def list_alerts(self, status: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._rows(f"SELECT * FROM resident_alerts{clause} ORDER BY id DESC LIMIT ?", (*params, min(max(limit, 1), 500)))
        items = [self._format_alert(r) for r in rows]
        return {"items": items, "total": len(items)}

    # ---------------- 出入与预警 ----------------
    def record_access(self, resident_id: str, event_type: str, occurred_at: Optional[datetime] = None,
                      source: str = "单元门禁") -> Dict[str, Any]:
        if event_type not in ("entry", "exit"):
            raise ValueError("event_type 只能是 entry 或 exit")
        resident = self._resident(resident_id)
        if not resident:
            raise KeyError("住户不存在")
        now = (occurred_at or datetime.now()).replace(microsecond=0)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                "INSERT INTO resident_access_events (resident_id, event_type, occurred_at, source) VALUES (?,?,?,?)",
                (resident_id, event_type, ts, source),
            )
            if event_type == "exit":
                conn.execute(
                    "UPDATE residents SET presence='away', last_exit_at=?, last_seen_at=?, away_until=NULL, updated_at=? WHERE id=?",
                    (ts, ts, ts, resident_id),
                )
            else:
                conn.execute(
                    "UPDATE residents SET presence='home', last_entry_at=?, last_seen_at=?, updated_at=? WHERE id=?",
                    (ts, ts, ts, resident_id),
                )
            cur = conn.execute(
                "UPDATE resident_alerts SET status='resolved', resolved_at=? WHERE resident_id=? AND status IN ('open','ack')",
                (ts, resident_id),
            )
            resolved = cur.rowcount
        self.repo.add_agent_log(
            agent="community_guard", agent_name="小区守护 Agent", action="record_access",
            output={"resident_id": resident_id, "event_type": event_type, "occurred_at": ts,
                    "alerts_resolved": resolved, "source": source},
            rationale=f"记录住户{'出门' if event_type == 'exit' else '进门'}事件，自动解除未出门预警 {resolved} 条。",
            confidence=0.99, created_at=now,
            input_summary=f"{resident.get('name')} / {event_type}",
        )
        return {
            "resident": self._format_resident(self._resident(resident_id) or resident, now),
            "event": {"resident_id": resident_id, "event_type": event_type,
                      "event_label": "出门" if event_type == "exit" else "进门",
                      "occurred_at": ts, "source": source},
            "alerts_resolved": resolved,
        }

    def scan(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = (now or datetime.now()).replace(microsecond=0)
        with self._scan_lock:
            residents = self.list_all_residents()
            decision = self.agent.run(residents, now)
            created, updated = 0, 0
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            with db.connection(self.repo.db_path) as conn:
                for alert in decision.output["alerts"]:
                    resident_id = alert["resident_id"]
                    existing = conn.execute(
                        "SELECT * FROM resident_alerts WHERE resident_id=? AND status IN ('open','ack') ORDER BY id DESC LIMIT 1",
                        (resident_id,),
                    ).fetchone()
                    if existing:
                        if existing["level"] != alert["level"] or abs((existing["inactive_days"] or 0) - alert["inactive_days"]) >= 0.5:
                            conn.execute(
                                "UPDATE resident_alerts SET level=?, inactive_days=?, threshold_days=?, message=?, detail_json=? WHERE id=?",
                                (alert["level"], alert["inactive_days"], alert["threshold_days"], alert["message"],
                                 json.dumps(alert, ensure_ascii=False), existing["id"]),
                            )
                            updated += 1
                    else:
                        conn.execute(
                            """INSERT INTO resident_alerts
                            (resident_id, alert_type, level, inactive_days, threshold_days, message, status, created_at, detail_json)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                            (resident_id, "inactivity", alert["level"], alert["inactive_days"],
                             alert["threshold_days"], alert["message"], "open", ts,
                             json.dumps(alert, ensure_ascii=False)),
                        )
                        created += 1
                # 更新住户风险缓存
                for resident in residents:
                    evaluated = self.agent.evaluate(resident, now)
                    conn.execute(
                        "UPDATE residents SET inactive_days=?, risk_level=?, updated_at=? WHERE id=?",
                        (evaluated["inactive_days"], evaluated["risk_level"], ts, resident["id"]),
                    )
            decision.output["alerts_created"] = created
            decision.output["alerts_updated"] = updated
            self.repo.add_agent_log(
                agent="community_guard", agent_name="小区守护 Agent", action="inactivity_scan",
                output=decision.output, rationale=decision.rationale,
                confidence=decision.confidence, created_at=now,
                input_summary=f"巡检 {len(residents)} 名住户",
            )
            return {
                "scanned": len(residents),
                "alerts_created": created,
                "alerts_updated": updated,
                "alerts": decision.output["alerts"],
                "risk_counts": decision.output["risk_counts"],
                "scanned_at": ts,
            }

    def ack_alert(self, alert_id: int, operator: str = "物业值班员") -> Dict[str, Any]:
        row = self._row("SELECT * FROM resident_alerts WHERE id=?", (alert_id,))
        if not row:
            raise KeyError("预警不存在")
        now = datetime.now().replace(microsecond=0)
        with db.connection(self.repo.db_path) as conn:
            conn.execute(
                "UPDATE resident_alerts SET status='ack', ack_at=?, ack_by=? WHERE id=?",
                (now.strftime("%Y-%m-%d %H:%M:%S"), operator, alert_id),
            )
        self.repo.add_agent_log(
            agent="community_guard", agent_name="小区守护 Agent", action="alert_acknowledged",
            output={"alert_id": alert_id, "resident_id": row["resident_id"], "operator": operator},
            rationale=f"物业人员 {operator} 已确认预警，安排后续上门核实。",
            confidence=1.0, created_at=now, input_summary=f"预警 {alert_id}",
        )
        return {"alert": self._format_alert(self._row("SELECT * FROM resident_alerts WHERE id=?", (alert_id,)) or row)}

    # ---------------- 统计辅助 ----------------
    def _today_access_count(self, now: datetime) -> int:
        row = self._row(
            "SELECT COUNT(*) AS c FROM resident_access_events WHERE date(occurred_at)=?",
            (now.strftime("%Y-%m-%d"),),
        )
        return int(row["c"]) if row else 0

    def _access_trend(self, now: datetime) -> List[Dict[str, Any]]:
        start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        access = {r["d"]: r["c"] for r in self._rows(
            "SELECT date(occurred_at) d, COUNT(*) c FROM resident_access_events WHERE date(occurred_at)>=? GROUP BY d",
            (start,),
        )}
        alerts = {r["d"]: r["c"] for r in self._rows(
            "SELECT date(created_at) d, COUNT(*) c FROM resident_alerts WHERE date(created_at)>=? GROUP BY d",
            (start,),
        )}
        result = []
        for i in range(7):
            day = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            result.append({"date": day, "access": int(access.get(day, 0)), "alerts": int(alerts.get(day, 0))})
        return result

    def _building_ranking(self, evaluated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buildings: Dict[str, Dict[str, int]] = {}
        for resident in evaluated:
            building = resident.get("building") or "未知"
            item = buildings.setdefault(building, {"building": building, "residents": 0, "inactive": 0})
            item["residents"] += 1
            if resident["presence"] == "home" and resident["inactive_days"] >= 3:
                item["inactive"] += 1
        return sorted(buildings.values(), key=lambda x: (x["inactive"], x["residents"]), reverse=True)[:8]

    def _recent_events(self) -> List[Dict[str, Any]]:
        rows = self._rows(
            """SELECT e.id, e.resident_id, e.event_type, e.occurred_at, e.source,
                      r.name AS resident_name, r.building, r.unit, r.room
               FROM resident_access_events e JOIN residents r ON r.id=e.resident_id
               ORDER BY e.occurred_at DESC, e.id DESC LIMIT 10"""
        )
        return [{
            **row,
            "event_label": "出门" if row["event_type"] == "exit" else "进门",
            "location": _location(row),
        } for row in rows]


def _location(resident: Dict[str, Any]) -> str:
    parts = [resident.get("building") or "", resident.get("unit") or "", resident.get("room") or ""]
    return "".join(parts) or "位置待确认"
