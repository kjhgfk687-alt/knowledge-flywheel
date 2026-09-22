"""降级路径与租户隔离测试（对应设计 M8 / 契约 §5）。

故障注入：monkeypatch 向量层组件抛异常 / 模拟超时，验证：
- 单路失败 → degraded=true + 关键词兜底结果；
- 双路失败 → 50301；
- 向量路径超预算 → 50401；
- 租户隔离与 min_score 语义。
"""
import time

import pytest

from app.core.errors import ApiError
from app.infra.embedder import get_embedder
from app.infra.models import Document
from app.schemas.retrieve import RetrieveRequest
from app.services import retrieval as retrieval_mod
from app.services.ingest import ingest_document

DOC_TEXT = "五金掉色处理规范：掉色属正常磨损，引导客户走半年内免费电镀修复的质保流程，仅退款不予支持。"


def _seed_doc(db, tenant_id="t_bagshop_001", title="降级测试文档"):
    doc = Document(
        tenant_id=tenant_id, layer="merchant", title=title,
        file_name="d.md", file_type="md", category="箱包", status="uploaded",
    )
    db.add(doc)
    db.commit()
    ingest_document(db, doc, DOC_TEXT.encode("utf-8"), get_embedder())
    return doc


def _raise(msg):
    def _f(*a, **kw):
        raise RuntimeError(msg)
    return _f


# ---- 降级：向量库故障 → FTS 兜底 ----

def test_degrade_on_vector_store_failure(db, monkeypatch):
    _seed_doc(db)
    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", _raise("chroma down"))

    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色怎么处理")
    data = retrieval_mod.retrieve_documents(db, req)

    assert data["degraded"] is True
    assert data["degrade_reason"] == "vector_store_unavailable"
    assert data["retrieved"] is True  # FTS 兜底命中
    assert any("五金" in e["content"] for e in data["evidence"])
    assert {"doc_id", "chunk_index", "created_at"} <= set(data["evidence"][0]["metadata"])


def test_degrade_on_embedding_failure(db, monkeypatch):
    _seed_doc(db)

    class BrokenEmbedder:
        def embed(self, texts):
            raise RuntimeError("embedding service down")

    monkeypatch.setattr(retrieval_mod, "get_embedder", lambda: BrokenEmbedder())
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色怎么处理")
    data = retrieval_mod.retrieve_documents(db, req)
    assert data["degraded"] is True and data["degrade_reason"] == "embedding_failed"
    assert data["retrieved"] is True


def test_degrade_on_vector_timeout(db, monkeypatch):
    _seed_doc(db)

    # 向量查询本身拖过内部预算（默认 800ms），嵌入正常执行 → 应归类 vector_timeout
    def slow_query(*a, **kw):
        time.sleep(2.0)
        return []

    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", slow_query)
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色怎么处理")
    data = retrieval_mod.retrieve_documents(db, req)
    assert data["degraded"] is True and data["degrade_reason"] == "vector_timeout"
    assert data["retrieved"] is True


# ---- 二级失败 ----

def test_both_paths_fail_returns_50301(db, monkeypatch):
    _seed_doc(db)
    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", _raise("down"))
    monkeypatch.setattr(retrieval_mod, "keyword_search", _raise("fts down"))

    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色")
    with pytest.raises(ApiError) as ei:
        retrieval_mod.retrieve_documents(db, req)
    assert ei.value.code == 50301 and ei.value.http_status == 503


def test_total_budget_exceeded_returns_50401(db, monkeypatch):
    _seed_doc(db)

    class SlowEmbedder:
        def embed(self, texts):
            time.sleep(0.3)
            raise RuntimeError("embedding too slow")

    tight_settings = retrieval_mod.get_settings().model_copy(
        update={"retrieval_total_budget_ms": 100}
    )
    monkeypatch.setattr(retrieval_mod, "get_settings", lambda: tight_settings)
    monkeypatch.setattr(retrieval_mod, "get_embedder", lambda: SlowEmbedder())

    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色")
    with pytest.raises(ApiError) as ei:
        retrieval_mod.retrieve_documents(db, req)
    assert ei.value.code == 50401 and ei.value.http_status == 504


# ---- 租户隔离 ----

def test_tenant_isolation_vector_and_fts(db):
    _seed_doc(db, tenant_id="t_bagshop_001", title="bagshop 专属文档")
    req = RetrieveRequest(tenant_id="t_watchshop_002", query="五金掉色怎么处理", top_k=20)

    # 向量路径：watchshop 检索不到 bagshop 的内容
    data = retrieval_mod.retrieve_documents(db, req)
    assert all("bagshop" not in e["source_title"] for e in data["evidence"])

    # FTS 路径同样隔离
    from app.infra.keyword import keyword_search
    hits = keyword_search(db, "t_watchshop_002", ["merchant"], "五金掉色", 20)
    assert all(h["metadata"]["tenant_id"] == "t_watchshop_002" for h in hits)


# ---- min_score 语义 ----

def test_min_score_filters_irrelevant(db, monkeypatch):
    _seed_doc(db)
    fake_hit = {
        "chunk_id": "chk_fake", "content": "完全无关内容", "score": 0.0,
        "metadata": {"tenant_id": "t_bagshop_001", "layer": "merchant", "document_id": 999,
                     "doc_title": "无关文档", "chunk_index": 0, "created_at": "now"},
    }
    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", lambda *a, **kw: [fake_hit])
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="月球能不能充电")
    data = retrieval_mod.retrieve_documents(db, req)
    assert data["retrieved"] is False and data["total"] == 0 and data["degraded"] is False
