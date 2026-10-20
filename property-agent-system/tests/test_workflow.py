from datetime import datetime


def test_submit_runs_agents_and_assigns(coordinator, repo):
    trace = coordinator.submit(
        "3栋2单元电梯门关不上，有人被困",
        reporter_name="李女士",
        building="3栋",
        unit="2单元",
        now=datetime(2026, 10, 20, 9, 0, 0),
    )
    ticket = trace["ticket"]
    assert ticket["status"] == "assigned"
    assert ticket["repair_type"] == "电梯困人"
    assert ticket["worker_name"] == "张伟"
    assert ticket["priority"] == "P0"
    assert ticket["deadline"] == "2026-10-20 09:30:00"
    assert [e["event_type"] for e in trace["events"]] == [
        "ticket_created", "understanding_completed", "ticket_assigned"
    ]
    agents = {log["agent"] for log in trace["logs"]}
    assert agents == {"understanding", "dispatch"}
    assert all(log["rationale"] for log in trace["logs"])


def test_accept_and_complete_with_photos(coordinator):
    start = datetime(2026, 10, 20, 9, 0, 0)
    trace = coordinator.submit("5栋1单元水管爆裂，楼道积水", building="5栋", unit="1单元", now=start)
    tid = trace["ticket"]["id"]
    coordinator.accept(tid, now=datetime(2026, 10, 20, 9, 8, 0))
    accepted = coordinator.repo.get_ticket(tid)
    assert accepted["status"] == "processing"
    assert accepted["response_minutes"] == 8.0

    result = coordinator.complete(
        tid,
        "已更换爆裂阀门并清理积水，压力测试正常",
        photos_count=2,
        now=datetime(2026, 10, 20, 9, 25, 0),
    )
    assert result["ticket"]["status"] == "closed"
    assert result["ticket"]["verification_verdict"] == "pass"
    assert result["ticket"]["is_timeout"] is False
    assert result["verification"]["verdict"] == "pass"


def test_verification_fail_then_resubmit(coordinator):
    start = datetime(2026, 10, 20, 9, 0, 0)
    trace = coordinator.submit("5栋1单元水管爆裂，楼道积水", building="5栋", unit="1单元", now=start)
    tid = trace["ticket"]["id"]
    coordinator.accept(tid, now=datetime(2026, 10, 20, 9, 10, 0))
    first = coordinator.complete(
        tid, "已更换爆裂阀门，关闭总阀。", photos_count=0, now=datetime(2026, 10, 20, 9, 38, 0)
    )
    assert first["ticket"]["status"] == "returned"
    assert first["verification"]["verdict"] == "return"
    assert first["ticket"]["is_timeout"] is True

    second = coordinator.complete(
        tid,
        "已更换爆裂阀门并清理积水，压力测试正常，照片已上传。",
        photos_count=2,
        now=datetime(2026, 10, 20, 9, 45, 0),
    )
    assert second["ticket"]["status"] == "closed"
    assert second["verification"]["verdict"] == "pass"


def test_monitoring_warns_and_escalates(coordinator, repo):
    start = datetime(2026, 10, 20, 9, 0, 0)
    trace = coordinator.submit("5栋1单元水管爆裂，楼道积水", building="5栋", unit="1单元", now=start)
    tid = trace["ticket"]["id"]
    # T+25 未接单：提醒
    result = coordinator.scan(datetime(2026, 10, 20, 9, 25, 0))
    assert result["scanned"] >= 1
    assert result["warnings_created"] >= 1
    warnings = repo.list_warnings(ticket_id=tid)
    assert any(w["warning_type"] == "accept_timeout" and w["level"] == "L1" for w in warnings)

    coordinator.accept(tid, now=datetime(2026, 10, 20, 9, 26, 0))
    # T+35 超时：升级主管
    result2 = coordinator.scan(datetime(2026, 10, 20, 9, 35, 0))
    assert result2["escalated"] >= 1
    ticket = repo.get_ticket(tid)
    assert ticket["escalated"] is True
    warnings = repo.list_warnings(ticket_id=tid)
    assert any(w["warning_type"] == "deadline_timeout" and w["level"] == "L2" for w in warnings)
    # 同类型预警不重复
    result3 = coordinator.scan(datetime(2026, 10, 20, 9, 36, 0))
    assert result3["warnings_created"] == 0


def test_manual_reassign(coordinator, repo):
    trace = coordinator.submit("8栋2单元配电箱跳闸，整层停电", building="8栋", unit="2单元")
    tid = trace["ticket"]["id"]
    old_worker = trace["ticket"]["worker_id"]
    new_trace = coordinator.reassign(tid, "W005", reason="原维修工负载过高")
    ticket = new_trace["ticket"]
    assert ticket["worker_id"] == "W005"
    assert ticket["reassigned"] is True
    assert ticket["auto_dispatched"] is False
    assert ticket["status"] == "assigned"
    assert any(e["event_type"] == "manual_reassign" for e in new_trace["events"])
    logs, _ = repo.list_agent_logs(ticket_id=tid)
    assert any(log["action"] == "human_takeover" for log in logs)


def test_demo_full_chain(coordinator):
    result = coordinator.run_demo()
    ticket = result["ticket"]
    assert ticket["status"] == "closed"
    assert ticket["is_timeout"] is True
    assert ticket["verification_verdict"] == "pass"
    assert len(result["steps"]) >= 12
    assert any(s["title"] == "接单超时提醒" for s in result["steps"])
    assert any(s["title"] == "处理超时升级" for s in result["steps"])
    assert any(s["title"] == "验收退回整改" for s in result["steps"])
    assert any(s["title"] == "闭环验收通过" for s in result["steps"])
    assert len(result["warnings"]) == 2
    # 重复运行会清理上一条演示工单
    second = coordinator.run_demo()
    demos, total = coordinator.repo.list_tickets(is_demo=True)
    assert total == 1
    assert second["ticket"]["id"] == demos[0]["id"]
