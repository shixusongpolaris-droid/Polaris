"""Agent 基础设施：统一决策结果结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AgentDecision:
    """一次 Agent 决策的完整记录，协调器负责落库。"""

    agent: str
    agent_name: str
    action: str
    output: Dict[str, Any]
    rationale: str
    confidence: float = 0.0
    input_summary: str = ""
    duration_ms: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "agent_name": self.agent_name,
            "action": self.action,
            "output": self.output,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
            "input_summary": self.input_summary,
            "duration_ms": round(self.duration_ms, 2),
        }


class BaseAgent:
    code = "base"
    name = "基础 Agent"
    action = "decide"

    def make_decision(
        self,
        output: Dict[str, Any],
        rationale: str,
        confidence: float = 0.9,
        input_summary: str = "",
    ) -> AgentDecision:
        return AgentDecision(
            agent=self.code,
            agent_name=self.name,
            action=self.action,
            output=output,
            rationale=rationale,
            confidence=confidence,
            input_summary=input_summary,
        )
