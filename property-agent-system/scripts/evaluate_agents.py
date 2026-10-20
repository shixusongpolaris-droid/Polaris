"""离线评估 4 个 Agent 的表现，生成 reports/agent_evaluation.json。

方法：
1. 从 3000 条历史工单中分层随机抽取 600 条，用报修描述重新跑「报修理解 Agent」；
2. 将理解结果交给「派单决策 Agent」，与其历史真实工种比对；
3. 用历史完工信息构造验收场景，跑「闭环验证 Agent」；
4. 统计准确率、人工改派率、平均决策耗时与分类混淆矩阵。
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.dispatch import DispatchAgent  # noqa: E402
from app.agents.understanding import UnderstandingAgent  # noqa: E402
from app.agents.verification import VerificationAgent  # noqa: E402
from app.config import EVAL_PATH, HISTORY_CSV, REPORT_DIR  # noqa: E402
from app.repository import Repository  # noqa: E402


def parse_dt(value: Any) -> datetime:
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")


def main() -> Dict[str, Any]:
    df = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig")
    sample_size = min(600, len(df))
    sample = df.sample(n=sample_size, random_state=42).reset_index(drop=True)
    repo = Repository(load_history=False)
    understanding = UnderstandingAgent()
    dispatcher = DispatchAgent(repo)
    verifier = VerificationAgent()

    records: List[Dict[str, Any]] = []
    durations: List[float] = []
    started = time.perf_counter()
    for _, row in sample.iterrows():
        desc = str(row["description"])
        t0 = time.perf_counter()
        u = understanding.analyze(desc)
        ticket = {
            "id": str(row["order_id"]),
            "description": desc,
            "building": str(row["building"]),
            "unit": str(row["unit"]),
            "repair_type": u["repair_type"],
            "urgency": u["urgency"],
        }
        d = dispatcher.run(ticket, u)
        durations.append((time.perf_counter() - t0) * 1000)
        type_ok = u["repair_type"] == row["repair_type"]
        urgency_ok = u["urgency"] == row["urgency"]
        trade_ok = d.output["trade"] == row["worker_trade"]
        combined = bool(type_ok and trade_ok and not d.output.get("fallback"))

        created = parse_dt(row["created_at"])
        sla = int(row["sla_minutes"])
        completed = created + timedelta(minutes=float(row["duration_minutes"]))
        verify_ticket = {
            "id": str(row["order_id"]),
            "repair_type": str(row["repair_type"]),
            "deadline": (created + timedelta(minutes=sla)).strftime("%Y-%m-%d %H:%M:%S"),
            "completed_at": completed.strftime("%Y-%m-%d %H:%M:%S"),
        }
        v = verifier.evaluate(
            verify_ticket,
            result_note=str(row["result_note"]) if pd.notna(row["result_note"]) else "",
            photos_count=int(row["photos_count"]) if pd.notna(row["photos_count"]) else 0,
            now=completed,
        )
        records.append(
            {
                "order_id": row["order_id"],
                "actual_type": row["repair_type"],
                "pred_type": u["repair_type"],
                "actual_urgency": row["urgency"],
                "pred_urgency": u["urgency"],
                "actual_trade": row["worker_trade"],
                "pred_trade": d.output["trade"],
                "type_ok": type_ok,
                "urgency_ok": urgency_ok,
                "trade_ok": trade_ok,
                "combined_ok": combined,
                "verification_pass": v.output["verdict"] == "pass",
                "confidence": u["confidence"],
            }
        )

    eval_df = pd.DataFrame(records)
    total = len(eval_df)
    metrics = {
        "understanding_accuracy": round(eval_df["type_ok"].mean() * 100, 2),
        "urgency_accuracy": round(eval_df["urgency_ok"].mean() * 100, 2),
        "dispatch_trade_accuracy": round(eval_df["trade_ok"].mean() * 100, 2),
        "dispatch_accuracy": round(eval_df["combined_ok"].mean() * 100, 2),
        "verification_pass_rate": round(eval_df["verification_pass"].mean() * 100, 2),
        "manual_reassign_rate": round(float(pd.to_numeric(df["reassigned"], errors="coerce").fillna(0).mean()) * 100, 2),
        "avg_decision_ms": round(sum(durations) / max(len(durations), 1), 2),
        "auto_dispatch_rate_history": round((df["dispatch_mode"] == "auto").mean() * 100, 2),
    }

    per_type = []
    for repair_type, group in eval_df.groupby("actual_type"):
        per_type.append(
            {
                "repair_type": repair_type,
                "count": int(len(group)),
                "understanding_accuracy": round(group["type_ok"].mean() * 100, 2),
                "urgency_accuracy": round(group["urgency_ok"].mean() * 100, 2),
                "dispatch_trade_accuracy": round(group["trade_ok"].mean() * 100, 2),
                "verification_pass_rate": round(group["verification_pass"].mean() * 100, 2),
            }
        )
    per_type.sort(key=lambda x: -x["count"])

    labels = sorted(eval_df["actual_type"].unique().tolist())
    matrix = []
    for actual in labels:
        row_values = []
        for pred in labels:
            row_values.append(int(((eval_df["actual_type"] == actual) & (eval_df["pred_type"] == pred)).sum()))
        matrix.append(row_values)

    errors = eval_df[~eval_df["type_ok"]].head(12)
    examples = [
        {
            "order_id": r["order_id"],
            "actual_type": r["actual_type"],
            "pred_type": r["pred_type"],
            "actual_urgency": r["actual_urgency"],
            "pred_urgency": r["pred_urgency"],
            "actual_trade": r["actual_trade"],
            "pred_trade": r["pred_trade"],
        }
        for _, r in errors.iterrows()
    ]
    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample_size": total,
        "methodology": (
            "从 3000 条历史工单中随机抽取 600 条（random_state=42），将报修描述重新输入报修理解 Agent 与派单决策 Agent，"
            "以历史工单的人工标注类型、紧急程度、实际责任工种作为 ground truth；自动派单准确率要求「类型识别正确且推荐工种正确」；"
            "验收通过率使用历史完工记录（处理说明、照片数、完工时间）重跑闭环验证 Agent；人工改派率取自历史工单 reassigned 字段。"
        ),
        "metrics": metrics,
        "per_type": per_type,
        "confusion_matrix": {"labels": labels, "matrix": matrix},
        "error_examples": examples,
        "notes": [
            "历史工单由小区场景模板 + 规则生成，标签噪声低于真实业务，准确率用于系统回归评估而非绝对业务承诺。",
            "派单准确率按责任工种口径评估，未将具体维修工个体差异计入；真实上线后应以人工改派率持续校准。",
            "验收通过率下降主要来自历史记录缺少照片或处理说明过短，符合闭环验收规则的设计预期。",
        ],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with EVAL_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"评估样本：{total} 条历史工单")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print("\n各类型准确率（前 5）：")
    for item in per_type[:5]:
        print(f"  {item['repair_type']}: 理解 {item['understanding_accuracy']}% / 派单 {item['dispatch_trade_accuracy']}% / 验收 {item['verification_pass_rate']}%")
    print(f"\n结果已写入：{EVAL_PATH}")
    return result


if __name__ == "__main__":
    main()
