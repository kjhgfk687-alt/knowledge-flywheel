"""阶段三端到端：订单查询（缺槽/命中/未命中/工具故障）、三维风控四出口、意图分流、WS 坐席通知。"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.graph.builder import build_graph
from app.llm import MockLLM
from app.memory.store import MemoryStore
from app.notify import LogNotifier
from app.retrieval.mock import MockRetrievalClient
from app.tools.order import MockOrderTool
from langgraph.checkpoint.memory import MemorySaver

from tests.conftest import make_settings, run_turn


async def test_order_query_asks_for_order_id(graph_factory):
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "帮我查下订单到哪了")

    assert state["intent"] == "order_query"
    assert state["awaiting_slot"] is True
    assert "订单号" in state["reply_text"]
    assert state["need_human"] is False


async def test_order_query_found(graph_factory):
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "帮我查下订单ORD1001到哪了")

    assert state["awaiting_slot"] is False
    assert state["order_data"]["amount"] == 1280.0
    assert "ORD1001" in state["reply_text"]
    assert "已签收" in state["reply_text"]
    assert state["need_human"] is False


async def test_order_query_not_found_is_business_reply(graph_factory):
    """订单未命中是业务回复，不是基础设施故障，不转人工。"""
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "查下订单ORD9999")

    assert state["awaiting_slot"] is False
    assert "未查询到" in state["reply_text"]
    assert state["need_human"] is False
    assert state["tool_failed"] is False


class _FailingOrderTool(MockOrderTool):
    async def get(self, order_id: str) -> dict:
        raise ConnectionError("order service down")


async def test_order_tool_failure_transfers_with_tool_error(tmp_path):
    settings = make_settings(tmp_path, "ok")
    store = MemoryStore(settings.db_path)
    graph = build_graph(
        settings=settings, llm=MockLLM(), client=MockRetrievalClient("ok"),
        store=store, notifier=LogNotifier(), order_tool=_FailingOrderTool(),
    ).compile(checkpointer=MemorySaver())

    state = await run_turn(graph, "帮我查下订单ORD1001到哪了")
    assert state["need_human"] is True
    assert state["transfer_reason"] == "tool_error"
    assert state["tool_failed"] is True


async def test_dispute_refund_forces_human_with_standard_citations(graph_factory):
    """鉴定争议类一律转人工，并附知识库鉴定标准引用（[n] 可点溯源）。"""
    graph, store = graph_factory("ok")
    state = await run_turn(graph, "我要退款，这包怀疑是假货")

    assert state["intent"] == "refund_request"
    assert state["need_human"] is True
    assert state["transfer_reason"] == "risk_hit"
    assert state["risk_level"] == "high"
    assert state["citations"], "争议类必须附鉴定标准引用"
    assert "[1]" in state["reply_text"]
    assert "人工" in state["reply_text"]  # 转人工话术以追加形式拼在说明之后
    assert store.load_handoffs("s_test")[0]["transfer_reason"] == "risk_hit"


async def test_authentic_low_amount_goes_auto(graph_factory):
    """已鉴定 + 金额≤上限 + 无争议 → 自动流程（只读答复，无写操作）。"""
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "我要退款，订单ORD1003")  # ¥520 已鉴定

    assert state["need_human"] is False
    assert state["risk_level"] == "low"
    assert "自动退货" in state["reply_text"]
    assert " ORD1003" in state["reply_text"]


async def test_unauthenticated_high_value_forces_human(graph_factory):
    """未鉴定 + 高价值 → 强制转人工（无争议关键词，纯金额/鉴定维度命中）。"""
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "我要退款，订单ORD1002")  # ¥15800 未鉴定

    assert state["need_human"] is True
    assert state["transfer_reason"] == "risk_hit"
    assert state["risk_level"] == "high"
    assert any("未鉴定" in f for f in state["risk_factors"])


async def test_middle_case_creates_approval_request(graph_factory):
    """已鉴定但金额超自动上限 → 中间地带：落审批队列（阶段四），不直接执行。"""
    graph, store = graph_factory("ok")
    state = await run_turn(graph, "我要退款，订单ORD1004")  # ¥8800 已鉴定

    assert state["need_human"] is False
    assert state["risk_outcome"] == "approval"
    assert state["risk_level"] == "medium"
    assert state["approval_request_id"]
    assert "审批单号" in state["reply_text"]
    approvals = store.load_approvals(status="pending")
    assert len(approvals) == 1
    assert approvals[0]["order_id"] == "ORD1004"
    assert approvals[0]["amount"] == 8800.0


async def test_policy_question_stays_on_knowledge_path(graph_factory):
    """“退货政策是什么”是知识问询，必须走 RAG 而不是触发风控/订单链路。"""
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "退货政策是什么？")

    assert state["intent"] == "knowledge_qa"
    assert state["retrieval_status"] == "ok"
    assert state["risk_level"] is None
    assert state["order_data"] == {}


def test_agent_receives_handoff_over_websocket(tmp_path):
    """坐席端通过 /ws/agent 实时收到转人工通知。

    POST 放后台线程：starlette 测试传输层的 send 在对端未 receive 时会阻塞，
    若测试线程先等 POST 返回、图内 send 又在等 receive，会互等死锁——
    这也正是生产代码里"通知限时 0.5s 不阻塞对话"设计约束的由来。
    """
    import threading

    from app.main import create_app

    settings = Settings(
        db_path=str(tmp_path / "ws.sqlite"),
        retrieve_mock_scenario="empty",  # 检索空 → 转人工
        llm_provider="mock",
    )
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/agent") as ws:
            result = {}

            def do_post():
                try:
                    result["resp"] = client.post(
                        "/api/v1/chat",
                        json={"tenant_id": "t_bagshop_001", "user_id": "u1", "message": "这个商品到底是不是真皮的啊"},
                    )
                except Exception as e:  # 诊断用：跨线程调用失败时给出明确原因而非 KeyError
                    result["error"] = e

            poster = threading.Thread(target=do_post)
            poster.start()
            event = ws.receive_json()  # 通知发送有 0.5s 上限保护，不会互等死锁
            poster.join(timeout=10)

            assert "error" not in result, f"POST 线程异常: {result.get('error')!r}"
            assert result["resp"].status_code == 200
            assert result["resp"].json()["need_human"] is True
            assert event["type"] == "handoff"
            assert event["record"]["transfer_reason"] == "retrieval_empty"
            assert event["record"]["query_id"].startswith("ret_")
