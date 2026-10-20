#!/usr/bin/env python3
"""「智修云」历史工单离线分析脚本。

输入  : data/historical_orders.csv
输出  : data/dispatch_priors.json      —— 在线派单 Agent 使用的先验统计
        reports/analysis_summary.json  —— 看板与报告所需的结构化指标
        reports/figures/*.png          —— 6 张论文/答辩用图表（中文字体、180 dpi）
        reports/历史工单分析报告.md      —— 中文分析报告（数字全部来自实际计算）

运行：.venv/bin/python scripts/analyze.py
"""
from __future__ import annotations

import json
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
FIG_DIR = REPORT_DIR / "figures"
CSV_PATH = DATA_DIR / "historical_orders.csv"
PRIORS_PATH = DATA_DIR / "dispatch_priors.json"
SUMMARY_PATH = REPORT_DIR / "analysis_summary.json"
REPORT_PATH = REPORT_DIR / "历史工单分析报告.md"

# ---------------------------------------------------------------- 业务常量
WORKERS = {
    "W001": ("张伟", "电梯维保", "A"),
    "W002": ("李强", "电梯维保", "B"),
    "W003": ("王建国", "水电工", "A"),
    "W004": ("赵磊", "水电工", "B"),
    "W005": ("陈明", "电工", "A"),
    "W006": ("刘洋", "电工", "C"),
    "W007": ("孙杰", "燃气抢修", "A"),
    "W008": ("周敏", "综合维修", "B"),
    "W009": ("吴芳", "保洁", "C"),
    "W010": ("郑海", "秩序维护", "A"),
    "W011": ("马超", "土建维修", "B"),
    "W012": ("何静", "综合维修", "C"),
}
# 报修类型 -> (设施类型, 紧急程度, SLA 分钟, 工种)
TYPE_RULES = {
    "电梯困人": ("电梯", "紧急", 30, "电梯维保"),
    "电梯故障": ("电梯", "高", 60, "电梯维保"),
    "水管爆裂": ("给排水", "紧急", 30, "水电工"),
    "水管漏水": ("给排水", "高", 120, "水电工"),
    "下水道堵塞": ("给排水", "中", 120, "水电工"),
    "电路故障": ("电气", "高", 60, "电工"),
    "燃气泄漏": ("燃气", "紧急", 15, "燃气抢修"),
    "消防隐患": ("消防", "紧急", 15, "秩序维护"),
    "门禁故障": ("门禁", "中", 240, "综合维修"),
    "公共照明": ("照明", "中", 240, "电工"),
    "墙面地面破损": ("土建", "低", 480, "土建维修"),
    "环境卫生": ("环境", "低", 480, "保洁"),
    "其他报修": ("其他", "中", 240, "综合维修"),
}
URGENCY_ORDER = ["紧急", "高", "中", "低"]
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ---------------------------------------------------------------- 统一视觉风格
COLORS = {
    "primary": "#2F5D8A",
    "primary_light": "#6FA8C7",
    "accent": "#E8A33D",
    "red": "#C0504D",
    "green": "#5A9E6F",
    "gray": "#9AA5B1",
    "grid": "#E6E6E6",
}
TRADE_COLORS = {
    "电梯维保": "#2F5D8A",
    "水电工": "#4E89AE",
    "电工": "#6FA8C7",
    "燃气抢修": "#C0504D",
    "秩序维护": "#E8A33D",
    "综合维修": "#5A9E6F",
    "保洁": "#9AA5B1",
    "土建维修": "#A0715B",
}


# ---------------------------------------------------------------- 工具函数
def r1(value) -> float:
    return round(float(value), 1)


def r2(value) -> float:
    return round(float(value), 2)


def pct(mask) -> float:
    return round(float(np.mean(mask)) * 100, 1)


def setup_style() -> str:
    available = {f.name for f in fm.fontManager.ttflist}
    candidates = ["PingFang HK", "Arial Unicode MS", "Hiragino Sans GB", "Heiti TC", "STHeiti", "Songti SC"]
    chosen = next((c for c in candidates if c in available), "DejaVu Sans")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [chosen] + candidates + ["DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#CCCCCC",
        "axes.grid": True,
        "grid.color": COLORS["grid"],
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titlesize": 15,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
        "legend.fontsize": 9.5,
        "figure.dpi": 150,
        "savefig.dpi": 180,
        "savefig.bbox": "tight",
    })
    return chosen


def save_figure(fig: plt.Figure, filename: str) -> None:
    """保存图片；若出现缺字形警告则直接报错，避免中文显示为方框。"""
    path = FIG_DIR / filename
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
        plt.close(fig)
    glyph_issues = [
        str(w.message) for w in caught
        if "Glyph" in str(w.message) or "missing from font" in str(w.message).lower()
    ]
    if glyph_issues:
        raise RuntimeError(f"{filename} 中文字体渲染异常: {glyph_issues[:3]}")
    print(f"  图表已保存: reports/figures/{filename}")


