def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["history_orders"] == 0
    assert data["workers"] == 12


def test_dashboard_empty_is_safe(client):
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["historical"]["total"] == 0
    assert data["live"]["total"] == 0
    assert data["trend"] == []


def test_ticket_lifecycle_via_api(client):
    resp = client.post(
        "/api/tickets",
        json={
            "description": "5栋1单元水管爆裂，楼道积水",
            "reporter_name": "王女士",
            "building": "5栋",
            "unit": "1单元",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    ticket_id = data["ticket"]["id"]
    assert data["ticket"]["status"] == "assigned"
    assert data["ticket"]["repair_type"] == "水管爆裂"

    accept = client.post(f"/api/tickets/{ticket_id}/accept", json={})
    assert accept.status_code == 200
    assert accept.json()["ticket"]["status"] == "processing"

    complete = client.post(
        f"/api/tickets/{ticket_id}/complete",
        json={"result_note": "已更换爆裂阀门并清理积水，压力测试正常", "photos_count": 2},
    )
    assert complete.status_code == 200
    assert complete.json()["ticket"]["status"] == "closed"

    detail = client.get(f"/api/tickets/{ticket_id}")
    assert detail.status_code == 200
    assert detail.json()["ticket"]["status"] == "closed"
    assert len(detail.json()["events"]) >= 5
    assert len(detail.json()["logs"]) >= 3

    listing = client.get("/api/tickets?status=all")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_404_and_validation(client):
    assert client.get("/api/tickets/NOT-EXIST").status_code == 404
    bad = client.post("/api/tickets", json={"description": "坏"})
    assert bad.status_code == 422


def test_demo_api(client):
    resp = client.post("/api/demo/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"]["status"] == "closed"
    assert len(data["steps"]) >= 12
    assert data["metrics_after"]["total"] == 1

    latest = client.get("/api/demo/latest")
    assert latest.status_code == 200
    assert latest.json()["ticket"]["id"] == data["ticket"]["id"]

    summary = client.get("/api/dashboard/summary").json()
    assert summary["live"]["closed"] == 1
    assert summary["agent"]["understanding_accuracy"] >= 0


def test_logs_and_workers(client):
    client.post("/api/tickets", json={"description": "3栋2单元电梯门关不上，有人被困"})
    logs = client.get("/api/logs").json()
    assert logs["total"] >= 2
    agents = {item["agent"] for item in logs["items"]}
    assert "understanding" in agents and "dispatch" in agents
    workers = client.get("/api/workers").json()
    assert workers["total"] == 12
    assert any(w["trade"] == "水电工" for w in workers["items"])


def test_reassign_api_contract(client):
    created = client.post("/api/tickets", json={"description": "8栋2单元配电箱跳闸，整层停电"}).json()
    tid = created["ticket"]["id"]
    resp = client.post(f"/api/tickets/{tid}/reassign", json={"worker_id": "W005", "reason": "负载均衡"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"]["worker_id"] == "W005"
    assert data["ticket"]["reassigned"] is True
    assert data["trace"]["ticket"]["id"] == tid
    assert any(e["event_type"] == "manual_reassign" for e in data["trace"]["events"])
