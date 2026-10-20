"""SQLite 数据库连接、建表与初始化。"""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import DB_PATH, HISTORY_CSV, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    trade TEXT NOT NULL,
    skills TEXT NOT NULL DEFAULT '[]',
    zone TEXT NOT NULL DEFAULT 'A',
    phone TEXT,
    level TEXT DEFAULT '中级',
    rating REAL DEFAULT 4.5,
    avg_duration_min REAL DEFAULT 90,
    max_daily INTEGER DEFAULT 8,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    title TEXT,
    description TEXT NOT NULL,
    reporter_name TEXT,
    reporter_phone TEXT,
    repair_type TEXT,
    facility_type TEXT,
    building TEXT,
    unit TEXT,
    room TEXT,
    location TEXT,
    urgency TEXT,
    urgency_score REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted',
    worker_id TEXT,
    worker_name TEXT,
    trade TEXT,
    priority TEXT,
    sla_minutes INTEGER,
    deadline TEXT,
    auto_dispatched INTEGER DEFAULT 1,
    reassigned INTEGER DEFAULT 0,
    escalated INTEGER DEFAULT 0,
    is_demo INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    understood_at TEXT,
    assigned_at TEXT,
    accepted_at TEXT,
    completed_at TEXT,
    closed_at TEXT,
    response_minutes REAL,
    duration_minutes REAL,
    is_timeout INTEGER DEFAULT 0,
    photos_count INTEGER DEFAULT 0,
    result_note TEXT,
    verification_verdict TEXT,
    verification_note TEXT,
    satisfaction REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    trace_id TEXT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT,
    ticket_id TEXT,
    agent TEXT NOT NULL,
    agent_name TEXT,
    action TEXT NOT NULL,
    input_summary TEXT,
    output_json TEXT,
    rationale TEXT,
    confidence REAL DEFAULT 0,
    duration_ms REAL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    trace_id TEXT,
    level TEXT NOT NULL,
    warning_type TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    escalated_to TEXT
);

CREATE TABLE IF NOT EXISTS historical_orders (
    order_id TEXT PRIMARY KEY,
    created_at TEXT,
    community TEXT,
    building TEXT,
    unit TEXT,
    room TEXT,
    description TEXT,
    repair_type TEXT,
    facility_type TEXT,
    urgency TEXT,
    worker_id TEXT,
    worker_name TEXT,
    worker_trade TEXT,
    dispatch_mode TEXT,
    reassigned INTEGER,
    response_minutes REAL,
    duration_minutes REAL,
    sla_minutes INTEGER,
    is_timeout INTEGER,
    is_closed INTEGER,
    satisfaction REAL,
    photos_count INTEGER,
    result_note TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS residents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    gender TEXT DEFAULT '未知',
    age INTEGER DEFAULT 60,
    phone TEXT,
    building TEXT NOT NULL,
    unit TEXT,
    room TEXT,
    tags TEXT DEFAULT '[]',
    family_contact TEXT,
    presence TEXT DEFAULT 'home',
    away_until TEXT,
    absence_reason TEXT,
    last_exit_at TEXT,
    last_entry_at TEXT,
    last_seen_at TEXT,
    inactive_days REAL DEFAULT 0,
    risk_level TEXT DEFAULT 'normal',
    profile_json TEXT DEFAULT '{}',
    care_level TEXT DEFAULT '',
    emergency_contact TEXT,
    profile_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_access_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT DEFAULT '单元门禁',
    note TEXT
);

CREATE TABLE IF NOT EXISTS resident_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL,
    alert_type TEXT DEFAULT 'inactivity',
    level TEXT NOT NULL,
    inactive_days REAL,
    threshold_days REAL,
    message TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL,
    ack_at TEXT,
    ack_by TEXT,
    resolved_at TEXT,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS parking_lots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    total_spaces INTEGER NOT NULL,
    free_minutes INTEGER DEFAULT 30,
    rate_per_hour REAL DEFAULT 2.0,
    daily_cap REAL DEFAULT 30.0,
    monthly_fee REAL DEFAULT 300.0
);

