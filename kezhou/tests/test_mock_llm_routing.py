"""MockLLM 路由回归：关键词判定只看当前问题，不被画像/历史污染。

2026-09-22 实测 bug：画像含"退款"主题 + 近期对话含"政策"，导致该用户所有消息
全被路由成 refund_request 进风控审批。路由依据必须只取"当前问题："之后的内容。
"""

import json

from app.llm import MockLLM


def _prompt(question: str, profile: str = "（无画像）", history: str = "") -> str:
    return f"用户画像：{profile}\n近期对话：\n{history}\n当前问题：{question}"


async def test_routing_ignores_refund_word_in_profile():
    """画像里有"退款"主题，当前问题是订单查询 → 必须路由 order_query。"""
    prompt = _prompt(
        "帮我查下订单ORD1001到哪了",
        profile="[我要退款，这包怀疑是假货]",
    )
    data = json.loads(await MockLLM().complete("INTENT_ROUTER", prompt))
    assert data["intent"] == "order_query"


async def test_routing_ignores_policy_word_in_history():
    """近期对话回复里有"政策"字样，当前问题是退款请求 → 必须路由 refund_request。"""
    prompt = _prompt(
        "我要退款，订单ORD1004",
        history="user: 你们家卖不卖冲锋衣？\nassistant: 根据知识库证据，建议按售后政策引导客户处理 [1]。",
    )
    data = json.loads(await MockLLM().complete("INTENT_ROUTER", prompt))
    assert data["intent"] == "refund_request"


async def test_routing_emotion_only_from_current_question():
    """历史里带情绪词，当前问题心平气和 → emotion 必须是 normal。"""
    prompt = _prompt(
        "帮我查下订单ORD1001到哪了",
        history="user: 东西是垃圾！我要投诉你们！\nassistant: 已为您转接人工客服。",
    )
    data = json.loads(await MockLLM().complete("INTENT_ROUTER", prompt))
    assert data["emotion"] == "normal"
