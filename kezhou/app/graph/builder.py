"""图装配：节点注册 + 条件边导航。阶段三/四的 order_query、risk_assess、create_approval
在此扩入；阶段一 order/refund 意图先走知识问答链路（检索是安全兜底），注释标记改造点。
"""

import logging

from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.graph.nodes import (
    assess_confidence,
    chitchat_reply,
    create_approval,
    generate,
    human_handoff,
    load_context,
    order_query,
    persist,
    respond,
    retrieve,
    risk_assess,
    route_intent,
)
from app.graph.state import KeZhouState
from app.llm import LLM
from app.memory.store import MemoryStore
from app.notify import Notifier
from app.retrieval.types import RetrievalClient
from app.tools.order import MockOrderTool, OrderTool

logger = logging.getLogger(__name__)


def after_route_intent(state: KeZhouState) -> str:
    if state.get("emotion_level") == "high":
        return "human_handoff"
    if state.get("intent") == "chitchat":
        return "chitchat_reply"
    if state.get("intent") == "order_query":
        return "order_query"
    if state.get("intent") == "refund_request":
        return "risk_assess"
    # 决策点2：knowledge_qa 与意图低置信都走 retrieve（检索是最安全的兜底路径）
    return "retrieve"


def build_graph(
    *,
    settings: Settings,
    llm: LLM,
    client: RetrievalClient,
    store: MemoryStore,
    notifier: Notifier,
    order_tool: OrderTool | None = None,
) -> StateGraph:
    order_tool = order_tool or MockOrderTool()

    g = StateGraph(KeZhouState)
    g.add_node("load_context", load_context.make_load_context(settings, store))
    g.add_node("route_intent", route_intent.make_route_intent(settings, llm))
    g.add_node("retrieve", retrieve.make_retrieve(settings, client))
    g.add_node("generate", generate.make_generate(settings, llm))
    g.add_node("assess_confidence", assess_confidence.make_assess_confidence(settings))
    g.add_node("human_handoff", human_handoff.make_human_handoff(settings, store, notifier))
    g.add_node("chitchat_reply", chitchat_reply.chitchat_reply)
    g.add_node("order_query", order_query.make_order_query(settings, order_tool))
    g.add_node("risk_assess", risk_assess.make_risk_assess(settings, order_tool, client))
    g.add_node("create_approval", create_approval.make_create_approval(settings, store))
    g.add_node("respond", respond.respond)
    g.add_node("persist", persist.make_persist(settings, store))

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "route_intent")
    g.add_conditional_edges("route_intent", after_route_intent, {
        "retrieve": "retrieve",
        "order_query": "order_query",
        "risk_assess": "risk_assess",
        "chitchat_reply": "chitchat_reply",
        "human_handoff": "human_handoff",
    })
    g.add_conditional_edges("retrieve", retrieve.after_retrieve, {
        "generate": "generate",
        "human_handoff": "human_handoff",
    })
    g.add_conditional_edges("generate", generate.after_generate, {
        "assess_confidence": "assess_confidence",
        "human_handoff": "human_handoff",
    })
    g.add_conditional_edges("assess_confidence", assess_confidence.after_confidence, {
        "respond": "respond",
        "human_handoff": "human_handoff",
    })
    g.add_conditional_edges("order_query", order_query.after_order_query, {
        "respond": "respond",
        "human_handoff": "human_handoff",
    })
    g.add_conditional_edges("risk_assess", risk_assess.after_risk_assess, {
        "respond": "respond",
        "create_approval": "create_approval",
        "human_handoff": "human_handoff",
    })
    g.add_edge("create_approval", "respond")
    g.add_edge("chitchat_reply", "respond")
    g.add_edge("human_handoff", "respond")
    g.add_edge("respond", "persist")
    g.add_edge("persist", END)
    return g
