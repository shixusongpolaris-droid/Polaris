"""小区守护 API：住户、出入、未出门预警。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas import (AlertAckRequest, CommunityAccessRequest, CommunityScanRequest,
                       ResidentCreate, ResidentUpdate)

router = APIRouter(prefix="/api/community", tags=["community"])


def _service(request: Request):
    return request.app.state.community


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip().replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD HH:MM:SS")


@router.get("/overview")
def overview(request: Request):
    return _service(request).overview()


@router.get("/residents")
def residents(
    request: Request,
    risk: Optional[str] = None,
    presence: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _service(request).list_residents(risk=risk, presence=presence, q=q, limit=limit, offset=offset)


@router.get("/residents/{resident_id}")
def resident_detail(request: Request, resident_id: str):
    return _service(request).get_resident(resident_id)


@router.post("/residents", status_code=201)
def create_resident(request: Request, body: ResidentCreate):
    return _service(request).create_resident(body.model_dump())


@router.put("/residents/{resident_id}")
def update_resident(request: Request, resident_id: str, body: ResidentUpdate):
    return _service(request).update_resident(resident_id, body.model_dump(exclude_unset=True))


@router.post("/access")
def access(request: Request, body: CommunityAccessRequest):
    return _service(request).record_access(
        body.resident_id, body.event_type,
        occurred_at=_parse(body.occurred_at), source=body.source or "单元门禁",
    )


@router.post("/scan")
def scan(request: Request, body: Optional[CommunityScanRequest] = None):
    return _service(request).scan(_parse(body.now if body else None))


@router.get("/alerts")
def alerts(request: Request, status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    return _service(request).list_alerts(status=status, limit=limit)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(request: Request, alert_id: int, body: Optional[AlertAckRequest] = None):
    return _service(request).ack_alert(alert_id, (body.operator if body else "物业值班员"))
