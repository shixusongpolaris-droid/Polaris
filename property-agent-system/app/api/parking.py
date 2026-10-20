"""停车管理 API：进出场、计费、缴费与看板。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..schemas import ParkingEntryRequest, ParkingExitRequest, ParkingPayRequest

router = APIRouter(prefix="/api/parking", tags=["parking"])


def _service(request: Request):
    return request.app.state.parking


@router.get("/dashboard")
def dashboard(request: Request):
    return _service(request).dashboard()


@router.post("/entry")
def entry(request: Request, body: ParkingEntryRequest):
    return _service(request).entry(body.plate, body.vehicle_type)


@router.post("/exit")
def exit_vehicle(request: Request, body: ParkingExitRequest):
    return _service(request).exit(body.plate, body.payment_method)


@router.post("/sessions/{session_id}/pay")
def pay(request: Request, session_id: str, body: Optional[ParkingPayRequest] = None):
    return _service(request).pay(session_id, body.payment_method if body else "wechat")


@router.get("/sessions")
def sessions(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _service(request).list_sessions(status=status, limit=limit, offset=offset)


@router.get("/vehicles")
def vehicles(
    request: Request,
    q: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    return _service(request).list_vehicles(q=q, limit=limit)
