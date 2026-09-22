"""create_approval（阶段四）：风控判"需审批"的高风险操作在此落审批队列。

结构性保证的最后一环：退款/改单在客舟图内没有任何执行路径，只能产生审批请求
（approvals 表，status=pending），由管理员经审核 API 给出结论（阶段五管理台消费）。
审批通过后是否执行真实动作，属于后续阶段与真实订单 API 的职责范围。
"""

import logging
import uuid
from typing import Any

from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_APPROVAL_REPLY = "已为您提交退货审批（审批单号 {approval_id}），审核通过后客服将与您确认办理，请保持通讯畅通。"


def make_create_approval(settings, store: MemoryStore):
    async def create_approval(state: dict[str, Any]) -> dict[str, Any]:
        order_data = state.get("order_data") or {}
        record = {
            "id": f"ap_{uuid.uuid4().hex[:12]}",
            "session_id": state.get("session_id", ""),
            "user_id": state.get("user_id", ""),
            "tenant_id": state.get("tenant_id", ""),
            "action_type": "refund",
            "order_id": state.get("order_id"),
            "amount": order_data.get("amount"),
            "payload": {
                "user_input": state.get("user_input", ""),
                "order_data": order_data,
                "query_id": state.get("query_id"),
                "draft_reply": state.get("reply_text"),
            },
            "risk_factors": state.get("risk_factors", []),
            "status": "pending",
        }
        try:
            rid = store.create_approval(record)
        except Exception:
            logger.critical(
                "审批请求写入失败（用户仍会收到受理话术）session=%s", state.get("session_id"), exc_info=True,
            )
            rid = None

        logger.warning(
            "APPROVAL_CREATED id=%s session=%s order=%s amount=%s factors=%s",
            rid, state.get("session_id"), record.get("order_id"), record.get("amount"), record.get("risk_factors"),
        )
        reply = _APPROVAL_REPLY.format(approval_id=rid or "待生成")
        return {"approval_request_id": rid, "reply_text": reply}

    return create_approval
