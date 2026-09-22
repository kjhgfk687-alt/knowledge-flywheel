"""load_context：装载短期记忆 + 长期画像摘要 + 情绪关键词快筛。

异常约定：记忆库不可用 → 空记忆继续对话（对话可用性优先于记忆完整性），ERROR 日志。
"""

import logging
from typing import Any

from app.config import Settings
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def make_load_context(settings: Settings, store: MemoryStore):
    # 每轮开始先复位的流水字段：checkpointer 会跨轮保留 State，上一轮的
    # 检索/风控/转人工结果若不复位，会渗入本轮响应（实测：订单查询响应带出
    # 上一轮的 risk_outcome=approval）。durable 字段（会话标识/记忆标记）除外。
    _TURN_RESET: dict[str, Any] = {
        "intent": None, "intent_confidence": 1.0,
        "emotion_level": "normal", "emotion_keyword_hit": False,
        "retrieval_status": None, "evidences": [], "query_id": None,
        "retrieval_degraded": False, "degrade_reason": None,
        "draft_answer": "", "citations": [], "citation_missing": False,
        "regenerated": False, "generation_failed": False,
        "confidence_level": "confident", "confidence_signals": [],
        "tool_failed": False, "awaiting_slot": False,
        "order_id": None, "order_data": {},
        "risk_outcome": None, "risk_level": None, "risk_factors": [],
        "approval_request_id": None,
        "need_human": False, "transfer_reason": None, "transfer_record_id": None,
        "unverified": False, "reply_text": None,
    }

    async def load_context(state: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {**_TURN_RESET}

        # 长期画像：只在首次注入（long_term_injected 标记防图恢复时重复注入——决策点1补充）
        if not state.get("long_term_injected"):
            try:
                updates["user_profile_summary"] = store.load_profile_summary(state["user_id"])
            except Exception:
                logger.error("长期画像加载失败 user_id=%s，空画像继续", state.get("user_id"), exc_info=True)
                updates["user_profile_summary"] = ""
            updates["long_term_injected"] = True

        # 短期记忆：每轮都从库里取最近 N 条（checkpointer 只保图状态，DB 是对话事实源）
        try:
            updates["history"] = store.load_recent_messages(state["session_id"], settings.history_window)
        except Exception:
            logger.error("短期记忆加载失败 session_id=%s，回退到图内历史", state.get("session_id"), exc_info=True)
            updates["history"] = state.get("history", [])

        # 情绪关键词快筛：纯内存零成本，命中即置位（与 route_intent 的模型判断取严）
        hit = any(w in state.get("user_input", "") for w in settings.emotion_keywords)
        updates["emotion_keyword_hit"] = hit
        return updates

    return load_context
