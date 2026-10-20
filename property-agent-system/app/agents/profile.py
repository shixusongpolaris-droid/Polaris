"""住户画像 Agent：把基础档案、标签、健康/行动能力与出入行为组合成可读画像。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import AgentDecision, BaseAgent

CARE_LEVELS = ["常规", "日常关注", "重点关注", "特殊关怀"]


def parse_profile(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _tags(resident: Dict[str, Any]) -> List[str]:
    tags = resident.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = []
    return tags if isinstance(tags, list) else []


class ResidentProfileAgent(BaseAgent):
    code = "resident_profile"
    name = "住户画像 Agent"
    action = "build_resident_profile"

    def build(self, resident: Dict[str, Any], risk_level: Optional[str] = None,
              inactive_days: Optional[float] = None) -> Dict[str, Any]:
        tags = _tags(resident)
        profile = parse_profile(resident.get("profile_json"))
        age = int(resident.get("age") or 60)
        living = profile.get("living_status") or ("独居" if "独居老人" in tags else "与家人同住")
        health = profile.get("health_status") or ("慢病管理" if "慢病" in tags else "健康")
        mobility = profile.get("mobility") or ("行动不便" if "行动不便" in tags else "行动自如")
        family = profile.get("family_members") or resident.get("family_contact") or "未登记"
        service_needs = profile.get("service_needs") or []
        if isinstance(service_needs, str):
            service_needs = [service_needs] if service_needs else []
        emergency = resident.get("emergency_contact") or ""
        inactive = float(inactive_days if inactive_days is not None else resident.get("inactive_days") or 0)
        risk = risk_level or resident.get("risk_level") or "normal"

        chronic_words = ("高血压", "糖尿病", "心脏病", "冠心病", "慢病", "认知障碍", "脑梗", "中风")
        has_chronic = "慢病" in tags or any(word in health for word in chronic_words)
        persona: List[str] = []
        if "独居老人" in tags or living == "独居":
            persona.append("独居长者")
        if "高龄" in tags or age >= 80:
            persona.append("高龄老人")
        if has_chronic:
            persona.append("慢病照护")
        if "行动不便" in tags or mobility != "行动自如":
            persona.append("行动不便")
        if living == "空巢":
            persona.append("空巢老人")
        if not persona and living == "与家人同住":
            persona.append("家庭支持")
        if not persona:
            persona.append("普通住户")

        stored_level = (resident.get("care_level") or "").strip()
        if stored_level in CARE_LEVELS:
            care_level = stored_level
        elif risk in ("critical", "warning") or ("独居长者" in persona and ("高龄老人" in persona or "行动不便" in persona or "慢病照护" in persona)):
            care_level = "特殊关怀"
        elif risk == "watch" or any(x in persona for x in ("慢病照护", "行动不便")):
            care_level = "重点关注"
        elif age >= 70 or any(x in persona for x in ("独居长者", "高龄老人", "慢病照护", "行动不便", "空巢老人")):
            care_level = "日常关注"
        else:
            care_level = "常规"

        risk_factors = []
        if "独居长者" in persona:
            risk_factors.append("独居，缺少日常照应")
        if "高龄老人" in persona:
            risk_factors.append("高龄，行动与应急能力下降")
        if "慢病照护" in persona:
            risk_factors.append("慢病管理，需要用药/复诊提醒")
        if "行动不便" in persona:
            risk_factors.append("行动不便，外出与就医需协助")
        if not emergency:
            risk_factors.append("未登记紧急联系人")
        if family in ("", "未登记"):
            risk_factors.append("家属信息缺失")
        if inactive >= 3:
            risk_factors.append(f"已连续 {inactive:.1f} 天未出门")

        if care_level == "特殊关怀":
            actions = ["每日电话问候", "每周至少一次上门探访", "联系家属确认近况", "检查居家安全隐患"]
        elif care_level == "重点关注":
            actions = ["每周电话问候", "每月上门探访", "提醒慢病用药与复诊"]
        elif care_level == "日常关注":
            actions = ["每月电话问候", "节日前重点关怀"]
        else:
            actions = ["按常规网格服务跟进"]

        filled = [
            bool(resident.get("name")), bool(resident.get("age")), bool(resident.get("phone")),
            bool(tags), bool(profile.get("living_status")), bool(profile.get("health_status")),
            bool(profile.get("mobility")), bool(emergency), family not in ("", "未登记"),
            bool(service_needs),
        ]
        completeness = round(sum(1 for x in filled if x) / len(filled) * 100)
        summary = (
            f"{resident.get('name', '住户')}，{age} 岁，{living}，{health}，{mobility}；"
            f"当前{inactive:.1f}天未出门，关怀等级为「{care_level}」。"
        )
        return {
            "persona": persona,
            "persona_label": " / ".join(persona),
            "care_level": care_level,
            "summary": summary,
            "risk_factors": risk_factors or ["暂无显著风险标签"],
            "recommended_actions": actions,
            "completeness": completeness,
            "living_status": living,
            "health_status": health,
            "mobility": mobility,
            "family_members": family,
            "service_needs": service_needs,
            "habits": profile.get("habits") or "",
            "notes": profile.get("notes") or "",
            "emergency_contact": emergency,
            "updated_at": resident.get("profile_updated_at") or resident.get("updated_at"),
        }

    def run(self, resident: Dict[str, Any], risk_level: Optional[str] = None,
            inactive_days: Optional[float] = None) -> AgentDecision:
        output = self.build(resident, risk_level, inactive_days)
        return self.make_decision(
            output,
            f"根据 {len(output['persona'])} 个画像标签、健康/行动能力与出入行为，生成「{output['care_level']}」关怀画像。",
            confidence=0.92,
            input_summary=f"{resident.get('name')} / {resident.get('building')}{resident.get('unit')}{resident.get('room')}",
        )
