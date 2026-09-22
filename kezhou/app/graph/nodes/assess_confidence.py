"""assess_confidence：纯规则置信度评估，不再调 LLM。

信号与扣分（阈值可配）：
- citation_missing   -0.40（引用缺失最重）
- retrieval_degraded -0.20（契约第 5 节：降级路径更倾向转人工）
- regenerated        -0.10
- 最高证据 score<0.5  -0.15
"""

import logging
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)


def make_assess_confidence(settings: Settings):
    def assess_confidence(state: dict[str, Any]) -> dict[str, Any]:
        evidences = state.get("evidences") or []
        score, signals = 1.0, []

        if state.get("citation_missing"):
            score -= 0.40
            signals.append("citation_missing")
        if state.get("retrieval_degraded"):
            score -= 0.20
            signals.append("retrieval_degraded")
        if state.get("regenerated"):
            score -= 0.10
            signals.append("regenerated")
        if evidences:
            top = max(e["score"] for e in evidences)
            if top < 0.5:
                score -= 0.15
                signals.append("weak_top_evidence")
        else:
            score, signals = 0.0, ["no_evidence"]

        level = "low" if score < settings.confidence_low_threshold else "confident"
        logger.info("confidence=%.2f level=%s signals=%s session=%s", score, level, signals, state.get("session_id"))
        return {"confidence_level": level, "confidence_signals": signals}

    return assess_confidence


def after_confidence(state: dict[str, Any]) -> str:
    return "respond" if state.get("confidence_level") == "confident" else "human_handoff"
