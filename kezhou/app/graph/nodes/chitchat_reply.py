"""chitchat_reply：固定模板礼貌拒答 + 业务引导，零 LLM 零 IO，无失败路径。

情绪激烈的闲聊到不了这里（route_intent 的条件边先拦截送转人工）。
"""

from typing import Any

_CHITCHAT_TEMPLATE = (
    "我是客舟智能客服，主要为您解决商品咨询、订单查询和售后问题。"
    "请问有什么可以帮您？"
)


async def chitchat_reply(state: dict[str, Any]) -> dict[str, Any]:
    return {"reply_text": _CHITCHAT_TEMPLATE}
