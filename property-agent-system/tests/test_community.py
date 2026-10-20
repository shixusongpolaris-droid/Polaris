from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.agents.community import CommunityGuardAgent, ParkingFeeAgent
from app.main import create_app


def insert_resident(db_path, resident_id="R0001", tags=None, presence="home", last_exit=None, last_entry=None, name="测试住户"):
    now = datetime.now().replace(microsecond=0)
    last_exit = last_exit or now - timedelta(days=5)
    last_entry = last_entry or last_exit + timedelta(hours=1)
    with db.connection(db_path) as conn:
        conn.execute(
            """INSERT INTO residents
            (id,name,gender,age,phone,building,unit,room,tags,family_contact,presence,
             away_until,absence_reason,last_exit_at,last_entry_at,last_seen_at,inactive_days,
             risk_level,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                resident_id, name, "女", 78, "138****0000", "12栋", "2单元", "301",
                __import__("json").dumps(tags or [], ensure_ascii=False), "家属 139****0000",
                presence, None, None, last_exit.strftime("%Y-%m-%d %H:%M:%S"),
                last_entry.strftime("%Y-%m-%d %H:%M:%S"), last_entry.strftime("%Y-%m-%d %H:%M:%S"),
                5.0, "normal", now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "community.db"), load_history=False, start_monitor=False)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def test_guard_agent_threshold_and_away():
    agent = CommunityGuardAgent()
    now = datetime(2026, 10, 20, 15, 0, 0)
    elderly = {
        "name": "王秀兰", "tags": ["独居老人"], "presence": "home",
        "last_exit_at": "2026-10-16 08:00:00", "last_entry_at": "2026-10-16 10:00:00",
        "created_at": "2026-01-01 00:00:00",
    }
    result = agent.evaluate(elderly, now)
    assert result["threshold_days"] == 1.0
    assert result["risk_level"] in ("warning", "critical")
    assert result["should_alert"] is True
    assert result["alert_level"] in ("L2", "L3")

    away = dict(elderly, presence="away", last_entry_at="2026-10-19 08:00:00")
    assert agent.evaluate(away, now)["should_alert"] is False


def test_community_scan_dedup_and_resolve(app):
    now = datetime.now().replace(microsecond=0)
    insert_resident(app.state.repo.db_path)
    service = app.state.community
    first = service.scan(now)
    assert first["alerts_created"] == 1
    second = service.scan(now + timedelta(minutes=5))
    assert second["alerts_created"] == 0
    overview = service.overview(now)
    assert overview["stats"]["open_alerts"] == 1
    access = service.record_access("R0001", "exit", occurred_at=now + timedelta(minutes=10))
    assert access["alerts_resolved"] == 1
    assert service.overview(now + timedelta(minutes=11))["stats"]["open_alerts"] == 0


def test_community_api_lifecycle(client, app):
    now = datetime.now().replace(microsecond=0)
    insert_resident(app.state.repo.db_path)
    scan = client.post("/api/community/scan")
    assert scan.status_code == 200
    assert scan.json()["alerts_created"] == 1
    overview = client.get("/api/community/overview")
    assert overview.status_code == 200
    assert overview.json()["stats"]["residents"] == 1
    alerts = client.get("/api/community/alerts?status=open").json()["items"]
    assert len(alerts) == 1
    ack = client.post(f"/api/community/alerts/{alerts[0]['id']}/ack", json={"operator": "值班员"})
    assert ack.status_code == 200
    assert ack.json()["alert"]["status"] == "ack"
    access = client.post("/api/community/access", json={"resident_id": "R0001", "event_type": "exit", "source": "物业代登记"})
    assert access.status_code == 200
    assert access.json()["resident"]["presence"] == "away"
    assert access.json()["alerts_resolved"] == 1


def test_parking_fee_rules():
    agent = ParkingFeeAgent()
    rules = {"free_minutes": 30, "rate_per_hour": 2.0, "daily_cap": 30.0}
    assert agent.calculate(20, False, rules)["fee"] == 0.0
    assert agent.calculate(220, False, rules)["fee"] == 8.0
    assert agent.calculate(600, False, rules)["fee"] == 20.0
    assert agent.calculate(2000, False, rules)["fee"] == 60.0
    assert agent.calculate(600, True, rules)["fee"] == 0.0


def test_parking_entry_exit_pay(app):
    service = app.state.parking
    start = datetime(2026, 10, 20, 9, 0, 0)
    entry = service.entry("粤B12345", now=start)
    assert entry["session"]["status"] == "parked"
    assert entry["session"]["space_no"].startswith("B1-")
    exit_result = service.exit("粤B12345", now=start + timedelta(minutes=90))
    assert exit_result["session"]["status"] == "unpaid"
    assert exit_result["billing"]["fee"] == 2.0  # 90 分钟：免费 30 分钟，计费 1 小时 × 2 元
    session_id = exit_result["session"]["id"]
    paid = service.pay(session_id, "wechat", now=start + timedelta(minutes=95))
    assert paid["session"]["status"] == "paid"
    dashboard = service.dashboard(now=start + timedelta(minutes=96))
    assert dashboard["lot"]["occupied"] == 0
    assert dashboard["stats"]["today_revenue"] >= 2.0


def test_parking_api(client):
    entry = client.post("/api/parking/entry", json={"plate": "粤B99999"})
    assert entry.status_code == 200
    assert entry.json()["session"]["status"] == "parked"
    plate = entry.json()["session"]["plate"]
    exit_resp = client.post("/api/parking/exit", json={"plate": plate})
    assert exit_resp.status_code == 200
    assert exit_resp.json()["session"]["status"] in ("unpaid", "paid")
    dashboard = client.get("/api/parking/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["lot"]["total_spaces"] == 120


def test_resident_profile_create_and_update(client):
    created = client.post("/api/community/residents", json={
        "name": "测试画像住户", "gender": "女", "age": 79,
        "phone": "13800001111", "building": "8栋", "unit": "1单元", "room": "502",
        "tags": ["独居老人", "慢病"], "family_contact": "女儿 13900002222",
        "emergency_contact": "女儿 13900002222",
        "profile": {
            "living_status": "独居", "health_status": "高血压", "mobility": "行动自如",
            "family_members": "女儿同城", "service_needs": ["代购药品"],
            "habits": "每天上午散步", "notes": "需要定期问候",
        },
    })
    assert created.status_code == 201
    resident = created.json()["resident"]
    assert resident["profile"]["care_level"] == "特殊关怀"
    assert "独居长者" in resident["profile"]["persona"]
    assert resident["profile"]["completeness"] == 100
    resident_id = resident["id"]

    updated = client.put(f"/api/community/residents/{resident_id}", json={
        "care_level": "重点关注",
        "profile": {
            "living_status": "独居", "health_status": "糖尿病", "mobility": "行动不便",
            "family_members": "女儿同城", "service_needs": ["送药上门"], "notes": "更新画像",
        },
    })
    assert updated.status_code == 200
    body = updated.json()["resident"]
    assert body["care_level"] == "重点关注"
    assert body["profile"]["health_status"] == "糖尿病"
    assert body["profile"]["mobility"] == "行动不便"
    assert body["profile"]["persona_label"]