CREATE TABLE IF NOT EXISTS parking_vehicles (
    plate TEXT PRIMARY KEY,
    vehicle_type TEXT DEFAULT '小型车',
    owner_resident_id TEXT,
    owner_name TEXT,
    building TEXT,
    unit TEXT,
    room TEXT,
    pass_type TEXT DEFAULT 'temporary',
    pass_expire_at TEXT,
    phone TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS parking_sessions (
    id TEXT PRIMARY KEY,
    plate TEXT NOT NULL,
    vehicle_type TEXT DEFAULT '小型车',
    owner_resident_id TEXT,
    owner_name TEXT,
    location TEXT,
    entry_at TEXT NOT NULL,
    exit_at TEXT,
    duration_minutes REAL,
    fee REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'parked',
    payment_method TEXT,
    paid_at TEXT,
    space_no TEXT,
    is_monthly INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_residents_risk ON residents(risk_level);
CREATE INDEX IF NOT EXISTS idx_residents_building ON residents(building);
CREATE INDEX IF NOT EXISTS idx_access_resident ON resident_access_events(resident_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_alerts_resident ON resident_alerts(resident_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON resident_alerts(status);
CREATE INDEX IF NOT EXISTS idx_parking_status ON parking_sessions(status);
CREATE INDEX IF NOT EXISTS idx_parking_plate ON parking_sessions(plate, status);
CREATE INDEX IF NOT EXISTS idx_parking_entry ON parking_sessions(entry_at);
CREATE INDEX IF NOT EXISTS idx_parking_paid ON parking_sessions(paid_at);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON ticket_events(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_logs_ticket ON agent_logs(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_logs_agent ON agent_logs(agent, id);
CREATE INDEX IF NOT EXISTS idx_warnings_ticket ON warnings(ticket_id, status);
CREATE INDEX IF NOT EXISTS idx_history_type ON historical_orders(repair_type);
CREATE INDEX IF NOT EXISTS idx_history_created ON historical_orders(created_at);
"""

WORKERS_SEED = [
    # id, name, trade, skills, zone, phone, level, rating, avg_duration, max_daily
    ("W001", "张伟", "电梯维保", ["电梯", "困人救援", "曳引机"], "A", "13800001001", "高级", 4.8, 45, 6),
    ("W002", "李强", "电梯维保", ["电梯", "门机", "控制系统"], "B", "13800001002", "中级", 4.6, 52, 6),
    ("W003", "王建国", "水电工", ["给排水", "水管", "下水道", "阀门"], "A", "13800001003", "高级", 4.7, 72, 8),
    ("W004", "赵磊", "水电工", ["给排水", "水管", "水泵"], "B", "13800001004", "中级", 4.5, 88, 8),
    ("W005", "陈明", "电工", ["电气", "照明", "配电箱"], "A", "13800001005", "高级", 4.6, 65, 8),
    ("W006", "刘洋", "电工", ["电气", "照明", "线路"], "C", "13800001006", "中级", 4.4, 82, 8),
    ("W007", "孙杰", "燃气抢修", ["燃气", "消防", "应急处置"], "A", "13800001007", "高级", 4.9, 35, 6),
    ("W008", "周敏", "综合维修", ["门禁", "土建", "其他", "五金"], "B", "13800001008", "中级", 4.5, 110, 8),
    ("W009", "吴芳", "保洁", ["环境", "清理", "消杀"], "C", "13800001009", "中级", 4.3, 60, 10),
    ("W010", "郑海", "秩序维护", ["消防", "秩序", "应急"], "A", "13800001010", "高级", 4.6, 40, 8),
    ("W011", "马超", "土建维修", ["土建", "墙面", "地砖"], "B", "13800001011", "中级", 4.4, 180, 5),
    ("W012", "何静", "综合维修", ["门禁", "土建", "其他"], "C", "13800001012", "中级", 4.6, 95, 8),
]

_init_lock = threading.Lock()
_initialized = False


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_residents(conn: sqlite3.Connection) -> None:
    """给已有数据库补充住户画像字段，保证 v1.1 老库可平滑升级。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(residents)").fetchall()}
    columns = {
        "profile_json": "TEXT DEFAULT '{}'",
        "care_level": "TEXT DEFAULT ''",
        "emergency_contact": "TEXT",
        "profile_updated_at": "TEXT",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE residents ADD COLUMN {name} {ddl}")


def _seed_parking_lot(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO parking_lots
        (id, name, total_spaces, free_minutes, rate_per_hour, daily_cap, monthly_fee)
        VALUES ('LOT-B1', '地下一层停车场', 120, 30, 2.0, 30.0, 300.0)"""
    )


def _seed_workers(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
    if count:
        return
    conn.executemany(
        """INSERT INTO workers
        (id, name, trade, skills, zone, phone, level, rating, avg_duration_min, max_daily)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(w[0], w[1], w[2], json.dumps(w[3], ensure_ascii=False), *w[4:]) for w in WORKERS_SEED],
    )


HISTORY_COLUMNS = [
    "order_id", "created_at", "community", "building", "unit", "room", "description",
    "repair_type", "facility_type", "urgency", "worker_id", "worker_name", "worker_trade",
    "dispatch_mode", "reassigned", "response_minutes", "duration_minutes", "sla_minutes",
    "is_timeout", "is_closed", "satisfaction", "photos_count", "result_note", "source",
]


def import_history(conn: sqlite3.Connection, csv_path: Path | None = None, force: bool = False) -> int:
    path = Path(csv_path or HISTORY_CSV)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    existing = conn.execute("SELECT COUNT(*) FROM historical_orders").fetchone()[0]
    if existing and not force:
        return 0
    if force:
        conn.execute("DELETE FROM historical_orders")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    placeholders = ",".join("?" for _ in range(len(HISTORY_COLUMNS)))
    sql = f"INSERT OR REPLACE INTO historical_orders ({','.join(HISTORY_COLUMNS)}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c) for c in HISTORY_COLUMNS] for r in rows])
    return len(rows)


def initialize(db_path: str | Path | None = None, seed_history: bool = True, force_history: bool = False) -> None:
    """建表并写入基础数据。重复调用是幂等的。"""
    global _initialized
    with _init_lock:
        Path(db_path or DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        with connection(db_path) as conn:
            conn.executescript(SCHEMA)
            _migrate_residents(conn)
            _seed_workers(conn)
            _seed_parking_lot(conn)
            if seed_history:
                import_history(conn, force=force_history)
        _initialized = True


def ensure_initialized() -> None:
    if not _initialized:
        initialize()


def reset_database(db_path: str | Path | None = None) -> None:
    """删除并重建数据库（仅开发与测试使用）。"""
    path = Path(db_path or DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    global _initialized
    _initialized = False
    initialize(db_path=path)


def now_str(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
