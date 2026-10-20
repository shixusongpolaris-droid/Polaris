"""Agent 2：派单决策 Agent。

规则引擎 + 多因子评分：技能匹配、片区、当前负载、历史准时率、满意度、历史时长。
每个候选维修工的得分明细都会写入决策日志，便于人工复核。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import PRIORS_PATH, type_meta
from .base import AgentDecision, BaseAgent

PRIORITY_BY_URGENCY = {"紧急": "P0", "高": "P1", "中": "P2", "低": "P3"}

WEIGHTS = {
    "skill": 0.32,
    "zone": 0.16,
    "load": 0.20,
    "rating": 0.12,
    "reliability": 0.12,
    "speed": 0.08,
}


def building_zone(building: str) -> str:
    import re

    if not building:
        return ""
    m = re.search(r"(\d{1,2})", building)
    if not m:
        return "D"
    num = int(m.group(1))
    if 1 <= num <= 5:
        return "A"
    if 6 <= num <= 10:
        return "B"
    if 11 <= num <= 16:
        return "C"
    return "D"


def load_priors() -> Dict[str, Any]:
    try:
        if PRIORS_PATH.exists():
            with PRIORS_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


class DispatchAgent(BaseAgent):
    code = "dispatch"
    name = "派单决策 Agent"
    action = "dispatch_decision"

    def __init__(self, repo, priors: Optional[Dict[str, Any]] = None):
        self.repo = repo
        self.priors = priors if priors is not None else load_priors()

    def _worker_stats(self) -> Dict[str, Dict[str, Any]]:
        stats = dict(self.priors.get("worker_stats") or {})
        if not stats:
            stats = self.repo.worker_performance()
        return stats

    def _score_worker(
        self,
        worker: Dict[str, Any],
        stats: Dict[str, Any],
        ticket: Dict[str, Any],
        candidate_durations: List[float],
        type_p90: float,
    ) -> Dict[str, Any]:
        zone = building_zone(ticket.get("building") or "")
        skill = 1.0 if worker["trade"] in (ticket.get("required_trades") or []) else 0.55
        zone_score = 1.0 if not zone else (1.0 if worker.get("zone") == zone else 0.55)
        max_daily = max(int(worker.get("max_daily") or 8), 1)
        load_ratio = min(worker.get("active_tickets", 0) / max_daily, 1.0)
        load_score = 1.0 - load_ratio
        rating_score = min(float(worker.get("rating") or 4.5) / 5.0, 1.0)
        timeout_rate = float(stats.get("timeout_rate") or 0.0) / 100.0
        reliability = max(0.0, 1.0 - timeout_rate)
        avg_duration = float(stats.get("avg_duration") or worker.get("avg_duration_min") or 90)
        lo, hi = (min(candidate_durations), max(candidate_durations)) if candidate_durations else (avg_duration, avg_duration)
        if hi - lo > 1:
            speed = 1.0 - (avg_duration - lo) / (hi - lo)
        else:
            speed = 1.0 - min(avg_duration / max(type_p90 or 90, 30), 1.0) * 0.5
        speed = max(0.0, min(1.0, speed))
        components = {
            "skill": round(skill, 3),
            "zone": round(zone_score, 3),
            "load": round(load_score, 3),
            "rating": round(rating_score, 3),
            "reliability": round(reliability, 3),
            "speed": round(speed, 3),
        }
        total = sum(components[k] * w for k, w in WEIGHTS.items())
        return {
            "worker_id": worker["id"],
            "name": worker["name"],
            "trade": worker["trade"],
            "zone": worker.get("zone"),
            "score": round(total, 3),
            "components": components,
            "active_tickets": worker.get("active_tickets", 0),
            "max_daily": max_daily,
            "rating": worker.get("rating"),
            "avg_duration": round(avg_duration, 1),
            "timeout_rate": round(timeout_rate * 100, 1),
        }

    def run(self, ticket: Dict[str, Any], understanding: Dict[str, Any], now: Optional[datetime] = None) -> AgentDecision:
        now = now or datetime.now()
        repair_type = understanding.get("repair_type") or ticket.get("repair_type") or "其他报修"
        facility_type, _, sla_minutes, required_trades = type_meta(repair_type)
        workers = self.repo.list_workers(active_only=True)
        stats_all = self._worker_stats()
        work_ticket = dict(ticket)
        work_ticket["required_trades"] = required_trades
        work_ticket["building"] = understanding.get("location", {}).get("building") or ticket.get("building") or ""

        # 主工种优先；主工种无人时才启用备选工种
        candidates: List[Dict[str, Any]] = []
        for trade in required_trades:
            pool = [w for w in workers if w["trade"] == trade]
            if pool:
                candidates = pool
                break
        fallback = False
        if not candidates:
            fallback = True
            candidates = workers

        type_stats = (self.priors.get("type_stats") or {}).get(repair_type, {})
        type_p90 = float(type_stats.get("p90_duration") or sla_minutes)
        durations = []
        for w in candidates:
            st = stats_all.get(w["id"], {})
            durations.append(float(st.get("avg_duration") or w.get("avg_duration_min") or 90))
        scored = [self._score_worker(w, stats_all.get(w["id"], {}), work_ticket, durations, type_p90) for w in candidates]
        scored.sort(key=lambda x: (x["score"], -x["active_tickets"], x["worker_id"]), reverse=True)
        chosen = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None
        margin = chosen["score"] - (runner_up["score"] if runner_up else 0)

        priority = PRIORITY_BY_URGENCY.get(understanding.get("urgency"), "P2")
        deadline_dt = now + timedelta(minutes=int(sla_minutes))
        deadline = deadline_dt.strftime("%Y-%m-%d %H:%M:%S")
        c = chosen["components"]
        rationale = (
            f"按「主工种优先 + 多因子评分」筛选：{chosen['name']}（{chosen['trade']}）综合得分 {chosen['score']:.3f}，"
            f"其中技能 {c['skill']:.2f}、片区 {c['zone']:.2f}、负载 {c['load']:.2f}（当前 "
            f"{chosen['active_tickets']}/{chosen['max_daily']}）、历史准时率 {c['reliability']:.2f}、"
            f"满意度 {chosen['rating']}。要求 {sla_minutes} 分钟内完成（截止 {deadline}）。"
        )
        if fallback:
            rationale = "主工种当前无可用维修工，已启用综合维修兜底并标记人工关注。" + rationale
        output = {
            "worker_id": chosen["worker_id"],
            "worker_name": chosen["name"],
            "trade": chosen["trade"],
            "zone": chosen["zone"],
            "priority": priority,
            "sla_minutes": int(sla_minutes),
            "deadline": deadline,
            "score": chosen["score"],
            "candidates": scored[:5],
            "required_trades": required_trades,
            "facility_type": facility_type,
            "fallback": fallback,
            "margin": round(margin, 3),
        }
        confidence = min(0.98, 0.6 + chosen["score"] * 0.34 + min(margin, 0.2) * 0.2)
        return self.make_decision(
            output=output,
            rationale=rationale,
            confidence=confidence,
            input_summary=f"{repair_type} / {understanding.get('urgency')} / {work_ticket['building'] or '位置待确认'}",
        )