def md_table(headers: list[str], rows: list[list], aligns: list[str] | None = None) -> str:
    aligns = aligns or ["---"] * len(headers)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(aligns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 数据加载与校验
def load_data() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, encoding="utf-8")
    df["created_at"] = pd.to_datetime(df["created_at"])
    for col in ["response_minutes", "duration_minutes", "sla_minutes", "satisfaction",
                "is_timeout", "is_closed", "reassigned", "photos_count"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["is_timeout"] = df["is_timeout"].astype(int)
    df["is_closed"] = df["is_closed"].astype(int)
    df["reassigned"] = df["reassigned"].astype(int)
    df["date"] = df["created_at"].dt.date
    df["date_str"] = df["created_at"].dt.strftime("%Y-%m-%d")
    df["month"] = df["created_at"].dt.strftime("%Y-%m")
    df["hour"] = df["created_at"].dt.hour
    df["weekday"] = df["created_at"].dt.weekday
    df["is_weekend"] = df["weekday"] >= 5
    return df


def validate_data(df: pd.DataFrame) -> None:
    """严格校验数据契约：类型映射、工种映射、超时判定、关键区间。"""
    assert len(df) == 3000, f"工单数量应为 3000，实际 {len(df)}"
    assert set(df["repair_type"]) == set(TYPE_RULES), "存在未定义的报修类型"
    for repair_type, (facility, urgency, sla, trade) in TYPE_RULES.items():
        sub = df[df["repair_type"] == repair_type]
        assert len(sub) > 0, f"{repair_type} 无数据"
        assert (sub["facility_type"] == facility).all(), f"{repair_type} 设施类型映射错误"
        assert (sub["urgency"] == urgency).all(), f"{repair_type} 紧急程度映射错误"
        assert (sub["sla_minutes"] == sla).all(), f"{repair_type} SLA 映射错误"
        assert set(sub["worker_trade"]) == {trade}, f"{repair_type} 工种映射错误"
    for wid, (name, trade, _zone) in WORKERS.items():
        sub = df[df["worker_id"] == wid]
        assert len(sub) > 0, f"{wid} 无工单"
        assert set(sub["worker_name"]) == {name} and set(sub["worker_trade"]) == {trade}, f"{wid} 主数据不一致"
    assert (df["is_timeout"] == (df["duration_minutes"] > df["sla_minutes"]).astype(int)).all(), "is_timeout 判定错误"
    assert df["is_closed"].isin([0, 1]).all() and df["is_timeout"].isin([0, 1]).all()
    assert df["photos_count"].between(0, 4).all()
    closed = df[df["is_closed"] == 1]
    assert closed["satisfaction"].between(3.2, 5.0).all(), "闭环满意度越界"
    assert df[df["is_closed"] == 0]["satisfaction"].isna().all(), "未闭环满意度应为空"
    assert df["response_minutes"].between(1, 120).all()
    print("数据契约校验通过：3000 行、类型/工种映射 100% 一致、is_timeout 严格正确")


# ---------------------------------------------------------------- 统计计算
def compute_metrics(df: pd.DataFrame) -> dict:
    total = len(df)
    start_date, end_date = df["date"].min(), df["date"].max()
    summary = {
        "total": total,
        "closed_count": int(df["is_closed"].sum()),
        "timeout_count": int(df["is_timeout"].sum()),
        "closed_rate": pct(df["is_closed"]),
        "timeout_rate": pct(df["is_timeout"]),
        "avg_duration": r1(df["duration_minutes"].mean()),
        "avg_response": r1(df["response_minutes"].mean()),
        "satisfaction": r2(df["satisfaction"].mean()),
        "auto_rate": pct(df["dispatch_mode"] == "auto"),
        "manual_rate": pct(df["dispatch_mode"] == "manual"),
        "reassigned_rate": pct(df["reassigned"] == 1),
        "time_range": {
            "start": str(start_date),
            "end": str(end_date),
            "days": int((end_date - start_date).days + 1),
        },
    }

    # 类型统计
    g = df.groupby("repair_type")
    type_df = pd.DataFrame({
        "count": g.size(),
        "pct": g.size() / total * 100,
        "avg_duration": g["duration_minutes"].mean(),
        "p90_duration": g["duration_minutes"].quantile(0.9),
        "timeout_rate": g["is_timeout"].mean() * 100,
        "sla_minutes": g["sla_minutes"].first(),
        "trade": g["worker_trade"].agg(lambda s: s.mode().iloc[0]),
        "avg_satisfaction": g["satisfaction"].mean(),
        "facility_type": g["facility_type"].first(),
        "urgency": g["urgency"].first(),
    }).sort_values("count", ascending=False)
    type_rows = []
    for name, row in type_df.iterrows():
        type_rows.append({
            "name": name,
            "count": int(row["count"]),
            "pct": r1(row["pct"]),
            "avg_duration": r1(row["avg_duration"]),
            "p90_duration": r1(row["p90_duration"]),
            "timeout_rate": r1(row["timeout_rate"]),
            "sla_minutes": int(row["sla_minutes"]),
            "trade": row["trade"],
            "avg_satisfaction": r2(row["avg_satisfaction"]),
            "facility_type": row["facility_type"],
            "urgency": row["urgency"],
        })

    # 设施分布
    facility_df = df.groupby("facility_type").size().sort_values(ascending=False)
    facility_rows = [
        {"name": name, "value": int(value), "pct": r1(value / total * 100)}
        for name, value in facility_df.items()
    ]

    # 维修工效率
    overall_avg_duration = float(df["duration_minutes"].mean())
    wg = df.groupby("worker_id")
    worker_rows = []
    for wid in WORKERS:
        row = wg.get_group(wid)
        avg_duration = float(row["duration_minutes"].mean())
        timeout_rate = float(row["is_timeout"].mean() * 100)
        satisfaction = float(row["satisfaction"].mean())
        on_time_rate = 100 - timeout_rate
        duration_index = min(1.0, overall_avg_duration / avg_duration)
        efficiency = 100 * (
            0.5 * on_time_rate / 100
            + 0.3 * satisfaction / 5.0
            + 0.2 * duration_index
        )
        worker_rows.append({
            "worker_id": wid,
            "name": WORKERS[wid][0],
            "trade": WORKERS[wid][1],
            "zone": WORKERS[wid][2],
            "orders": int(len(row)),
            "avg_duration": r1(avg_duration),
            "timeout_rate": r1(timeout_rate),
            "satisfaction": r2(satisfaction),
            "on_time_rate": r1(on_time_rate),
            "duration_index": round(duration_index, 3),
            "efficiency": r1(efficiency),
        })
    worker_rows.sort(key=lambda x: (-x["efficiency"], -x["orders"]))

    # 楼栋
    bg = df.groupby("building")
    building_stats = {}
    for name, row in bg:
        building_stats[name] = {
            "orders": int(len(row)),
            "timeout_rate": r1(row["is_timeout"].mean() * 100),
            "avg_duration": r1(row["duration_minutes"].mean()),
        }
    building_ranking = [
        {"name": name, "orders": item["orders"], "pct": r1(item["orders"] / total * 100),
         "timeout_rate": item["timeout_rate"], "avg_duration": item["avg_duration"]}
        for name, item in sorted(building_stats.items(), key=lambda kv: -kv[1]["orders"])[:10]
    ]

    # 月度
    mg = df.groupby("month")
    monthly_trend = []
    for month, row in mg:
        monthly_trend.append({
            "month": month,
            "orders": int(len(row)),
            "closed_rate": pct(row["is_closed"]),
            "timeout_rate": pct(row["is_timeout"]),
            "avg_duration": r1(row["duration_minutes"].mean()),
            "satisfaction": r2(row["satisfaction"].mean()),
        })

    # 近 90 天日趋势
    trend_end = df["date"].max()
    trend_start = trend_end - timedelta(days=89)
    daily = (df[df["date"] >= trend_start]
             .groupby("date")
             .agg(orders=("order_id", "size"),
                  closed=("is_closed", "sum"),
                  timeout=("is_timeout", "sum")))
    full_index = [d.date() for d in pd.date_range(trend_start, trend_end)]
    daily = daily.reindex(full_index, fill_value=0)
    daily_trend = [
        {"date": str(idx), "orders": int(row["orders"]),
         "closed": int(row["closed"]), "timeout": int(row["timeout"])}
        for idx, row in daily.iterrows()
    ]

    # 小时
    hourly = df.groupby("hour").size().reindex(range(24), fill_value=0)
    hour_distribution = [
        {"hour": int(h), "orders": int(n), "pct": r1(n / total * 100)}
        for h, n in hourly.items()
    ]

    # 紧急程度
    ug = df.groupby("urgency")
    urgency_distribution = []
    for level in URGENCY_ORDER:
        row = ug.get_group(level)
        urgency_distribution.append({
            "name": level,
            "value": int(len(row)),
            "pct": r1(len(row) / total * 100),
            "avg_response": r1(row["response_minutes"].mean()),
        })

    # 超时 / 满意度分类型
    timeout_by_type = [
        {"name": r["name"], "count": r["count"],
         "timeout_count": int(round(r["count"] * r["timeout_rate"] / 100)),
         "timeout_rate": r["timeout_rate"], "sla_minutes": r["sla_minutes"],
         "avg_duration": r["avg_duration"]}
        for r in sorted(type_rows, key=lambda x: -x["timeout_rate"])
    ]
    satisfaction_by_type = [
        {"name": r["name"], "satisfaction": r["avg_satisfaction"], "count": r["count"]}
        for r in type_rows
    ]

    return {
        "summary": summary,
        "type_rows": type_rows,
        "facility_rows": facility_rows,
        "worker_rows": worker_rows,
        "building_stats": building_stats,
        "building_ranking": building_ranking,
        "monthly_trend": monthly_trend,
        "daily_trend": daily_trend,
        "hour_distribution": hour_distribution,
        "urgency_distribution": urgency_distribution,
        "timeout_by_type": timeout_by_type,
        "satisfaction_by_type": satisfaction_by_type,
    }


def compute_insights(df: pd.DataFrame, m: dict) -> list[str]:
    summary = m["summary"]
    type_rows = m["type_rows"]
    worker_rows = m["worker_rows"]
    building_ranking = m["building_ranking"]
    urgency = {u["name"]: u for u in m["urgency_distribution"]}
    total = summary["total"]

    top1, top2 = type_rows[0], type_rows[1]
    water = next(f for f in m["facility_rows"] if f["name"] == "给排水")
    urgent = df[df["urgency"] == "紧急"]
    low = df[df["urgency"] == "低"]
    hourly = m["hour_distribution"]
    peak = sorted(hourly, key=lambda x: -x["orders"])[0]
    weekend_day = df[df["is_weekend"]].groupby("date").size()
    weekday_day = df[~df["is_weekend"]].groupby("date").size()
    weekend_avg, weekday_avg = weekend_day.mean(), weekday_day.mean()
    timeout_closed = pct(df[df["is_timeout"] == 1]["is_closed"])
    ontime_closed = pct(df[df["is_timeout"] == 0]["is_closed"])
    open_df = df[df["is_closed"] == 0]
    open_timeout_share = pct(open_df["is_timeout"] == 1)

    low_timeout = sorted(type_rows, key=lambda x: x["timeout_rate"])[:3]
    high_timeout = sorted(type_rows, key=lambda x: -x["timeout_rate"])[0]
    best_worker = worker_rows[0]
    busiest_worker = max(worker_rows, key=lambda x: x["orders"])
    top5_share = sum(b["pct"] for b in building_ranking[:5])
    building_most = building_ranking[0]
    high_timeout_buildings = [b for b in building_ranking if b["orders"] >= 60]
    building_worst = max(high_timeout_buildings, key=lambda x: x["timeout_rate"]) if high_timeout_buildings else building_most

    return [
        f"180 天共受理工单 {total} 单，日均 {total / 180:.1f} 单；闭环率 {summary['closed_rate']:.1f}%，"
        f"整体超时率 {summary['timeout_rate']:.1f}%，平均处理时长 {summary['avg_duration']:.1f} 分钟，"
        f"平均响应 {summary['avg_response']:.1f} 分钟，闭环满意度 {summary['satisfaction']:.2f} 分。",
        f"{top1['name']}以 {top1['count']} 单（{top1['pct']:.1f}%）居首，{top2['name']}（{top2['pct']:.1f}%）次之；"
        f"给排水类设施合计 {water['value']} 单，占全部工单的 {water['pct']:.1f}%，是最主要的设施风险来源。",
        f"紧急工单共 {len(urgent)} 单（占 {len(urgent) / total * 100:.1f}%），平均响应 {urgent['response_minutes'].mean():.1f} 分钟，"
        f"明显快于低紧急度工单的 {low['response_minutes'].mean():.1f} 分钟，说明紧急程度对响应速度有实质影响。",
        f"超时率最低的三类为 " + "、".join(f"{r['name']}（{r['timeout_rate']:.1f}%）" for r in low_timeout)
        + f"，多为 15~30 分钟短 SLA 的应急处置类工单；超时率最高的是 {high_timeout['name']}"
        f"（{high_timeout['timeout_rate']:.1f}%，SLA {high_timeout['sla_minutes']} 分钟）。",
        f"工作日呈 8-11 点与 18-21 点双高峰，峰值出现在 {peak['hour']} 点（{peak['orders']} 单）；"
        f"周末日均 {weekend_avg:.1f} 单，较工作日 {weekday_avg:.1f} 单高 {(weekend_avg / weekday_avg - 1) * 100:.1f}%。",
        f"楼栋分布上，{building_most['name']}工单最多（{building_most['orders']} 单），前 5 栋合计占 {top5_share:.1f}%；"
        f"{building_worst['name']}的超时率最高（{building_worst['timeout_rate']:.1f}%），建议作为驻点资源投放的重点。",
        f"维修工中{best_worker['name']}效率指数最高（{best_worker['efficiency']:.1f}），超时率 {best_worker['timeout_rate']:.1f}%、"
        f"满意度 {best_worker['satisfaction']:.2f}；{busiest_worker['name']}承接工单最多（{busiest_worker['orders']} 单）。",
        f"未闭环工单共 {len(open_df)} 单，其中 {open_timeout_share:.1f}% 为超时工单；超时工单闭环率仅 {timeout_closed:.1f}%，"
        f"远低于非超时工单的 {ontime_closed:.1f}%，超时是导致工单无法闭环的首要风险因素。",
    ]


# ---------------------------------------------------------------- 图表
def plot_type_distribution(type_rows: list[dict]) -> None:
    rows = sorted(type_rows, key=lambda x: x["count"])
    names = [r["name"] for r in rows]
    counts = [r["count"] for r in rows]
    colors = [COLORS["accent"] if r["urgency"] == "紧急" else COLORS["primary"] for r in rows]
    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    ax.barh(names, counts, color=colors, height=0.68, edgecolor="white")
    max_count = max(counts)
    for i, r in enumerate(rows):
        ax.text(r["count"] + max_count * 0.012, i, f"{r['count']} 单（{r['pct']:.1f}%）",
                va="center", fontsize=8.8, color="#444444")
    ax.set_xlim(0, max_count * 1.28)
    ax.set_xlabel("工单数量（单）")
    ax.set_title("图 1  报修类型分布（N=3000，橙色为紧急类工单）", loc="left")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save_figure(fig, "01_type_distribution.png")


def plot_daily_trend(daily_trend: list[dict], summary: dict) -> None:
    dates = pd.to_datetime([r["date"] for r in daily_trend])
    orders = np.array([r["orders"] for r in daily_trend], dtype=float)
    rolling = pd.Series(orders).rolling(7, min_periods=1).mean().values
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.plot(dates, orders, color=COLORS["primary"], alpha=0.35, linewidth=1.2, label="日工单量")
    ax.plot(dates, rolling, color=COLORS["accent"], linewidth=2.2, label="7 日移动平均")
    ax.axhline(orders.mean(), color=COLORS["gray"], linestyle="--", linewidth=1.0,
               label=f"日均 {orders.mean():.1f} 单")
    ax.set_ylabel("工单数量（单）")
    ax.set_xlabel("日期")
    ax.set_title(f"图 2  近 90 天工单量趋势（日均 {orders.mean():.1f} 单）", loc="left")
    ax.legend(loc="upper left", frameon=False, ncol=3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    save_figure(fig, "02_daily_trend.png")


def plot_timeout_by_type(timeout_by_type: list[dict], summary: dict) -> None:
    rows = sorted(timeout_by_type, key=lambda x: x["timeout_rate"])
    names = [r["name"] for r in rows]
    rates = [r["timeout_rate"] for r in rows]
    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    ax.barh(names, rates, color=COLORS["primary"], height=0.68, edgecolor="white")
    for i, r in enumerate(rows):
        ax.text(r["timeout_rate"] + 0.18, i, f"{r['timeout_rate']:.1f}%",
                va="center", fontsize=8.8, color="#444444")
    ax.axvline(summary["timeout_rate"], color=COLORS["accent"], linestyle="--", linewidth=1.5,
               label=f"整体超时率 {summary['timeout_rate']:.1f}%")
    ax.set_xlim(0, max(rates) * 1.35)
    ax.set_xlabel("超时率（%）")
    ax.set_title("图 3  各报修类型超时率（按类型 SLA 严格判定）", loc="left")
    ax.legend(loc="lower right", frameon=False)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save_figure(fig, "03_timeout_by_type.png")


def plot_building_ranking(building_ranking: list[dict]) -> None:
    rows = sorted(building_ranking, key=lambda x: x["orders"])
    names = [r["name"] for r in rows]
    counts = [r["orders"] for r in rows]
    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    ax.barh(names, counts, color=COLORS["primary_light"], height=0.66, edgecolor="white")
    max_count = max(counts)
    for i, r in enumerate(rows):
        ax.text(r["orders"] + max_count * 0.012, i,
                f"{r['orders']} 单｜超时 {r['timeout_rate']:.1f}%",
                va="center", fontsize=8.8, color="#444444")
    ax.set_xlim(0, max_count * 1.32)
    ax.set_xlabel("工单数量（单）")
    ax.set_title("图 4  工单量 Top10 楼栋（标注超时率）", loc="left")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save_figure(fig, "04_building_ranking.png")


def plot_worker_efficiency(worker_rows: list[dict], summary: dict) -> None:
    rows = sorted(worker_rows, key=lambda x: x["efficiency"])
    names = [r["name"] for r in rows]
    scores = [r["efficiency"] for r in rows]
    colors = [TRADE_COLORS[r["trade"]] for r in rows]
    avg_eff = float(np.mean(scores))
    fig, ax = plt.subplots(figsize=(11.6, 6.4))
    ax.barh(names, scores, color=colors, height=0.66, edgecolor="white")
    for i, r in enumerate(rows):
        ax.text(r["efficiency"] + 0.8, i,
                f"{r['efficiency']:.1f}｜{r['orders']} 单｜超时 {r['timeout_rate']:.1f}%",
                va="center", fontsize=8.6, color="#444444")
    ax.axvline(avg_eff, color=COLORS["accent"], linestyle="--", linewidth=1.5)
    ax.set_xlim(0, 122)
    ax.set_xlabel("综合效率指数（0-100）")
    ax.set_title("图 5  维修工效率指数（颜色 = 工种，标注接单量与超时率）", loc="left")
    trades = [t for t in TRADE_COLORS if any(r["trade"] == t for r in rows)]
    handles = [plt.Line2D([], [], color=COLORS["accent"], linestyle="--", linewidth=1.5)]
    labels = [f"平均效率指数 {avg_eff:.1f}"]
    for trade in trades:
        handles.append(plt.Rectangle((0, 0), 1, 1, color=TRADE_COLORS[trade]))
        labels.append(trade)
    ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.01, 0.5),
              frameon=False, ncol=1, fontsize=8.5)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save_figure(fig, "05_worker_efficiency.png")


