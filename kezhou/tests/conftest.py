"""测试公共设施：MockLLM + MockRetrievalClient + 临时库，图级端到端。"""

import pytest

from app.config import Settings
from app.graph.builder import build_graph
from app.llm import MockLLM
from app.memory.store import MemoryStore
from app.notify import LogNotifier
from app.retrieval.mock import MockRetrievalClient

TENANT = "t_bagshop_001"


def make_settings(tmp_path, scenario: str = "ok") -> Settings:
    return Settings(
        db_path=str(tmp_path / "db" / "test.sqlite"),
        retrieve_mock_scenario=scenario,
        llm_provider="mock",
    )


def make_graph(settings: Settings):
    """图级测试用 MemorySaver（不测持久化），业务表用临时 SQLite。"""
    from langgraph.checkpoint.memory import MemorySaver

    store = MemoryStore(settings.db_path)
    compiled = build_graph(
        settings=settings,
        llm=MockLLM(),
        client=MockRetrievalClient(settings.retrieve_mock_scenario),
        store=store,
        notifier=LogNotifier(),
    ).compile(checkpointer=MemorySaver())
    return compiled, store


async def run_turn(graph, message: str, session_id: str = "s_test", tenant_id: str = TENANT) -> dict:
    result = await graph.ainvoke(
        {"session_id": session_id, "user_id": "u1", "tenant_id": tenant_id, "user_input": message},
        config={"configurable": {"thread_id": session_id}},
    )
    return dict(result)


@pytest.fixture
def graph_factory(tmp_path):
    def _make(scenario: str = "ok"):
        return make_graph(make_settings(tmp_path, scenario))
    return _make
