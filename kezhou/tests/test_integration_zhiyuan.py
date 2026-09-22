"""真实联调测试：客舟 → 知源(localhost:8100) → /retrieve。

知源服务未运行时整体跳过（`python -m uvicorn app.main:app --port 8100`，见 knowsource/backend）。
数据依赖：knowsource/backend/scripts/demo_seed.py 已灌入 t_bagshop_001 + 平台层文档。
"""

import httpx
import pytest

from app.config import Settings
from app.retrieval.client import ZhiyuanRetrievalClient
from app.retrieval.types import RetrievalStatus
from app.runtime import Runtime

BASE = "http://localhost:8100"
TENANT = "t_bagshop_001"
QUERY = "客户说买的包五金掉色怀疑是假货，怎么处理？"


def _zhiyuan_up() -> bool:
    try:
        return httpx.get(f"{BASE}/healthz", timeout=1.5).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _zhiyuan_up(), reason="知源服务未运行（localhost:8100），跳过真实联调")


def _settings(**kw) -> Settings:
    return Settings(zhiyuan_base_url=BASE, **kw)


async def test_real_retrieve_ok_path():
    async with ZhiyuanRetrievalClient(_settings()) as client:
        result = await client.retrieve(TENANT, QUERY, top_k=5)

    assert result.status is RetrievalStatus.OK, f"error={result.error_code} msg={result.error_message}"
    assert result.query_id.startswith("ret_")
    assert result.evidence, "种子数据里必有假货争议相关文档，不应为空"
    # 契约 v1.3 排序：score 仅层内降序；数组顺序 = 案例 → 商户 → 平台（平台兜底末位）
    seq = [e.source_type for e in result.evidence]
    if "platform" in seq:
        assert set(seq[seq.index("platform"):]) <= {"platform"}, f"平台层证据后不得再出现其他层: {seq}"
    for st in ("case", "merchant", "platform"):
        layer_scores = [e.score for e in result.evidence if e.source_type == st]
        assert layer_scores == sorted(layer_scores, reverse=True), f"{st} 层内 score 应降序: {layer_scores}"
    types = {e.source_type for e in result.evidence}
    assert types <= {"platform", "merchant", "case"}


async def test_real_retrieve_unknown_tenant():
    async with ZhiyuanRetrievalClient(_settings()) as client:
        result = await client.retrieve("t_no_such_tenant", QUERY)

    assert result.status is RetrievalStatus.ERROR
    assert result.error_code == "TENANT_NOT_FOUND"
    assert result.severity == "error"


async def test_real_retrieve_overlong_query_maps_422():
    """知源须把 FastAPI 默认 422 改写为契约信封（契约第 4 节实现注记），客户端按表解析。"""
    async with ZhiyuanRetrievalClient(_settings()) as client:
        result = await client.retrieve(TENANT, "长" * 600)

    assert result.status is RetrievalStatus.ERROR
    assert result.error_code == "QUERY_INVALID"


async def test_real_retrieve_wrong_api_key_is_critical():
    async with ZhiyuanRetrievalClient(_settings(zhiyuan_api_key="wrong-key")) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.status is RetrievalStatus.ERROR
    assert result.error_code == "AUTH_KEY_INVALID"
    assert result.severity == "critical"


async def test_full_graph_with_real_zhiyuan(tmp_path):
    """阶段二验收：用户提问 → 客舟全图 → 真实知源检索 → 带引用回答。"""
    settings = _settings(
        db_path=str(tmp_path / "it.sqlite"),
        retrieve_provider="zhiyuan",
        llm_provider="mock",
    )
    rt = Runtime(settings)
    await rt.start()
    try:
        state = await rt.chat("s_it_001", "u1", TENANT, QUERY)
    finally:
        await rt.stop()

    assert state["retrieval_status"] == "ok"
    assert state["query_id"].startswith("ret_")
    assert state["retrieval_degraded"] in (True, False)  # 知源侧可能走降级，两种都合法
    assert state["citations"], "有证据就应有引用"
    for c in state["citations"]:
        assert c["source_type"] in ("platform", "merchant", "case")
    assert state["need_human"] is False
    assert "[1]" in state["reply_text"]


async def test_full_flywheel_writeback_loop():
    """飞轮闭环（契约 v1.3）：检索 → 转人工审核落定 → 回写 pending → 知源人工确认
    → 案例优先命中 + hit_count 自增。跑在真实知源服务上。
    问题文本带唯一工单号：用例可重复运行，历史运行留下的同主题案例不会干扰断言。"""
    from uuid import uuid4

    from app.retrieval.case_client import ZhiyuanCaseWritebackClient

    ticket = f"LQ{uuid4().hex[:6].upper()}"
    question = f"客户说买的腰带扣掉漆了，要求赔偿，怎么处理？（工单{ticket}）"
    review = f"掉漆属正常磨损，引导走质保免费修复，赔偿诉求驳回。（联调 E2E 案例 {ticket}）"

    # 1) 用户问题触发检索，拿到 query_id（链路关联键）
    async with ZhiyuanRetrievalClient(_settings()) as client:
        r1 = await client.retrieve(TENANT, question, top_k=5)
    assert r1.status is RetrievalStatus.OK, f"error={r1.error_code} msg={r1.error_message}"
    assert r1.query_id

    # 2) 人工审核落定 → 客舟回写知源（真实 REST，契约 §7）
    async with ZhiyuanCaseWritebackClient(_settings()) as cw:
        wb = await cw.write_back(
            tenant_id=TENANT,
            original_query=question,
            review_result=review,
            query_id=r1.query_id,
            category="箱包",
        )
    assert wb.ok, f"error={wb.error_code} msg={wb.error_message}"
    assert wb.case_id and wb.case_id.startswith("case_")

    # 3) pending 阶段不可被检索命中（半自动：入库前必经人工确认）
    async with ZhiyuanRetrievalClient(_settings()) as client:
        r_pending = await client.retrieve(TENANT, question, top_k=5)
    assert all(e.metadata.get("case_id") != wb.case_id for e in r_pending.evidence)

    # 4) 知源控制台人工确认入库（dev 模式无控制台鉴权）
    confirm = httpx.post(f"{BASE}/api/v1/cases/{wb.case_id}/confirm", timeout=10)
    assert confirm.status_code == 200
    assert confirm.json()["data"]["status"] == "confirmed"

    # 5) 再次检索同类问题：案例排第一（飞轮生效）且 hit_count = 1
    async with ZhiyuanRetrievalClient(_settings()) as client:
        r3 = await client.retrieve(TENANT, question, top_k=5)
    assert r3.status is RetrievalStatus.OK
    case_hits = [e for e in r3.evidence if e.source_type == "case" and e.metadata.get("case_id") == wb.case_id]
    assert case_hits, "确认后的案例必须可被检索命中"
    assert r3.evidence[0].source_type == "case", "案例层必须排在最前（契约 v1.3 §3.5）"
    assert r3.evidence[0].metadata.get("hit_count") == 1
    assert "联调 E2E 案例" in r3.evidence[0].content
    assert ticket in r3.evidence[0].content  # 命中的正是本次回写的案例
