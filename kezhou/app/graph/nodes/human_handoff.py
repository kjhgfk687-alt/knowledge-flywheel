"""human_handoff：所有异常与低置信路径的汇聚点。

职责：①派生 transfer_reason（含组合原因——决策点2补充）；②写 handoff_records
（阶段四回写知源的数据底座，query_id 串联飞轮链路）；③发通知；④按原因选模板回复。

异常约定：记录表写失败 → 仍返回兜底话术（用户必须拿到回复），CRITICAL 日志。
"""

import logging
import uuid
from typing import Any

from app.config import Settings
from app.memory.store import MemoryStore
from app.notify import Notifier

logger = logging.getLogger(__name__)

# 回复模板集中管理，便于运营调整文案；retrieval_error 为用户 2026-09-22 改定的最终文案
REPLY_TEMPLATES = {
    "retrieval_empty": "暂时没有找到与您问题相关的知识，已为您转接人工。您可以补充订单号或商品信息后重试。",
    "retrieval_error": "抱歉，我暂时无法查询知识库，已为您转人工。",
    "low_confidence": "您的问题需要人工客服为您进一步确认，正在为您转接。",
    "emotion_high": "非常理解您的心情，已为您优先转接人工客服，会尽快为您处理。",
    "llm_error": "系统暂时繁忙，已为您转接人工客服，请稍候。",
    "risk_hit": "该请求涉及需要人工核验的风险项，已为您转接人工客服处理。",
    "unknown": "抱歉，系统出现异常，已为您转接人工客服。",
}


def derive_transfer_reason(state: dict[str, Any], intent_threshold: float) -> str:
    """按优先级派生基础原因；意图低置信时与知识侧原因组合标注（决策点2补充）。"""
    if state.get("emotion_level") == "high":
        base = "emotion_high"
    elif state.get("generation_failed"):
        base = "llm_error"
    elif state.get("tool_failed"):
        base = "tool_error"
    elif state.get("retrieval_status") == "error":
        base = "retrieval_error"
    elif state.get("retrieval_status") == "empty":
        base = "retrieval_empty"
    elif state.get("risk_level") == "high":
        base = "risk_hit"
    elif state.get("confidence_level") == "low":
        base = "low_confidence"
    else:
        base = "unknown"

    intent_low = state.get("intent_confidence", 1.0) < intent_threshold
    # 只与"知识/回答质量"类原因组合：基础设施类（retrieval_error/llm_error/tool_error）不能归因给路由
    if intent_low and base in ("retrieval_empty", "low_confidence"):
        return f"intent_low_confidence+{base}"
    return base


def make_human_handoff(settings: Settings, store: MemoryStore, notifier: Notifier):
    async def human_handoff(state: dict[str, Any]) -> dict[str, Any]:
        reason = derive_transfer_reason(state, settings.intent_confidence_threshold)
        template = REPLY_TEMPLATES.get(reason.split("+")[-1], REPLY_TEMPLATES["unknown"])
        # 风控等上游节点可能已生成带引用的说明（如鉴定争议附鉴定标准），追加转人工话术而非覆盖
        base_reply = state.get("reply_text")
        reply = f"{base_reply}\n{template}" if base_reply else template

        record = {
            "id": f"ho_{uuid.uuid4().hex[:12]}",
            "session_id": state.get("session_id", ""),
            "user_id": state.get("user_id", ""),
            "tenant_id": state.get("tenant_id", ""),
            "query": state.get("user_input", ""),
            "query_id": state.get("query_id"),
            "transfer_reason": reason,
            "draft_answer": state.get("draft_answer"),
            "status": "pending",
        }
        try:
            rid = store.save_handoff(record)
            await notifier.notify_handoff(record)
        except Exception:
            logger.critical(
                "转人工记录写入失败（用户仍会收到兜底话术）session=%s reason=%s",
                state.get("session_id"), reason, exc_info=True,
            )
            rid = None

        logger.warning("TRANSFER reason=%s session=%s query_id=%s", reason, state.get("session_id"), state.get("query_id"))
        return {"need_human": True, "transfer_reason": reason, "transfer_record_id": rid, "reply_text": reply}

    return human_handoff
