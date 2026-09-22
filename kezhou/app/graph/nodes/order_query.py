"""order_query（阶段三）：订单查询节点。

- 无订单号 → awaiting_slot，追问订单号，本轮结束等用户补充；
- 有订单号 → 只读查询：命中则答复订单信息；未命中（OrderNotFound）为业务回复，不转人工；
- 工具基础设施故障 → tool_failed=True → human_handoff(tool_error)。
结构性安全：图内不存在任何订单写操作工具。
"""

import logging
from typing import Any

from app.tools.order import OrderNotFound, OrderTool, extract_order_id

logger = logging.getLogger(__name__)

_ASK_ORDER_ID = "请提供您的订单号（例如 ORD1001），我来帮您查询。"
_NOT_FOUND = "未查询到订单 {order_id}，请核对订单号后重试；您也可以在订单详情页复制完整订单号。"
_ORDER_REPLY = (
    "订单 {order_id}：{item}（{category}），金额 ¥{amount:.2f}，"
    "鉴定状态：{auth_text}，当前状态：{status}。还有什么可以帮您？"
)
_AUTH_TEXT = {"authentic": "已鉴定（正品）", "unauthenticated": "未鉴定"}


def make_order_query(settings, order_tool: OrderTool):
    async def order_query(state: dict[str, Any]) -> dict[str, Any]:
        order_id = extract_order_id(state.get("user_input"), *(m.get("content") for m in state.get("history", [])[-3:]))
        if not order_id:
            logger.info("订单查询缺订单号，追问槽位 session=%s", state.get("session_id"))
            return {"awaiting_slot": True, "reply_text": _ASK_ORDER_ID}

        try:
            order = await order_tool.get(order_id)
        except OrderNotFound:
            return {"awaiting_slot": False, "reply_text": _NOT_FOUND.format(order_id=order_id)}
        except Exception as e:
            logger.error("订单工具故障 order_id=%s: %s", order_id, e)
            return {"tool_failed": True}

        return {
            "awaiting_slot": False,
            "order_id": order_id,
            "order_data": order,
            "reply_text": _ORDER_REPLY.format(
                order_id=order["order_id"], item=order["item"], category=order["category"],
                amount=order["amount"], auth_text=_AUTH_TEXT.get(order["auth_status"], "未知"),
                status=order["status"],
            ),
        }

    return order_query


def after_order_query(state: dict[str, Any]) -> str:
    return "human_handoff" if state.get("tool_failed") else "respond"
