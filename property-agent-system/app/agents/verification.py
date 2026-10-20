"""Agent 4：闭环验证 Agent。

根据工单类型与紧急程度检查处理记录、现场照片、时限与安全要求，
输出「验收通过 / 退回整改」以及逐项检查依据。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import AgentDecision, BaseAgent

PHOTO_REQUIREMENTS = {
    "电梯困人": 2,
    "燃气泄漏": 2,
    "水管爆裂": 2,
    "消防隐患": 1,
    "电梯故障": 1,
    "水管漏水": 1,
    "下水道堵塞": 1,
    "电路故障": 1,
    "门禁故障": 1,
    "公共照明": 1,
    "墙面地面破损": 1,
    "环境卫生": 1,
    "其他报修": 1,
}

TYPE_KEYWORDS = {
    "电梯困人": ["救援", "困人", "解救", "电梯", "轿厢", "门"],
    "电梯故障": ["电梯", "门", "控制", "更换", "调试", "恢复"],
    "水管爆裂": ["水", "阀", "管", "积水", "压力", "清理"],
    "水管漏水": ["水", "阀", "管", "漏", "更换", "密封"],
    "下水道堵塞": ["疏通", "下水", "管道", "排水", "清理", "堵塞"],
    "电路故障": ["电", "闸", "线", "更换", "恢复", "绝缘"],
    "燃气泄漏": ["燃气", "检漏", "阀", "浓度", "通风", "关闭"],
    "消防隐患": ["消防", "灭火器", "通道", "烟感", "复位", "更换"],
    "门禁故障": ["门", "锁", "禁", "更换", "调试", "恢复"],
    "公共照明": ["灯", "照明", "更换", "线路", "恢复"],
    "墙面地面破损": ["墙", "砖", "补", "修", "地面", "恢复"],
    "环境卫生": ["清理", "垃圾", "消杀", "清扫", "清运"],
    "其他报修": [],
}

SAFETY_HARD_TYPES = {"电梯困人", "燃气泄漏", "消防隐患"}


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class VerificationAgent(BaseAgent):
    code = "verification"
    name = "闭环验证 Agent"
    action = "verify_work_order"

    def evaluate(
        self,
        ticket: Dict[str, Any],
        result_note: str,
        photos_count: int,
        now: Optional[datetime] = None,
    ) -> AgentDecision:
        now = now or datetime.now()
        repair_type = ticket.get("repair_type") or "其他报修"
        note = (result_note or "").strip()
        photos = int(photos_count or 0)
        min_photos = PHOTO_REQUIREMENTS.get(repair_type, 1)
        checks: List[Dict[str, Any]] = []

        checks.append(
            {
                "code": "result_note",
                "label": "处理记录完整",
                "passed": len(note) >= 8,
                "hard": True,
                "detail": f"处理说明 {len(note)} 字，至少 8 字；内容：{note[:50] or '未填写'}",
            }
        )
        checks.append(
            {
                "code": "photos",
                "label": "现场照片齐全",
                "passed": photos >= min_photos,
                "hard": True,
                "detail": f"{repair_type}要求至少 {min_photos} 张，实际上传 {photos} 张",
            }
        )
        checks.append(
            {
                "code": "completed_at",
                "label": "完工时间已记录",
                "passed": bool(ticket.get("completed_at")),
                "hard": True,
                "detail": f"完工时间：{ticket.get('completed_at') or '未记录'}",
            }
        )

        keywords = TYPE_KEYWORDS.get(repair_type, [])
        if keywords:
            hit = [k for k in keywords if k in note]
            checks.append(
                {
                    "code": "content_relevance",
                    "label": "处理内容与报修匹配",
                    "passed": bool(hit),
                    "hard": True,
                    "detail": f"命中处理要素：{'、'.join(hit) if hit else '无'}",
                }
            )

        deadline = _parse(ticket.get("deadline"))
        completed_at = _parse(ticket.get("completed_at")) or now
        overdue_minutes = 0
        if deadline and completed_at > deadline:
            overdue_minutes = int((completed_at - deadline).total_seconds() // 60)
        hard_time = repair_type in SAFETY_HARD_TYPES
        checks.append(
            {
                "code": "time_limit",
                "label": "处理时限达标",
                "passed": overdue_minutes == 0,
                "hard": hard_time,
                "detail": (
                    "在时限内完成"
                    if overdue_minutes == 0
                    else f"超时 {overdue_minutes} 分钟"
                    + ("（安全类工单必须限时完成）" if hard_time else "（已升级主管，记入考核）")
                ),
            }
        )

        if repair_type == "燃气泄漏":
            safety_hit = [k for k in ["检漏", "浓度", "通风", "关闭"] if k in note]
            checks.append(
                {
                    "code": "safety_check",
                    "label": "燃气安全复检",
                    "passed": bool(safety_hit),
                    "hard": True,
                    "detail": f"安全复检要素：{'、'.join(safety_hit) if safety_hit else '未体现检漏/浓度/通风/关阀'}",
                }
            )
        if repair_type == "电梯困人":
            rescue_hit = [k for k in ["救援", "困人", "解救", "轿厢"] if k in note]
            checks.append(
                {
                    "code": "rescue_record",
                    "label": "困人救援记录",
                    "passed": bool(rescue_hit) and photos >= 2,
                    "hard": True,
                    "detail": f"救援记录：{'、'.join(rescue_hit) if rescue_hit else '未体现'}；照片 {photos} 张",
                }
            )

        failed = [c for c in checks if c["hard"] and not c["passed"]]
        warnings = [c for c in checks if not c["hard"] and not c["passed"]]
        passed = len(failed) == 0
        score = round(sum(1 for c in checks if c["passed"]) / max(len(checks), 1) * 100, 1)
        if passed:
            note_text = f"验收通过：{len(checks)} 项检查全部满足，工单闭环。"
            if warnings:
                note_text += f" 提示：{warnings[0]['detail']}。"
            required_actions: List[str] = []
        else:
            note_text = "验收不通过：" + "；".join(f"{c['label']}未满足（{c['detail']}）" for c in failed) + "。请整改后重新提交。"
            required_actions = [f"{c['label']}：{c['detail']}" for c in failed]

        output = {
            "verdict": "pass" if passed else "return",
            "score": score,
            "checks": checks,
            "failed_checks": [c["code"] for c in failed],
            "required_actions": required_actions,
            "note": note_text,
            "overdue_minutes": overdue_minutes,
            "photos_count": photos,
            "min_photos": min_photos,
            "safety_hard": hard_time,
        }
        rationale = (
            f"对「{repair_type}」执行 {len(checks)} 项验收检查，得分 {score}；"
            + ("全部硬性规则通过，准予闭环。" if passed else "存在未通过硬性规则：" + "、".join(c["label"] for c in failed))
        )
        return self.make_decision(
            output,
            rationale,
            confidence=0.96,
            input_summary=f"{ticket.get('id')} / {repair_type} / 照片{photos}张 / 超时{overdue_minutes}分钟",
        )