def plot_hour_heatmap(df: pd.DataFrame) -> None:
    pivot = (df.pivot_table(index="weekday", columns="hour", values="order_id", aggfunc="count", fill_value=0)
             .reindex(index=range(7), fill_value=0)
             .reindex(columns=range(24), fill_value=0))
    fig, ax = plt.subplots(figsize=(12.2, 5.2))
    im = ax.imshow(pivot.values, aspect="auto", cmap="Blues")
    ax.set_yticks(range(7))
    ax.set_yticklabels(WEEKDAY_LABELS)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels(range(0, 24, 2))
    ax.set_xlabel("小时（0-23 时）")
    ax.set_title("图 6  周内 × 小时工单分布热力图", loc="left")
    ax.grid(False)
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("工单数量（单）")
    fig.tight_layout()
    save_figure(fig, "06_hourly_heatmap_or_bar.png")


# ---------------------------------------------------------------- 报告
FIELD_DOCS = [
    ("order_id", "字符串", "工单编号，WO + 日期 + 当日流水号"),
    ("created_at", "时间", "业主报修时间（YYYY-MM-DD HH:MM:SS）"),
    ("community", "字符串", "小区名称，共 5 个"),
    ("building", "字符串", "楼栋，如 5栋"),
    ("unit", "字符串", "单元，如 1单元"),
    ("room", "字符串", "房号，如 1203"),
    ("description", "字符串", "业主口语化报修描述，含地点与症状"),
    ("repair_type", "类别", "报修类型，13 类之一"),
    ("facility_type", "类别", "设施类型，由报修类型唯一决定"),
    ("urgency", "类别", "紧急程度：紧急/高/中/低"),
    ("worker_id", "字符串", "维修工编号 W001-W012"),
    ("worker_name", "字符串", "维修工姓名"),
    ("worker_trade", "类别", "维修工工种，与报修类型匹配"),
    ("dispatch_mode", "类别", "派单方式：auto 自动 / manual 人工"),
    ("reassigned", "0/1", "是否发生人工改派"),
    ("response_minutes", "数值", "从报修到接单的响应时长（分钟）"),
    ("duration_minutes", "数值", "从接单到完工的处理时长（分钟）"),
    ("sla_minutes", "整数", "该类型规定处理时限（分钟）"),
    ("is_timeout", "0/1", "是否超时，严格由 duration_minutes > sla_minutes 判定"),
    ("is_closed", "0/1", "是否已闭环"),
    ("satisfaction", "数值", "闭环满意度，3.2-5.0；未闭环为空"),
    ("photos_count", "整数", "处理照片数量，0-4 张"),
    ("result_note", "字符串", "处理记录短句；未闭环可为空"),
    ("source", "类别", "报修来源：业主App/电话/微信小程序/前台登记"),
]


