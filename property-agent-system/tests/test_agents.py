from datetime import datetime, timedelta

from app.agents.dispatch import DispatchAgent, building_zone
from app.agents.understanding import UnderstandingAgent
from app.agents.verification import VerificationAgent
from app.repository import Repository


def test_understanding_safety_cases():
    agent = UnderstandingAgent()
    out = agent.analyze("3栋2单元电梯门关不上，有人被困，里面还有老人")
    assert out["repair_type"] == "电梯困人"
    assert out["location"]["building"] == "3栋"
    assert out["location"]["unit"] == "2单元"
    assert out["urgency"] == "紧急"
    assert out["entities"]["people_trapped"] is True
    assert "老人" in out["entities"]["vulnerable_people"]

    # 消防器材/防火设施缺失同样属于安全类，必须是消防隐患 + 紧急
    for desc in ["2栋3单元楼道里的防火用具丢失", "5栋灭火器丢失", "消防水带不见了", "防火门损坏关不上"]:
        fire = agent.analyze(desc)
        assert fire["repair_type"] == "消防隐患", (desc, fire["repair_type"])
        assert fire["urgency"] == "紧急", (desc, fire["urgency"])
        assert fire["sla_minutes"] == 15, (desc, fire["sla_minutes"])
        assert fire["required_trades"] == ["秩序维护"], (desc, fire["required_trades"])

    old_address = agent.analyze("27号203防火用具丢失")
    assert old_address["location"]["building"] == "27号楼"
    assert old_address["location"]["room"] == "203室"


def test_understanding_water_burst():
    agent = UnderstandingAgent()
    out = agent.analyze("5栋1单元水管爆裂，楼道积水已经流到电梯口")
    assert out["repair_type"] == "水管爆裂"
    assert out["facility_type"] == "给排水"
    assert out["urgency"] == "紧急"
    assert out["sla_minutes"] == 30
    assert "5栋" in out["location"]["text"]


def test_understanding_ambiguous_goes_other():
    agent = UnderstandingAgent()
    out = agent.analyze("家里东西坏了，需要修一下")
    assert out["repair_type"] == "其他报修"
    assert out["confidence"] < 0.8


def test_dispatch_picks_matching_trade(repo):
    agent = DispatchAgent(repo)
    understanding = UnderstandingAgent().analyze("5栋1单元水管爆裂，楼道积水")
    ticket = {"id": "T1", "building": "5栋", "unit": "1单元"}
    out = agent.run(ticket, understanding)
    assert out.output["trade"] == "水电工"
    assert out.output["priority"] == "P0"
    assert out.output["sla_minutes"] == 30
    assert len(out.output["candidates"]) >= 1
    assert out.confidence > 0.8


def test_building_zone():
    assert building_zone("3栋") == "A"
    assert building_zone("8栋") == "B"
    assert building_zone("12栋") == "C"
    assert building_zone("") == ""


def test_verification_returns_without_photos():
    agent = VerificationAgent()
    ticket = {
        "id": "T1",
        "repair_type": "水管爆裂",
        "deadline": "2026-10-20 10:30:00",
        "completed_at": "2026-10-20 10:20:00",
    }
    decision = agent.evaluate(ticket, "已更换爆裂阀门并清理积水，压力测试正常", 0)
    assert decision.output["verdict"] == "return"
    assert "photos" in decision.output["failed_checks"]


def test_verification_passes_with_complete_record():
    agent = VerificationAgent()
    ticket = {
        "id": "T1",
        "repair_type": "水管爆裂",
        "deadline": "2026-10-20 10:30:00",
        "completed_at": "2026-10-20 10:20:00",
    }
    decision = agent.evaluate(ticket, "已更换爆裂阀门并清理积水，压力测试正常", 2)
    assert decision.output["verdict"] == "pass"
    assert decision.output["score"] == 100.0


def test_elevator_trap_overtime_is_hard_failure():
    agent = VerificationAgent()
    ticket = {
        "id": "T2",
        "repair_type": "电梯困人",
        "deadline": "2026-10-20 10:30:00",
        "completed_at": "2026-10-20 11:00:00",
    }
    decision = agent.evaluate(ticket, "已完成困人救援，电梯恢复运行，现场照片已上传", 2)
    assert decision.output["verdict"] == "return"
    assert "time_limit" in decision.output["failed_checks"]


def test_worker_seed_loaded(repo):
    workers = repo.list_workers()
    assert len(workers) == 12
    assert {w["trade"] for w in workers} >= {"电梯维保", "水电工", "电工", "综合维修"}

