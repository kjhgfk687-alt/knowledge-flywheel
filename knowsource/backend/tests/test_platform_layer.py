"""阶段二：平台层知识库 + 双层检索优先级排序（契约 v1.2 §3.5）。

排序策略：商户层证据在前（层内 score 降序），平台层证据作为兜底约束附后；
include_platform=true 且平台层有相关证据时保留最后 1 个席位（top_k≥2）；
total 不超过 top_k；降级路径同样双层。
"""
import pytest

from app.infra.embedder import get_embedder
from app.infra.models import Document
from app.schemas.retrieve import RetrieveRequest
from app.services import retrieval as retrieval_mod
from app.services.ingest import ingest_document
from tests.conftest import AUTH

MERCHANT_TEXT = "五金掉色处理规范：掉色属正常磨损，引导客户走免费电镀修复的质保流程，仅退款不予支持。"
PLATFORM_TEXT = "平台假货争议规范：假货争议须通过平台鉴定入口鉴定，平台鉴定结论为最终依据，各方均须执行。"


def _ingest(db, *, tenant_id, layer, title, content, category=""):
    doc = Document(
        tenant_id=tenant_id, layer=layer, title=title,
        file_name="t.md", file_type="md", category=category, status="uploaded",
    )
    db.add(doc)
    db.commit()
    ingest_document(db, doc, content.encode("utf-8"), get_embedder())
    return doc


@pytest.fixture
def layered_docs(db):
    _ingest(db, tenant_id="t_bagshop_001", layer="merchant", title="商户售后手册", content=MERCHANT_TEXT, category="箱包")
    _ingest(db, tenant_id="platform", layer="platform", title="平台假货争议规范", content=PLATFORM_TEXT)
    return db


def _retrieve(db, tenant_id="t_bagshop_001", query="假货争议怎么处理", **kw):
    # 案例优先排序有专属用例（test_cases.py）；本文件的主体是商户×平台两层关系
    kw.setdefault("include_case", False)
    return retrieval_mod.retrieve_documents(db, RetrieveRequest(tenant_id=tenant_id, query=query, **kw))


def test_platform_evidence_appended_after_merchant(layered_docs):
    data = _retrieve(layered_docs)
    assert data["retrieved"] is True
    types = [e["source_type"] for e in data["evidence"]]
    assert "merchant" in types and "platform" in types
    assert types.index("merchant") < types.index("platform"), "平台层必须附在商户层之后"


def test_include_platform_false_excludes_platform(layered_docs):
    data = _retrieve(layered_docs, include_platform=False)
    assert {e["source_type"] for e in data["evidence"]} == {"merchant"}


def test_platform_reserved_slot_respects_top_k(layered_docs):
    # top_k=2：商户 1 席 + 平台 1 席（兜底），总数不超 top_k
    data = _retrieve(layered_docs, top_k=2)
    types = [e["source_type"] for e in data["evidence"]]
    assert len(types) <= 2 and types[-1] == "platform"
    # top_k=1：无平台席位
    data1 = _retrieve(layered_docs, top_k=1)
    assert {e["source_type"] for e in data1["evidence"]} <= {"merchant"}


def test_platform_shared_across_tenants(layered_docs):
    for tenant in ("t_bagshop_001", "t_watchshop_002"):
        data = _retrieve(layered_docs, tenant_id=tenant)
        assert any(e["source_type"] == "platform" for e in data["evidence"]), tenant


def test_platform_evidence_metadata_shape(layered_docs):
    # 用 fixture 文档独有的措辞查询，保证它在平台层唯一席位上（避免被其他用例的平台文档挤出）
    data = _retrieve(layered_docs, query="平台鉴定入口的鉴定结论是最终依据吗")
    plat = next(e for e in data["evidence"] if e["source_type"] == "platform")
    assert plat["source_title"] == "平台假货争议规范"
    assert plat["metadata"]["doc_id"].startswith("doc_")
    assert {"doc_id", "chunk_index", "created_at"} <= set(plat["metadata"])


def test_reserved_tenant_rejected(layered_docs):
    from app.core.errors import ApiError

    with pytest.raises(ApiError) as ei:
        _retrieve(layered_docs, tenant_id="platform")
    assert ei.value.code == 40001


def test_platform_layer_in_degrade_path(layered_docs, monkeypatch):
    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    data = _retrieve(layered_docs)
    assert data["degraded"] is True
    types = [e["source_type"] for e in data["evidence"]]
    assert "platform" in types, "降级路径也必须带出平台层兜底证据"
    assert types.index("platform") == len(types) - 1


def test_platform_upload_via_endpoint(client):
    content = "平台物流规范：商家须在 48 小时内发货，超时未发货买家可申请赔付。".encode("utf-8")
    r = client.post(
        "/api/v1/documents/upload",
        data={"layer": "platform", "title": "平台发货时效规范"},
        files={"file": ("rule.md", content, "text/markdown")},
    )
    assert r.status_code == 200 and r.json()["code"] == 0
    assert r.json()["data"]["layer"] == "platform"

    # 平台层列表可见，且不依附任何租户
    r2 = client.get("/api/v1/documents", params={"layer": "platform"})
    titles = {d["title"] for d in r2.json()["data"]}
    assert "平台发货时效规范" in titles