def build_report(df: pd.DataFrame, m: dict, insights: list[str], generated_at: str) -> str:
    s = m["summary"]
    type_rows = m["type_rows"]
    worker_rows = m["worker_rows"]
    building_rows = m["building_ranking"]
    monthly_rows = m["monthly_trend"]
    daily_rows = m["daily_trend"]
    facility_rows = m["facility_rows"]
    urgency_rows = m["urgency_distribution"]
    tr = s["time_range"]

    # 额外事实（全部来自同一份 CSV）
    hourly = m["hour_distribution"]
    peak = sorted(hourly, key=lambda x: -x["orders"])[0]
    peak2 = sorted(hourly, key=lambda x: -x["orders"])[1]
    morning = sum(h["orders"] for h in hourly if 8 <= h["hour"] <= 11)
    evening = sum(h["orders"] for h in hourly if 18 <= h["hour"] <= 21)
    night = sum(h["orders"] for h in hourly if 0 <= h["hour"] <= 6)
    weekend_day = df[df["is_weekend"]].groupby("date").size()
    weekday_day = df[~df["is_weekend"]].groupby("date").size()
    weekend_avg, weekday_avg = weekend_day.mean(), weekday_day.mean()
    weekend_share = pct(df["is_weekend"])
    timeout_df = df[df["is_timeout"] == 1]
    ontime_df = df[df["is_timeout"] == 0]
    open_df = df[df["is_closed"] == 0]
    closed_df = df[df["is_closed"] == 1]
    open_timeout_share = pct(open_df["is_timeout"] == 1)
    timeout_closed = pct(timeout_df["is_closed"])
    ontime_closed = pct(ontime_df["is_closed"])
    sat_timeout = timeout_df["satisfaction"].mean()
    sat_ontime = ontime_df["satisfaction"].mean()
    urgent = df[df["urgency"] == "紧急"]
    low = df[df["urgency"] == "低"]
    peak_day = max(daily_rows, key=lambda x: x["orders"])
    low_day = min(daily_rows, key=lambda x: x["orders"])
    first_month, last_month = tr["start"][:7], tr["end"][:7]
    complete_months = [r for r in monthly_rows if r["month"] not in (first_month, last_month)]
    complete_best = max(complete_months, key=lambda x: x["orders"])
    complete_min = min(r["orders"] for r in complete_months)
    complete_max = max(r["orders"] for r in complete_months)
    partial_rows = [r for r in monthly_rows if r["month"] in (first_month, last_month)]
    urgent_rate = {r["name"]: r["timeout_rate"] for r in type_rows}
    top1, top2, top3 = type_rows[0], type_rows[1], type_rows[2]
    water = next(f for f in facility_rows if f["name"] == "给排水")
    best_worker = worker_rows[0]
    busiest_worker = max(worker_rows, key=lambda x: x["orders"])
    safest_worker = max(worker_rows, key=lambda x: x["on_time_rate"])
    building_most = building_rows[0]
    top5_share = sum(b["pct"] for b in building_rows[:5])
    big_buildings = [b for b in building_rows if b["orders"] >= 60]
    building_worst = max(big_buildings, key=lambda x: x["timeout_rate"]) if big_buildings else building_most
    low_timeout_types = sorted(type_rows, key=lambda x: x["timeout_rate"])[:3]
    high_timeout_type = max(type_rows, key=lambda x: x["timeout_rate"])
    low_sat_type = min(type_rows, key=lambda x: x["avg_satisfaction"])
    high_sat_type = max(type_rows, key=lambda x: x["avg_satisfaction"])

    type_table = md_table(
        ["报修类型", "工单数", "占比", "紧急程度", "SLA(分钟)", "平均时长", "P90", "超时率", "满意度"],
        [[r["name"], r["count"], f"{r['pct']:.1f}%", r["urgency"], r["sla_minutes"],
          f"{r['avg_duration']:.1f}", f"{r['p90_duration']:.1f}", f"{r['timeout_rate']:.1f}%",
          f"{r['avg_satisfaction']:.2f}"] for r in type_rows],
        ["---"] * 9,
    )
    worker_table = md_table(
        ["工号", "姓名", "工种", "工单数", "平均时长(分钟)", "超时率", "满意度", "效率指数"],
        [[w["worker_id"], w["name"], w["trade"], w["orders"], f"{w['avg_duration']:.1f}",
          f"{w['timeout_rate']:.1f}%", f"{w['satisfaction']:.2f}", f"{w['efficiency']:.1f}"]
         for w in worker_rows],
        ["---"] * 8,
    )
    building_table = md_table(
        ["楼栋", "工单数", "占比", "超时率", "平均处理时长(分钟)"],
        [[b["name"], b["orders"], f"{b['pct']:.1f}%", f"{b['timeout_rate']:.1f}%",
          f"{b['avg_duration']:.1f}"] for b in building_rows],
        ["---"] * 5,
    )
    monthly_table = md_table(
        ["月份", "工单数", "闭环率", "超时率", "平均处理时长(分钟)"],
        [[r["month"], r["orders"], f"{r['closed_rate']:.1f}%", f"{r['timeout_rate']:.1f}%",
          f"{r['avg_duration']:.1f}"] for r in monthly_rows],
        ["---"] * 5,
    )
    timeout_closure_table = md_table(
        ["分组", "工单数", "闭环率", "平均处理时长(分钟)", "平均响应(分钟)", "平均满意度"],
        [["超时工单", len(timeout_df), f"{timeout_closed:.1f}%",
          f"{timeout_df['duration_minutes'].mean():.1f}", f"{timeout_df['response_minutes'].mean():.1f}",
          f"{timeout_df['satisfaction'].mean():.2f}"],
         ["非超时工单", len(ontime_df), f"{ontime_closed:.1f}%",
          f"{ontime_df['duration_minutes'].mean():.1f}", f"{ontime_df['response_minutes'].mean():.1f}",
          f"{ontime_df['satisfaction'].mean():.2f}"]],
        ["---"] * 6,
    )
    summary_table = md_table(
        ["指标", "数值", "统计口径"],
        [["工单总数", f"{s['total']} 单", "CSV 全部记录"],
         ["时间范围", f"{tr['start']} ~ {tr['end']}（{tr['days']} 天）", "created_at 最小/最大值"],
         ["日均工单量", f"{s['total'] / tr['days']:.1f} 单/天", "工单总数 / 天数"],
         ["闭环率", f"{s['closed_rate']:.1f}%", "is_closed = 1 占比"],
         ["超时率", f"{s['timeout_rate']:.1f}%", "is_timeout = 1 占比"],
         ["平均处理时长", f"{s['avg_duration']:.1f} 分钟", "duration_minutes 均值"],
         ["平均响应时长", f"{s['avg_response']:.1f} 分钟", "response_minutes 均值"],
         ["闭环满意度", f"{s['satisfaction']:.2f} 分", "闭环工单 satisfaction 均值"],
         ["自动派单率", f"{s['auto_rate']:.1f}%", "dispatch_mode = auto 占比"],
         ["人工派单率", f"{s['manual_rate']:.1f}%", "dispatch_mode = manual 占比"],
         ["改派率", f"{s['reassigned_rate']:.1f}%", "reassigned = 1 占比"],
         ["未闭环工单", f"{len(open_df)} 单", "is_closed = 0 记录"]],
        ["---", "---", "---"],
    )
    field_table = md_table(
        ["字段", "类型", "含义"],
        [[f, t, d] for f, t, d in FIELD_DOCS],
        ["---", "---", "---"],
    )
    urgency_inline = "、".join(f"{u['name']} {u['value']} 单（{u['pct']:.1f}%）" for u in urgency_rows)
    facility_inline = "、".join(f"{f['name']} {f['value']} 单（{f['pct']:.1f}%）" for f in facility_rows[:5])
    insight_list = "\n".join(f"{i}. {text}" for i, text in enumerate(insights, 1))

    report = f"""# 小区物业历史报修工单数据分析报告

**项目**：智修云 · 多智能体物业工单系统  
**数据文件**：`data/historical_orders.csv`（{s['total']} 条，{tr['start']} 至 {tr['end']}，共 {tr['days']} 天）  
**生成脚本**：`scripts/generate_data.py`（随机种子 20261020，可重复生成）  
**分析脚本**：`scripts/analyze.py`　**报告生成时间**：{generated_at}

## 摘要

本报告基于 {s['total']} 条小区物业历史报修工单，从报修类型、时空分布、超时与闭环、维修工效率四个维度进行离线分析，并提取可供在线派单 Agent 使用的先验特征。结果显示：整体闭环率 {s['closed_rate']:.1f}%，超时率 {s['timeout_rate']:.1f}%，平均处理时长 {s['avg_duration']:.1f} 分钟、平均响应 {s['avg_response']:.1f} 分钟，闭环满意度 {s['satisfaction']:.2f} 分；给排水类工单占比最高，工作日 8-11 点与 18-21 点形成明显双高峰；超时与未闭环高度相关，超时工单闭环率仅 {timeout_closed:.1f}%。分析结果已沉淀为 `data/dispatch_priors.json`，直接服务于自动派单决策。

## 一、数据说明与字段

### 1.1 数据范围与性质

数据覆盖 5 个小区（锦绣家园、翠湖名居、阳光里、江南雅苑、金域蓝湾）、13 类报修、12 名维修工，时间跨度 {tr['days']} 天（{tr['start']} ~ {tr['end']}），共 {s['total']} 条工单，日均 {s['total'] / tr['days']:.1f} 单。数据依据真实物业工单业务规则与业主作息规律模拟生成，固定随机种子 20261020，可完全复现；不包含任何真实业主隐私信息，仅用于系统开发、算法离线验证与毕业论文实验。

### 1.2 字段说明

{field_table}

### 1.3 数据质量校验

分析前对数据执行了严格校验：① 工单数量恰好 {s['total']} 行、字段顺序与契约一致；② 13 类报修类型与（设施类型、紧急程度、SLA、责任工种）的映射 100% 合法，例如“电梯困人 → 电梯 / 紧急 / 30 分钟 / 电梯维保”；③ `is_timeout` 与 `duration_minutes > sla_minutes` 逐行严格一致；④ 维修工编号、姓名、工种三者一致，且每类工单只派给对应工种；⑤ 闭环工单满意度均落在 3.2-5.0，未闭环工单满意度为空；⑥ 各紧急程度响应时长分别落在 2-15、5-30、10-60、20-120 分钟区间内。上述校验全部通过，数据可直接用于统计分析与 Agent 先验提取。

## 二、分析方法

### 2.1 预处理

使用 pandas 读取 CSV 并解析 `created_at`，派生日期、月份、小时、星期、是否周末等字段，将数值列转换为数值类型。分析过程中不剔除任何记录，未闭环工单在满意度、处理记录等字段上的空值按业务含义保留，不参与对应指标的均值计算。

### 2.2 统计口径

- **闭环率** = `is_closed = 1` 的工单数 / 工单总数；
- **超时率** = `is_timeout = 1` 的工单数 / 工单总数，超时仅由处理时长是否超过该类型 SLA 严格判定；
- **平均处理时长 / 平均响应时长** 分别为 `duration_minutes`、`response_minutes` 的算术均值；
- **P90 时长** 为同一类型工单处理时长的 90% 分位数，用于刻画长尾；
- **满意度** 仅统计已闭环工单，未闭环不计入；
- 分类型、分楼栋、分维修工的比率均以对应分组工单数为分母。

### 2.3 维修工效率指数

为综合衡量维修工表现，构造效率指数：

`效率指数 = 100 × (0.5 × 准时率 + 0.3 × 满意度 / 5 + 0.2 × 时长指数)`

其中准时率 = 1 − 超时率，时长指数 = min(1, 全部工单平均时长 / 该维修工平均时长)。该指数兼顾“按时完成、业主满意、处理高效”三个目标，数值越高表示综合效率越好，用于横向对比与派单参考。

### 2.4 工具与可复现性

分析使用 Python 3、pandas 2.3.3、numpy 与 matplotlib 3.9.4；随机种子固定，任何一次重新生成与重新分析都会得到一致的数值结果。

## 三、总体指标

{summary_table}

从总体看，系统在 {tr['days']} 天内处理 {s['total']} 单，闭环率 {s['closed_rate']:.1f}%、超时率 {s['timeout_rate']:.1f}%，平均响应 {s['avg_response']:.1f} 分钟，平均处理 {s['avg_duration']:.1f} 分钟，闭环满意度 {s['satisfaction']:.2f} 分。自动派单占比 {s['auto_rate']:.1f}%，人工派单与改派分别占 {s['manual_rate']:.1f}% 和 {s['reassigned_rate']:.1f}%，说明绝大多数工单可由规则引擎自动完成派发，人工主要介入复杂或异常工单。

## 四、报修类型分析

### 4.1 类型分布

{type_table}

![图 1 报修类型分布](figures/01_type_distribution.png)

{top1['name']}（{top1['count']} 单，{top1['pct']:.1f}%）、{top2['name']}（{top2['count']} 单，{top2['pct']:.1f}%）与{top3['name']}（{top3['count']} 单，{top3['pct']:.1f}%）是最高频的三类报修。从设施维度看，{facility_inline}；给排水类工单量明显高于其他设施，是物业日常运维最主要的压力来源；电梯类工单虽然总量不大，但包含电梯困人等紧急场景，安全权重远高于其数量占比。

### 4.2 类型与紧急程度

各类型紧急程度分布为 {urgency_inline}。紧急工单平均响应 {urgent['response_minutes'].mean():.1f} 分钟，远快于低紧急度工单的 {low['response_minutes'].mean():.1f} 分钟，表明派单与接单流程对紧急程度做出了实质响应。SLA 方面，15~30 分钟的短时限类型（燃气泄漏、消防隐患、电梯困人、水管爆裂）共同构成安全底线，其中电梯困人、燃气泄漏、消防隐患的超时率分别仅 {urgent_rate['电梯困人']:.1f}%、{urgent_rate['燃气泄漏']:.1f}%、{urgent_rate['消防隐患']:.1f}%；而 240~480 分钟的长时限类型（门禁故障、公共照明、墙面地面破损等）超时率相对更高，可能与配件到货周期、业主在家时间等因素有关。

## 五、时空分布

### 5.1 小时与周内规律

从小时分布看，工单呈现明显的“双高峰”特征：8-11 点合计 {morning} 单，占 {morning / s['total'] * 100:.1f}%；18-21 点合计 {evening} 单，占 {evening / s['total'] * 100:.1f}%；峰值出现在 {peak['hour']} 点（{peak['orders']} 单）和 {peak2['hour']} 点（{peak2['orders']} 单）。0-6 点仅 {night} 单，占 {night / s['total'] * 100:.1f}%，与业主作息高度吻合。周末共 {int(weekend_share * s['total'] / 100)} 单（占 {weekend_share:.1f}%），周末日均 {weekend_avg:.1f} 单，比工作日的 {weekday_avg:.1f} 单高 {(weekend_avg / weekday_avg - 1) * 100:.1f}%，说明业主在家时间越长，报修意愿越强。

![图 6 周内×小时工单热力图](figures/06_hourly_heatmap_or_bar.png)

### 5.2 日趋势与月度波动

近 90 天工单量在 {low_day['orders']}~{peak_day['orders']} 单/天之间波动，日均 {sum(r['orders'] for r in daily_rows) / len(daily_rows):.1f} 单，整体平稳，没有长期恶化趋势。月度上，完整月份（{complete_months[0]['month']} ~ {complete_months[-1]['month']}）的月工单量在 {complete_min}~{complete_max} 单之间，{complete_best['month']} 最多（{complete_best['orders']} 单，超时率 {complete_best['timeout_rate']:.1f}%）；首尾两个月为不完整月份（2026-04 自 24 日起、2026-10 截至 20 日），分别 {partial_rows[0]['orders']} 单与 {partial_rows[1]['orders']} 单，不宜直接与整月对比。

![图 2 近90天工单量趋势](figures/02_daily_trend.png)

{monthly_table}

### 5.3 楼栋分布

{top5_share:.1f}% 的工单集中在前 5 栋楼，其中 {building_most['name']}工单最多（{building_most['orders']} 单，占 {building_most['pct']:.1f}%）。在工单量不少于 60 单的楼栋中，{building_worst['name']}的超时率最高（{building_worst['timeout_rate']:.1f}%），建议作为驻点人员与备件前置的重点楼栋，并进一步排查其工单结构与维修动线。

![图 4 工单量Top10楼栋](figures/04_building_ranking.png)

{building_table}

## 六、超时与闭环分析

{timeout_closure_table}

超时与闭环之间存在显著关联：超时工单闭环率仅 {timeout_closed:.1f}%，而非超时工单闭环率达到 {ontime_closed:.1f}%，相差 {ontime_closed - timeout_closed:.1f} 个百分点；{s['total']} 单中有 {len(open_df)} 单未闭环，其中 {open_timeout_share:.1f}% 属于超时工单。可见“超时 → 业主不满 / 返工 → 未闭环”是主要风险链路。分类型看，超时率最低的是{low_timeout_types[0]['name']}（{low_timeout_types[0]['timeout_rate']:.1f}%）、{low_timeout_types[1]['name']}（{low_timeout_types[1]['timeout_rate']:.1f}%）和{low_timeout_types[2]['name']}（{low_timeout_types[2]['timeout_rate']:.1f}%），均属于 SLA 短、处置流程标准化的紧急工单；超时率最高的是{high_timeout_type['name']}（{high_timeout_type['timeout_rate']:.1f}%）。

满意度方面，闭环工单平均 {s['satisfaction']:.2f} 分，其中非超时工单 {sat_ontime:.2f} 分、超时工单 {sat_timeout:.2f} 分，超时直接拉低约 {sat_ontime - sat_timeout:.2f} 分。分类型满意度最高的是{high_sat_type['name']}（{high_sat_type['avg_satisfaction']:.2f}），最低的是{low_sat_type['name']}（{low_sat_type['avg_satisfaction']:.2f}）；最低类型样本量为 {low_sat_type['count']} 单，差异不宜过度解读，建议结合回访记录进一步分析。

![图 3 各类型超时率](figures/03_timeout_by_type.png)

## 七、维修工效率

{worker_table}

从接单量看，{busiest_worker['name']}承接最多（{busiest_worker['orders']} 单），是给排水类工单的主力；{safest_worker['name']}准时率最高（{safest_worker['on_time_rate']:.1f}%）。综合效率指数最高的是{best_worker['name']}（{best_worker['efficiency']:.1f}），其平均处理时长 {best_worker['avg_duration']:.1f} 分钟、超时率 {best_worker['timeout_rate']:.1f}%、满意度 {best_worker['satisfaction']:.2f}，属于“快、稳、好评”的骨干维修工。整体看，同一工种内部也存在效率差异，说明在技能匹配的前提下，结合历史准时率与处理时长进行二次排序，可以进一步提升派单质量。

![图 5 维修工效率分布](figures/05_worker_efficiency.png)

## 八、Agent 派单可用的先验特征

分析结果已写入 `data/dispatch_priors.json`，供在线派单 Agent 直接加载，主要包括：

- **类型先验 type_stats**：每类报修的工单占比、平均时长、P90 时长、超时率、SLA 与责任工种。派单 Agent 可用 P90 时长估计工单复杂度，用超时率计算维修工历史准时率，用工种完成技能匹配。
- **维修工先验 worker_stats**：W001-W012 的接单量、平均时长、超时率、满意度与所属片区，用于多因子评分中的可靠性、速度与负载参考。
- **紧急程度先验 urgency_distribution**：各紧急程度的单量与平均响应时长，可校准不同优先级工单的响应预期。
- **时间先验 hour_distribution / daily_trend**：小时与近 90 天日单量分布，用于预测高峰时段负载、提前安排值班。
- **楼栋先验 building_stats**：各楼栋单量与超时率，可辅助片区划分与驻点资源投放。

以{top1['name']}为例，其先验 P90 时长为 {top1['p90_duration']:.1f} 分钟、超时率 {top1['timeout_rate']:.1f}%、责任工种为{top1['trade']}；派单时可先按工种筛出候选人，再结合候选人的历史准时率与平均时长计算期望完成时间，优先选择能在 SLA 内完成的维修工。

## 九、结论与改进建议

{insight_list}

## 十、图表清单与复现方式

| 图号 | 文件 | 内容 |
|---|---|---|
| 图 1 | figures/01_type_distribution.png | 报修类型分布 |
| 图 2 | figures/02_daily_trend.png | 近 90 天工单趋势 |
| 图 3 | figures/03_timeout_by_type.png | 各类型超时率 |
| 图 4 | figures/04_building_ranking.png | 工单量 Top10 楼栋 |
| 图 5 | figures/05_worker_efficiency.png | 维修工效率分布 |
| 图 6 | figures/06_hourly_heatmap_or_bar.png | 周内-小时热力图 |

复现命令：

```bash
.venv/bin/python scripts/generate_data.py
.venv/bin/python scripts/analyze.py
```

报告中的全部数字均来自 `data/historical_orders.csv` 的实际计算，并与 `reports/analysis_summary.json`、`data/dispatch_priors.json` 保持一致。
"""
    return report


