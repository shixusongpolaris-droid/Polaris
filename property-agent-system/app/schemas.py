"""API 请求模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    description: str = Field(..., min_length=4, max_length=1000, description="报修描述")
    reporter_name: str = Field("业主", max_length=40)
    reporter_phone: str = Field("", max_length=30)
    building: str = Field("", max_length=20)
    unit: str = Field("", max_length=20)
    room: str = Field("", max_length=20)


class AcceptRequest(BaseModel):
    worker_id: Optional[str] = None


class CompleteRequest(BaseModel):
    result_note: str = Field("", max_length=1000)
    photos_count: int = Field(0, ge=0, le=20)


class ReassignRequest(BaseModel):
    worker_id: str
    reason: str = Field("人工改派", max_length=200)


class MonitorRequest(BaseModel):
    now: Optional[str] = None


class CommunityAccessRequest(BaseModel):
    resident_id: str = Field(..., max_length=20)
    event_type: str = Field("exit", pattern="^(entry|exit)$")
    source: str = Field("单元门禁", max_length=40)
    occurred_at: Optional[str] = None


class CommunityScanRequest(BaseModel):
    now: Optional[str] = None


class AlertAckRequest(BaseModel):
    operator: str = Field("物业值班员", max_length=40)


class ParkingEntryRequest(BaseModel):
    plate: str = Field(..., min_length=4, max_length=20)
    vehicle_type: Optional[str] = Field(None, max_length=20)


class ParkingExitRequest(BaseModel):
    plate: str = Field(..., min_length=4, max_length=20)
    payment_method: Optional[str] = None


class ParkingPayRequest(BaseModel):
    payment_method: str = Field("wechat", max_length=20)


class ResidentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    gender: str = Field("未知", max_length=10)
    age: int = Field(60, ge=0, le=120)
    phone: str = Field("", max_length=30)
    building: str = Field(..., min_length=1, max_length=20)
    unit: str = Field("", max_length=20)
    room: str = Field("", max_length=20)
    tags: list[str] = Field(default_factory=list)
    family_contact: str = Field("", max_length=100)
    emergency_contact: str = Field("", max_length=100)
    care_level: str = Field("", max_length=20)
    profile: dict = Field(default_factory=dict)


class ResidentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    gender: Optional[str] = Field(None, max_length=10)
    age: Optional[int] = Field(None, ge=0, le=120)
    phone: Optional[str] = Field(None, max_length=30)
    building: Optional[str] = Field(None, min_length=1, max_length=20)
    unit: Optional[str] = Field(None, max_length=20)
    room: Optional[str] = Field(None, max_length=20)
    tags: Optional[list[str]] = None
    family_contact: Optional[str] = Field(None, max_length=100)
    emergency_contact: Optional[str] = Field(None, max_length=100)
    care_level: Optional[str] = Field(None, max_length=20)
    profile: Optional[dict] = None
