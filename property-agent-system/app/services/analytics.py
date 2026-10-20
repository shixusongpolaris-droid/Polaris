"""看板与分析服务：把历史工单 CSV + 实时工单库聚合成前端所需 JSON。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from ..config import EVAL_PATH, HISTORY_CSV, PRIORS_PATH, REPORT_DIR
from ..repository import Repository

_lock = threading.Lock()
_cache: Dict[str, Any] = {"mtime": None, "df": None}


def load_history(force: bool = False) -> pd.DataFrame:
    with _lock:
        mtime = HISTORY_CSV.stat().st_mtime if HISTORY_CSV.exists() else None
        if force or _cache["df"] is None or _cache["mtime"] != mtime:
            if not HISTORY_CSV.exists():
                df = pd.DataFrame()
            else:
                df = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig")
                if "created_at" in df.columns:
                    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
                for col in ["response_minutes", "duration_minutes", "sla_minutes", "satisfaction",
                            "photos_count", "is_timeout", "is_closed", "reassigned"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
            _cache["df"] = df
            _cache["mtime"] = mtime
        return _cache["df"]


def _round(value: Any, digits: int = 1, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return default


def _distribution(df: pd.DataFrame, col: str, top: int = 20) -> list:
    if df.empty or col not in df.columns:
        return []
    counts = df[col].fillna("未知").value_counts().head(top)
    return [{"name": str(k), "value": int(v)} for k, v in counts.items()]


def _trend(df: pd.DataFrame, days: int = 30) -> list:
    if df.empty or "created_at" not in df.columns:
        return []
    end = df["created_at"].max()
    if pd.isna(end):
        return []
    end = end.normalize()
    start = end - timedelta(days=days - 1)
    temp = df.copy()
    temp["date"] = temp["created_at"].dt.normalize()
    grouped = temp.groupby("date").agg(
        orders=("order_id", "count"),
        closed=("is_closed", "sum"),
        timeout=("is_timeout", "sum"),
    )
    index = pd.date_range(start, end, freq="D")
    grouped = grouped.reindex(index, fill_value=0)
    return [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "orders": int(row["orders"]),
            "closed": int(row["closed"]),
            "timeout": int(row["timeout"]),
        }
        for idx, row in grouped.iterrows()
    ]


def _building_ranking(df: pd.DataFrame, top: int = 8) -> list:
    if df.empty or "building" not in df.columns:
        return []
    grouped = df.groupby("building").agg(
        value=("order_id", "count"),
        timeout_rate=("is_timeout", "mean"),
    ).sort_values("value", ascending=False).head(top)
    return [
        {"name": str(idx), "value": int(row["value"]), "timeout_rate": _round(row["timeout_rate"] * 100)}
        for idx, row in grouped.iterrows()
    ]


def _worker_ranking(df: pd.DataFrame, top: int = 12) -> list:
    if df.empty or "worker_name" not in df.columns:
        return []
    grouped = df.groupby(["worker_name", "worker_trade"]).agg(
        orders=("order_id", "count"),
        avg_duration=("duration_minutes", "mean"),
        satisfaction=("satisfaction", "mean"),
        timeout_rate=("is_timeout", "mean"),
        closed_rate=("is_closed", "mean"),
    ).sort_values("orders", ascending=False).head(top)
    result = []
    for (name, trade), row in grouped.iterrows():
        result.append(
            {
                "name": str(name),
                "trade": str(trade),
                "orders": int(row["orders"]),
                "avg_duration": _round(row["avg_duration"]),
                "satisfaction": _round(row["satisfaction"], 2),
                "timeout_rate": _round(row["timeout_rate"] * 100),
                "closed_rate": _round(row["closed_rate"] * 100),
            }
        )
    return result


def _type_stats(df: pd.DataFrame) -> list:
    if df.empty or "repair_type" not in df.columns:
        return []
    grouped = df.groupby("repair_type").agg(
        count=("order_id", "count"),
        avg_duration=("duration_minutes", "mean"),
        p90_duration=("duration_minutes", lambda s: s.quantile(0.9)),
        timeout_rate=("is_timeout", "mean"),
        avg_satisfaction=("satisfaction", "mean"),
        sla_minutes=("sla_minutes", "median"),
        trade=("worker_trade", lambda s: s.mode().iloc[0] if not s.mode().empty else ""),
    ).sort_values("count", ascending=False)
    return [
        {
            "name": str(idx),
            "count": int(row["count"]),
            "avg_duration": _round(row["avg_duration"]),
            "p90_duration": _round(row["p90_duration"]),
            "timeout_rate": _round(row["timeout_rate"] * 100),
            "avg_satisfaction": _round(row["avg_satisfaction"], 2),
            "sla_minutes": int(row["sla_minutes"]) if not pd.isna(row["sla_minutes"]) else 0,
            "trade": str(row["trade"]),
        }
        for idx, row in grouped.iterrows()
    ]


def agent_metrics() -> Dict[str, Any]:
    data = _load_json(EVAL_PATH, {})
    metrics = data.get("metrics") or {}
    return {
        "understanding_accuracy": _round(metrics.get("understanding_accuracy", 0), 1),
        "dispatch_accuracy": _round(metrics.get("dispatch_accuracy", 0), 1),
        "verification_pass_rate": _round(metrics.get("verification_pass_rate", 0), 1),
        "manual_reassign_rate": _round(metrics.get("manual_reassign_rate", 0), 1),
        "avg_decision_ms": _round(metrics.get("avg_decision_ms", 0), 1),
        "sample_size": int(data.get("sample_size") or 0),
        "source": str(EVAL_PATH.name) if EVAL_PATH.exists() else "not_generated",
    }


def dashboard_summary(repo: Repository, days: int = 30, include_history: bool = True) -> Dict[str, Any]:
    df = load_history() if include_history else pd.DataFrame()
    live = repo.stats_live()
    analysis = _load_json(REPORT_DIR / "analysis_summary.json", {})
    if df.empty:
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "historical": {"total": 0, "closed_rate": 0, "timeout_rate": 0, "avg_duration": 0,
                           "avg_response": 0, "satisfaction": 0, "auto_dispatch_rate": 0, "period_days": 0},
            "live": live,
            "agent": agent_metrics(),
            "trend": [], "types": [], "facilities": [], "buildings": [], "workers": [],
            "urgency": [], "hourly": [], "recent": repo.recent_tickets(8),
            "insights": analysis.get("insights", []),
            "type_stats": [],
        }

    total = len(df)
    closed = int(df["is_closed"].fillna(0).sum()) if "is_closed" in df.columns else 0
    timeout = int(df["is_timeout"].fillna(0).sum()) if "is_timeout" in df.columns else 0
    auto_rate = 0.0
    if "dispatch_mode" in df.columns:
        auto_rate = (df["dispatch_mode"] == "auto").mean() * 100
    period_days = 0
    if "created_at" in df.columns and df["created_at"].notna().any():
        period_days = int((df["created_at"].max() - df["created_at"].min()).days) + 1
    historical = {
        "total": total,
        "closed_rate": _round(closed / total * 100),
        "timeout_rate": _round(timeout / total * 100),
        "avg_duration": _round(df["duration_minutes"].mean()) if "duration_minutes" in df.columns else 0,
        "avg_response": _round(df["response_minutes"].mean()) if "response_minutes" in df.columns else 0,
        "satisfaction": _round(df["satisfaction"].mean(), 2) if "satisfaction" in df.columns else 0,
        "auto_dispatch_rate": _round(auto_rate),
        "period_days": period_days,
        "avg_photos": _round(df["photos_count"].mean()) if "photos_count" in df.columns else 0,
    }
    hourly = []
    if "created_at" in df.columns:
        hours = df["created_at"].dt.hour.value_counts().sort_index()
        hourly = [{"hour": int(h), "orders": int(c)} for h, c in hours.items()]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "historical": historical,
        "live": live,
        "agent": agent_metrics(),
        "trend": _trend(df, days),
        "types": _distribution(df, "repair_type", 13),
        "facilities": _distribution(df, "facility_type", 10),
        "buildings": _building_ranking(df, 8),
        "workers": _worker_ranking(df),
        "urgency": _distribution(df, "urgency", 4),
        "hourly": hourly,
        "recent": repo.recent_tickets(8),
        "insights": analysis.get("insights", []),
        "type_stats": _type_stats(df),
        "monthly_trend": analysis.get("monthly_trend", []),
    }


def priors() -> Dict[str, Any]:
    return _load_json(PRIORS_PATH, {})


def evaluation_detail() -> Dict[str, Any]:
    return _load_json(EVAL_PATH, {"methodology": "评估结果尚未生成", "metrics": {}, "per_type": []})
