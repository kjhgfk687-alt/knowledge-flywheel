"""主链路端到端：检索成功 / 检索为空 / 接口报错（阶段一核心三分支）。"""

from tests.conftest import run_turn


async def test_ok_path_generates_cited_answer(graph_factory):
    graph, store = graph_factory("ok")
    state = await run_turn(graph, "客户说买的包五金掉色怀疑是假货怎么处理？")

    assert state["retrieval_status"] == "ok"
    assert state["intent"] == "knowledge_qa"
    assert state["need_human"] is False
    assert state["confidence_level"] == "confident"
    assert state["unverified"] is False
    assert state["query_id"].startswith("ret_")
    # 引用可溯源：[n] 标注 + citations 数据源，case 类证据带 from_case
    assert "[1]" in state["reply_text"]
    assert len(state["citations"]) == 3
    assert state["citations"][0]["from_case"] is True
    assert state["citations"][0]["source_type"] == "case"
    # 短期记忆落库：本轮 user + assistant 各一条
    assert len(store.load_recent_messages("s_test", 10)) == 2


async def test_empty_retrieval_transfers_without_model_call(graph_factory):
    graph, store = graph_factory("empty")
    state = await run_turn(graph, "这个商品到底是不是真皮的啊")

    assert state["retrieval_status"] == "empty"
    assert state["need_human"] is True
    assert state["transfer_reason"] == "retrieval_empty"
    assert "没有找到" in state["reply_text"]
    assert state["evidences"] == []
    assert state["query_id"].startswith("ret_")  # 检索空也是合法响应，query_id 仍在
    # 转人工记录落库（阶段四回写知源的数据底座）
    records = store.load_handoffs("s_test")
    assert len(records) == 1
    assert records[0]["transfer_reason"] == "retrieval_empty"
    assert records[0]["query_id"].startswith("ret_")
    assert records[0]["status"] == "pending"


async def test_error_retrieval_fallback_wording(graph_factory):
    graph, _ = graph_factory("error")
    state = await run_turn(graph, "客户说买的包五金掉色怀疑是假货怎么处理？")

    assert state["retrieval_status"] == "error"
    assert state["need_human"] is True
    assert state["transfer_reason"] == "retrieval_error"
    # 用户 2026-09-22 改定的最终兜底文案
    assert state["reply_text"] == "抱歉，我暂时无法查询知识库，已为您转人工。"
