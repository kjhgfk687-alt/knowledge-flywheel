"""审核/回写策略：哪些转人工 case 值得沉淀为知源案例知识（2026-09-22 决策④确认）。

只有"知识/回答质量"类原因产生的转人工是知识盲区信号，回写候选：
  retrieval_empty / low_confidence（含 intent_low_confidence+ 组合原因）。
基础设施类（retrieval_error/llm_error/tool_error）、情绪类（emotion_high）、
风控类（risk_hit）不是知识缺失，回写会污染案例库。
"""

WRITEBACK_CANDIDATE_BASES = frozenset({"retrieval_empty", "low_confidence"})


def is_writeback_candidate(transfer_reason: str | None) -> bool:
    if not transfer_reason:
        return False
    base = transfer_reason.split("+")[-1]  # 组合原因 "intent_low_confidence+retrieval_empty" 取末段
    return base in WRITEBACK_CANDIDATE_BASES