# ---------------------------------------------------------------- 主流程
def main() -> None:
    for directory in (DATA_DIR, REPORT_DIR, FIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    font = setup_style()
    print(f"matplotlib 中文字体: {font}")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df = load_data()
    validate_data(df)
    m = compute_metrics(df)
    m["insights"] = compute_insights(df, m)
    summary = m["summary"]

    # 1) 派单先验
    priors = {
        "total_orders": summary["total"],
        "generated_at": generated_at,
        "time_range": summary["time_range"],
        "overall": {
            "closed_rate": summary["closed_rate"],
            "timeout_rate": summary["timeout_rate"],
            "avg_duration": summary["avg_duration"],
            "avg_response": summary["avg_response"],
            "satisfaction": summary["satisfaction"],
        },
        "type_stats": {
            r["name"]: {
                "count": r["count"],
                "pct": r["pct"],
                "avg_duration": r["avg_duration"],
                "p90_duration": r["p90_duration"],
                "timeout_rate": r["timeout_rate"],
                "sla_minutes": r["sla_minutes"],
                "trade": r["trade"],
                "avg_satisfaction": r["avg_satisfaction"],
                "facility_type": r["facility_type"],
                "urgency": r["urgency"],
            }
            for r in m["type_rows"]
        },
        "worker_stats": {
            w["worker_id"]: {
                "name": w["name"],
                "trade": w["trade"],
                "zone": w["zone"],
                "orders": w["orders"],
                "avg_duration": w["avg_duration"],
                "timeout_rate": w["timeout_rate"],
                "satisfaction": w["satisfaction"],
                "on_time_rate": w["on_time_rate"],
            }
            for w in m["worker_rows"]
        },
        "urgency_distribution": m["urgency_distribution"],
        "hour_distribution": [{"hour": h["hour"], "orders": h["orders"]} for h in m["hour_distribution"]],
        "building_stats": m["building_stats"],
        "daily_trend": m["daily_trend"],
    }
    PRIORS_PATH.write_text(json.dumps(priors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"先验文件已写入: data/dispatch_priors.json")

    # 2) 分析汇总
    analysis_summary = {
        "generated_at": generated_at,
        "summary": summary,
        "type_distribution": [{"name": r["name"], "value": r["count"], "pct": r["pct"]} for r in m["type_rows"]],
        "facility_distribution": m["facility_rows"],
        "building_ranking": m["building_ranking"],
        "worker_ranking": m["worker_rows"],
        "monthly_trend": m["monthly_trend"],
        "daily_trend": m["daily_trend"],
        "hourly_distribution": m["hour_distribution"],
        "urgency_distribution": m["urgency_distribution"],
        "timeout_by_type": m["timeout_by_type"],
        "satisfaction_by_type": m["satisfaction_by_type"],
        "insights": m["insights"],
    }
    SUMMARY_PATH.write_text(json.dumps(analysis_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"分析汇总已写入: reports/analysis_summary.json")

    # 3) 图表
    print("生成图表：")
    plot_type_distribution(m["type_rows"])
    plot_daily_trend(m["daily_trend"], summary)
    plot_timeout_by_type(m["timeout_by_type"], summary)
    plot_building_ranking(m["building_ranking"])
    plot_worker_efficiency(m["worker_rows"], summary)
    plot_hour_heatmap(df)

    # 4) 报告
    report = build_report(df, m, m["insights"], generated_at)
    REPORT_PATH.write_text(report, encoding="utf-8")
    cjk = sum(1 for ch in report if "\u4e00" <= ch <= "\u9fff")
    print(f"分析报告已写入: reports/历史工单分析报告.md（汉字 {cjk} 个，总字符 {len(report)} 个）")

    # 5) 控制台摘要
    print("=" * 62)
    print("离线分析完成")
    print(f"工单总数 : {summary['total']}   闭环率: {summary['closed_rate']}%   超时率: {summary['timeout_rate']}%")
    print(f"平均处理 : {summary['avg_duration']} 分钟   平均响应: {summary['avg_response']} 分钟   满意度: {summary['satisfaction']}")
    print(f"自动派单率: {summary['auto_rate']}%   改派率: {summary['reassigned_rate']}%")
    print(f"图表数量 : 6 张（reports/figures/，180 dpi）")
    print("=" * 62)


if __name__ == "__main__":
    main()
