"""生成小区守护与停车管理的演示数据（幂等，已有住户/车辆则跳过）。"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.agents.community import ParkingFeeAgent  # noqa: E402
from app.config import PARKING_RULES  # noqa: E402
from app.repository import Repository  # noqa: E402
from app.services.community import CommunityService  # noqa: E402

RNG = random.Random(20261020)

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN = ["建国", "秀兰", "志强", "桂芳", "海燕", "文博", "玉梅", "国庆", "晓东", "丽华", "明辉", "淑珍", "涛", "静", "敏", "磊", "洋", "勇", "艳", "娟", "军", "霞", "平", "刚", "桂英", "德福", "春梅", "小林", "雅琴", "振华"]
TAGS = [("独居老人", 0.12), ("慢病", 0.09), ("行动不便", 0.05), ("高龄", 0.10)]
ABSENCE_REASONS = ["探亲", "住院", "出差", "旅游"]


def mask_phone() -> str:
    return f"138****{RNG.randint(1000, 9999)}"


def pick_tags(age: int) -> list:
    tags = []
    for tag, p in TAGS:
        if RNG.random() < p:
            tags.append(tag)
    if age >= 80 and "高龄" not in tags and RNG.random() < 0.4:
        tags.append("高龄")
    return tags


def seed_residents(now: datetime) -> int:
    rows = []
    events = []
    for i in range(1, 241):
        building = f"{RNG.randint(1, 15)}栋"
        unit = f"{RNG.randint(1, 4)}单元"
        room = f"{RNG.randint(1, 18)}{RNG.choice(['01', '02', '03', '04'])}"
        age = RNG.randint(22, 92)
        name = RNG.choice(SURNAMES) + RNG.choice(GIVEN)
        tags = pick_tags(age)
        # 固定两名“独居老人”作为紧急预警演示样本，保证看板有 L3
        if i == 1:
            age, tags, name = 82, ["独居老人", "高龄"], "王秀兰"
        elif i == 2:
            age, tags, name = 76, ["独居老人", "慢病"], "李桂芳"
        away = False if i <= 2 else RNG.random() < 0.14
        planned = away and RNG.random() < 0.35
        if away:
            out_days = round(RNG.uniform(0.3, 5.5), 1)
            last_entry = now - timedelta(days=out_days)
            last_exit = last_entry + timedelta(minutes=RNG.randint(10, 120))
            last_seen = last_exit
            presence = "away"
            away_until = (now + timedelta(days=RNG.randint(2, 6))).strftime("%Y-%m-%d %H:%M:%S") if planned else None
            absence_reason = RNG.choice(ABSENCE_REASONS) if planned else None
            # 之前几轮正常出入
            for d in range(1, 4):
                e = last_entry - timedelta(days=d)
                x = e + timedelta(minutes=RNG.randint(20, 90))
                events.append((f"R{i:04d}", "entry", e.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
                events.append((f"R{i:04d}", "exit", x.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
            events.append((f"R{i:04d}", "entry", last_entry.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
            events.append((f"R{i:04d}", "exit", last_exit.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
            inactive = out_days
        else:
            r = RNG.random()
            if i == 1:
                days = 9.2
            elif i == 2:
                days = 5.6
            elif r < 0.84:
                days = RNG.uniform(0.1, 1.4)
            elif r < 0.93:
                days = RNG.uniform(1.5, 2.9)
            elif r < 0.975:
                days = RNG.uniform(3.2, 6.0)
            else:
                days = RNG.uniform(6.0, 12.0)
            last_exit = now - timedelta(days=days)
            last_entry = last_exit + timedelta(minutes=RNG.randint(15, 180))
            if last_entry > now:
                last_entry = last_exit + timedelta(minutes=5)
            last_seen = last_entry
            presence = "home"
            away_until, absence_reason = None, None
            inactive = round(days, 1)
            # 之前几轮正常出入
            for d in range(1, 5):
                e = last_exit - timedelta(days=d)
                x = e + timedelta(minutes=RNG.randint(30, 120))
                events.append((f"R{i:04d}", "entry", e.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
                events.append((f"R{i:04d}", "exit", x.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
            events.append((f"R{i:04d}", "exit", last_exit.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))
            events.append((f"R{i:04d}", "entry", last_entry.strftime("%Y-%m-%d %H:%M:%S"), "单元门禁"))

        rows.append((
            f"R{i:04d}", name, RNG.choice(["男", "女"]), age, mask_phone(),
            building, unit, room, json.dumps(tags, ensure_ascii=False),
            f"{RNG.choice(SURNAMES)}先生 {mask_phone()}", presence,
            away_until, absence_reason,
            last_exit.strftime("%Y-%m-%d %H:%M:%S"),
            last_entry.strftime("%Y-%m-%d %H:%M:%S"),
            last_seen.strftime("%Y-%m-%d %H:%M:%S"),
            inactive, "normal",
            (now - timedelta(days=RNG.randint(60, 720))).strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ))
    with db.connection() as conn:
        conn.executemany(
            """INSERT INTO residents
            (id,name,gender,age,phone,building,unit,room,tags,family_contact,presence,
             away_until,absence_reason,last_exit_at,last_entry_at,last_seen_at,inactive_days,
             risk_level,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.executemany(
            "INSERT INTO resident_access_events (resident_id,event_type,occurred_at,source) VALUES (?,?,?,?)",
            events,
        )
    return len(rows)


def backfill_profiles(now: datetime) -> int:
    """为缺少画像字段的住户生成可解释的画像初始值（不覆盖用户已编辑内容）。"""
    rng = random.Random(20261021)
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id,name,age,tags,family_contact,emergency_contact,profile_json FROM residents"
        ).fetchall()]
        updated = 0
        for row in rows:
            if row.get("profile_json") and row["profile_json"] not in ("", "{}"):
                continue
            tags = json.loads(row.get("tags") or "[]")
            age = int(row.get("age") or 60)
            living = "独居" if "独居老人" in tags else ("空巢" if age >= 75 and rng.random() < 0.35 else "与家人同住")
            if "慢病" in tags:
                health = rng.choice(["高血压", "糖尿病", "高血压、糖尿病", "冠心病"])
            else:
                health = rng.choice(["健康", "一般", "健康"])
            mobility = "行动不便" if "行动不便" in tags else ("行动缓慢" if age >= 85 else "行动自如")
            family = row.get("family_contact") or (
                "子女同城，周末探望" if living != "独居" else "子女外地，电话联系"
            )
            needs = []
            if "慢病" in tags:
                needs.extend(["代购药品", "复诊提醒"])
            if "行动不便" in tags:
                needs.extend(["代购生活用品", "上门维修协助"])
            if "独居老人" in tags:
                needs.extend(["每日问候", "紧急联系人"])
            habits = rng.choice(["每天上午在小区散步", "较少出门，生活规律", "傍晚在楼下活动", "周末由家属陪同外出"])
            notes = "；".join(tags) if tags else "暂无特殊备注"
            profile = {
                "living_status": living,
                "health_status": health,
                "mobility": mobility,
                "family_members": family,
                "service_needs": list(dict.fromkeys(needs)),
                "habits": habits,
                "notes": notes,
            }
            emergency = row.get("emergency_contact") or (family if "****" in family else f"{family} {mask_phone()}")
            conn.execute(
                "UPDATE residents SET profile_json=?, emergency_contact=?, profile_updated_at=? WHERE id=?",
                (json.dumps(profile, ensure_ascii=False), emergency, now.strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
            )
            updated += 1
    return updated


def make_plates(count: int) -> list:
    plates, used = [], set()
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
    while len(plates) < count:
        plate = "粤B" + "".join(RNG.choice(chars) for _ in range(5))
        if plate not in used:
            used.add(plate)
            plates.append(plate)
    return plates


def seed_vehicles(now: datetime) -> list:
    plates = make_plates(140)
    residents = []
    with db.connection() as conn:
        residents = [dict(r) for r in conn.execute("SELECT id,name,building,unit,room FROM residents").fetchall()]
        rows = []
        for i, plate in enumerate(plates):
            owner = RNG.choice(residents)
            monthly = i < 68
            expire = (now + timedelta(days=RNG.randint(20, 120))).strftime("%Y-%m-%d") if monthly else None
            rows.append((
                plate,
                RNG.choice(["小型车", "新能源", "SUV"]),
                owner["id"], owner["name"], owner["building"], owner["unit"], owner["room"],
                "monthly" if monthly else "temporary", expire,
                mask_phone(), now.strftime("%Y-%m-%d %H:%M:%S"),
            ))
        conn.executemany(
            """INSERT INTO parking_vehicles
            (plate,vehicle_type,owner_resident_id,owner_name,building,unit,room,pass_type,
             pass_expire_at,phone,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return plates


def seed_parking(now: datetime) -> int:
    agent = ParkingFeeAgent()
    rules = PARKING_RULES
    with db.connection() as conn:
        vehicles = [dict(r) for r in conn.execute("SELECT * FROM parking_vehicles").fetchall()]
    if not vehicles:
        return 0
    rows = []
    counters = {}

    def next_id(entry: datetime) -> str:
        key = entry.strftime("%Y%m%d")
        counters[key] = counters.get(key, 0) + 1
        return f"PS{key}{counters[key]:04d}"

    def is_monthly(vehicle: dict) -> bool:
        return vehicle["pass_type"] == "monthly" and (not vehicle["pass_expire_at"] or vehicle["pass_expire_at"] >= now.strftime("%Y-%m-%d"))

    # 1) 当前在场 42 辆
    parked_vehicles = RNG.sample(vehicles, 42)
    for idx, v in enumerate(parked_vehicles, start=1):
        entry = now - timedelta(minutes=RNG.randint(30, 600))
        monthly = is_monthly(v)
        rows.append((
            next_id(entry), v["plate"], v["vehicle_type"], v["owner_resident_id"], v["owner_name"],
            "".join(p for p in [v["building"] or "", v["unit"] or "", v["room"] or ""] if p),
            entry.strftime("%Y-%m-%d %H:%M:%S"), None, None, 0.0, "parked", None, None,
            f"B1-{idx:03d}", 1 if monthly else 0, entry.strftime("%Y-%m-%d %H:%M:%S"),
        ))

    # 2) 今日已缴费/待缴费出场 + 近 13 天历史
    temporary_vehicles = [v for v in vehicles if v["pass_type"] != "monthly"]
    for day_offset in range(0, 14):
        day = (now - timedelta(days=day_offset)).date()
        count = 55 if day_offset == 0 else 45
        for _ in range(count):
            if day_offset == 0:
                # 今日记录集中在上午至中午入场，确保演示时大部分已完成缴费
                v = RNG.choice(temporary_vehicles) if RNG.random() < 0.7 else RNG.choice(vehicles)
                entry = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=RNG.randint(6, 12), minutes=RNG.randint(0, 59)
                )
            else:
                v = RNG.choice(temporary_vehicles) if RNG.random() < 0.55 else RNG.choice(vehicles)
                entry = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=RNG.randint(7, 20), minutes=RNG.randint(0, 59)
                )
            duration = RNG.randint(25, 300)
            exit_at = entry + timedelta(minutes=duration)
            if exit_at > now:
                continue
            monthly = is_monthly(v)
            billing = agent.calculate(duration, monthly, rules)
            fee = billing["fee"]
            if day_offset == 0 and RNG.random() < 0.12:
                status, method, paid_at = "unpaid", None, None
            else:
                status = "paid"
                method = "monthly" if monthly else RNG.choice(["wechat", "wechat", "alipay", "alipay", "cash"])
                paid_at = exit_at.strftime("%Y-%m-%d %H:%M:%S")
            rows.append((
                next_id(entry), v["plate"], v["vehicle_type"], v["owner_resident_id"], v["owner_name"],
                "".join(p for p in [v["building"] or "", v["unit"] or "", v["room"] or ""] if p),
                entry.strftime("%Y-%m-%d %H:%M:%S"), exit_at.strftime("%Y-%m-%d %H:%M:%S"),
                duration, fee, status, method, paid_at,
                f"B1-{RNG.randint(1, 120):03d}", 1 if monthly else 0,
                entry.strftime("%Y-%m-%d %H:%M:%S"),
            ))
    with db.connection() as conn:
        conn.executemany(
            """INSERT INTO parking_sessions
            (id,plate,vehicle_type,owner_resident_id,owner_name,location,entry_at,exit_at,
             duration_minutes,fee,status,payment_method,paid_at,space_no,is_monthly,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def main() -> None:
    db.initialize(seed_history=True)
    now = datetime.now().replace(microsecond=0)
    repo = Repository()
    with db.connection() as conn:
        residents = conn.execute("SELECT COUNT(*) FROM residents").fetchone()[0]
        vehicles = conn.execute("SELECT COUNT(*) FROM parking_vehicles").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM parking_sessions").fetchone()[0]
    if residents == 0:
        count = seed_residents(now)
        print(f"住户：{count} 名")
    else:
        print(f"住户已存在（{residents} 名），跳过")
    profiled = backfill_profiles(now)
    if profiled:
        print(f"住户画像：补全 {profiled} 户")
    if vehicles == 0:
        seed_vehicles(now)
        print("车辆：140 辆（月卡 68 / 临时 72）")
    else:
        print(f"车辆已存在（{vehicles} 辆），跳过")
    if sessions == 0:
        count = seed_parking(now)
        print(f"停车记录：{count} 条")
    else:
        print(f"停车记录已存在（{sessions} 条），跳过")

    service = CommunityService(repo)
    result = service.scan(now)
    print(f"小区守护巡检：扫描 {result['scanned']} 人，新建预警 {result['alerts_created']} 条，"
          f"风险分布 {result['risk_counts']}")
    print(f"数据目录：{db.DB_PATH}")


if __name__ == "__main__":
    main()
