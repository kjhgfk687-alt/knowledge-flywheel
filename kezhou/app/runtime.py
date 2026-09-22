"""运行时装配：图 + checkpointer（SqliteSaver）+ 记忆存储的生命周期管理。

决策点1：短期/图状态用 LangGraph AsyncSqliteSaver（thread_id=session_id），
长期记忆（画像/转人工记录）走自建表。
"""

import logging
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings, get_settings
from app.graph.builder import build_graph
from app.llm import get_llm
from app.memory.store import MemoryStore
from app.notify import WSNotifier
from app.retrieval.case_client import DryRunCaseWritebackClient, ZhiyuanCaseWritebackClient
from app.retrieval.client import ZhiyuanRetrievalClient
from app.retrieval.mock import MockRetrievalClient

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.store: MemoryStore | None = None
        self.graph = None
        self.notifier = WSNotifier()
        self.case_writeback = None
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        Path(self.settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(self.settings.db_path)
        llm = get_llm(
            provider=self.settings.llm_provider,
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            model=self.settings.llm_model,
        )
        # 检索客户端按 provider 注入：zhiyuan=真 REST（同协议，节点零改动）；mock=阶段一演示
        if self.settings.retrieve_provider == "zhiyuan":
            self.client = ZhiyuanRetrievalClient(self.settings)
        else:
            self.client = MockRetrievalClient(self.settings.retrieve_mock_scenario)
        # 案例回写客户端（飞轮沉淀端，契约 v1.3 §7）
        if self.settings.writeback_provider == "zhiyuan":
            self.case_writeback = ZhiyuanCaseWritebackClient(self.settings)
        else:
            self.case_writeback = DryRunCaseWritebackClient()

        # checkpointer 与业务库分文件（同文件多连接写锁竞争会间歇性停顿 ~11s，见 config 注释）
        if self.settings.checkpoint_db_path:
            ckpt_path = self.settings.checkpoint_db_path
        else:
            db = Path(self.settings.db_path)
            ckpt_path = str(db.with_name(f"{db.stem}_checkpoints{db.suffix}"))
        Path(ckpt_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(ckpt_path)
        saver = AsyncSqliteSaver(self._conn)
        await saver.setup()

        compiled = build_graph(
            settings=self.settings, llm=llm, client=self.client,
            store=self.store, notifier=self.notifier,
        )
        self.graph = compiled.compile(checkpointer=saver)
        logger.info(
            "客舟运行时就绪 db=%s llm=%s mock_scenario=%s",
            self.settings.db_path, self.settings.llm_provider, self.settings.retrieve_mock_scenario,
        )

    async def stop(self) -> None:
        aclose = getattr(self.client, "aclose", None)
        if aclose is not None:
            await aclose()
        aclose = getattr(self.case_writeback, "aclose", None)
        if aclose is not None:
            await aclose()
        if self._conn is not None:
            await self._conn.close()

    async def chat(self, session_id: str, user_id: str, tenant_id: str, message: str) -> dict:
        """执行一轮对话，返回图终态（respond/persist 已完成）。"""
        result = await self.graph.ainvoke(
            {"session_id": session_id, "user_id": user_id, "tenant_id": tenant_id, "user_input": message},
            config={"configurable": {"thread_id": session_id}},
        )
        return dict(result)
