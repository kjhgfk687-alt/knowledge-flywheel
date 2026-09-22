"""扩展分支端到端：降级 / 闲聊 / 情绪 / 组合原因 / 引用缺失 / LLM 故障。"""

from app.config import Settings
from app.graph.builder import build_graph
from app.llm import MockLLM
from app.memory.store import MemoryStore
from app.notify import LogNotifier
from app.retrieval.mock import MockRetrievalClient
from langgraph.checkpoint.memory import MemorySaver

from tests.conftest import make_settings, run_turn


async def test_degraded_lowers_confidence_but_still_answers(graph_factory):
    graph, _ = graph_factory("degraded")
    state = await run_turn(graph, "客户说买的包五金掉色怀疑是假货怎么处理？")

    assert state["retrieval_degraded"] is True
    assert state["degrade_reason"] == "vector_store_unavailable"
    assert "retrieval_degraded" in state["confidence_signals"]
    # 扣 0.2 后仍高于阈值：有答案，但信号已记录（阶段二可调阈值观察更多转人工）
    assert state["confidence_level"] == "confident"
    assert state["need_human"] is False


async def test_chitchat_polite_refusal(graph_factory):
    graph, _ = graph_factory("ok")
    state = await run_turn(graph, "你好，你是谁呀")

    assert state["intent"] == "chitchat"
    assert "客舟智能客服" in state["reply_text"]
    assert state["need_human"] is False
    # 闲聊不触发检索（每轮入口会复位流水字段，检索未执行则为 None）
    assert state.get("retrieval_status") is None
    assert state.get("query_id") is None


async def test_emotion_high_forces_transfer_before_retrieval(graph_factory):
    graph, store = graph_factory("ok")
    state = await run_turn(graph, "东西是垃圾！我要投诉你们！")

    assert state["emotion_level"] == "high"
    assert state["need_human"] is True
    assert state["transfer_reason"] == "emotion_high"
    assert state.get("query_id") is None  # 检索从未执行
    assert store.load_handoffs("s_test")[0]["transfer_reason"] == "emotion_high"


async def test_low_intent_plus_empty_combined_reason(graph_factory):
    """决策点2补充：意图低置信 + 检索为空 → 组合原因，飞轮分析可区分归因。"""
    graph, _ = graph_factory("empty")
    state = await run_turn(graph, "〔低置信〕这是什么情况")

    assert state["intent_confidence"] < 0.6
    assert state["need_human"] is True
    assert state["transfer_reason"] == "intent_low_confidence+retrieval_empty"


async def test_citation_missing_marks_unverified_and_transfers(graph_factory):
    """决策点3：引用缺失 → 强制重生成一次 → 仍缺 → citation_missing 标记 + 降置信转人工。"""
    graph, store = graph_factory("ok")
    state = await run_turn(graph, "〔无引用〕五金掉色怎么处理")

    assert state["citation_missing"] is True
    assert state["regenerated"] is True
    assert state["unverified"] is True
    assert state["confidence_level"] == "low"
    assert state["need_human"] is True
    assert state["transfer_reason"] == "low_confidence"
    # 草稿答案随转人工记录留给人工参考
    assert store.load_handoffs("s_test")[0]["draft_answer"]


class _GenerateFailsLLM(MockLLM):
    async def complete(self, system: str, user: str) -> str:
        if "RAG_GENERATE" in system:
            raise RuntimeError("llm down")
        return await super().complete(system, user)


async def test_llm_down_transfers_with_llm_error(tmp_path):
    settings = make_settings(tmp_path, "ok")
    store = MemoryStore(settings.db_path)
    graph = build_graph(
        settings=settings,
        llm=_GenerateFailsLLM(),
        client=MockRetrievalClient("ok"),
        store=store,
        notifier=LogNotifier(),
    ).compile(checkpointer=MemorySaver())

    state = await run_turn(graph, "客户说买的包五金掉色怀疑是假货怎么处理？")

    assert state["generation_failed"] is True
    assert state["need_human"] is True
    assert state["transfer_reason"] == "llm_error"
    assert state["reply_text"] == "系统暂时繁忙，已为您转接人工客服，请稍候。"
