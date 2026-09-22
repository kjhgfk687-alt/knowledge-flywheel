"""阶段三：案例回写 → 人工确认 → 案例优先命中（飞轮闭环，契约 v1.3）。

覆盖：回写端点契约行为、半自动确认链路、案例→商户→平台终态排序、
include_case 开关、hit_count 自增、驳回、降级路径中的案例。
"""
from app.infra.embedder import get_embedder
from app.infra.models import Document
from app.schemas.retrieve import RetrieveRequest
from app.services import retrieval as retrieval_mod
from app.services.cases import confirm_and_index
from app.services.ingest import ingest_document
from tests.conftest import AUTH

WRITE_BACK = "/api/v1/cases"
MERCHANT_TEXT = "五金掉色处理规范：掉色属正常磨损，引导客户走免费电镀修复的质保流程，仅退款不予支持。"
# 平台层用物流域内容，避免与 test_platform_layer 的假货争议文本在哈希嵌入下撞分
PLATFORM_TEXT = "平台物流时效规范：商家须在买家付款后四十八小时内完成发货，超时未发货买家可申请延迟赔付。"


def _ingest_doc(db, tenant_id="t_bagshop_001", layer="merchant", title="商户文档", content=MERCHANT_TEXT):
    doc = Document(
        tenant_id=tenant_id, layer=layer, title=title,
        file_name="t.md", file_type="md", category="", status="uploaded",
    )
    db.add(doc)
    db.commit()
    ingest_document(db, doc, content.encode("utf-8"), get_embedder())
    return doc


def _write_back(client, **overrides):
    body = {
        "tenant_id": "t_bagshop_001",
        "original_query": "客户咬定五金掉色是假货，威胁投诉，怎么破？",
        "review_result": "鉴定正品，属正常磨损，走质保流程，仅退款驳回。",
        "query_id": "ret_abc12345",
        "category": "箱包",
    }
    body.update(overrides)
    return client.post(WRITE_BACK, headers=AUTH, json=body)


# ---- 回写端点（契约面） ----

def test_write_back_requires_auth(client):
    r = client.post(WRITE_BACK, json={"tenant_id": "t_bagshop_001", "original_query": "x", "review_result": "y"})
    assert r.status_code == 401 and r.json()["code"] == 40100


def test_write_back_creates_pending(client, db):
    r = _write_back(client)
    assert r.status_code == 200 and r.json()["code"] == 0
    data = r.json()["data"]
    assert data["case_id"].startswith("case_")
    assert data["status"] == "pending"
    assert data["query_id"] == "ret_abc12345"
    assert data["hit_count"] == 0
    # 尚未确认 → 不可检索
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色是假货怎么办")
    result = retrieval_mod.retrieve_documents(db, req)
    assert all(e["metadata"].get("case_id") != data["case_id"] for e in result["evidence"])


def test_write_back_validations(client):
    r = _write_back(client, tenant_id="t_missing_404")
    assert r.status_code == 404 and r.json()["code"] == 40401
    r = _write_back(client, original_query="   ")
    assert r.status_code == 422 and r.json()["code"] == 40002
    r = _write_back(client, query_id="bad-prefix")
    assert r.status_code == 422 and r.json()["code"] == 40004  # pydantic 字段校验 → filters/结构非法族


# ---- 半自动确认链路 ----

def test_confirm_makes_case_retrievable_with_top_priority(client, db):
    _ingest_doc(db, title="商户售后手册", content=MERCHANT_TEXT)
    _ingest_doc(db, tenant_id="platform", layer="platform", title="平台物流时效规范", content=PLATFORM_TEXT)

    case_id = _write_back(client).json()["data"]["case_id"]
    r = client.post(f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH)
    assert r.status_code == 200 and r.json()["data"]["status"] == "confirmed"

    data = retrieval_mod.retrieve_documents(
        db, RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色被咬定假货怎么处理", top_k=5)
    )
    types = [e["source_type"] for e in data["evidence"]]
    assert types[0] == "case", f"案例必须排第一，实际 {types}"
    assert "platform" not in types or types[-1] == "platform"

    # 案例元数据契约字段齐全
    case_ev = next(e for e in data["evidence"] if e["source_type"] == "case")
    for key in ("case_id", "original_query", "review_result", "hit_count", "feedback_source"):
        assert key in case_ev["metadata"], key
    assert case_ev["metadata"]["feedback_source"] == "kezhou"


def test_confirm_with_review_edit(client, db):
    case_id = _write_back(client).json()["data"]["case_id"]
    r = client.post(
        f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH,
        json={"review_result": "修订后的审核结论：引导走半年免费电镀。"},
    )
    assert r.status_code == 200
    data = retrieval_mod.retrieve_documents(
        db, RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色投诉怎么回复")
    )
    # 前序用例可能沉淀过同主题案例，这里断言"修订版"确实被索引且可命中
    case_contents = [e["content"] for e in data["evidence"] if e["source_type"] == "case"]
    assert any("修订后的审核结论" in c for c in case_contents), case_contents


def test_include_case_false_excludes_case(client, db):
    case_id = _write_back(client).json()["data"]["case_id"]
    client.post(f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH)
    data = retrieval_mod.retrieve_documents(
        db, RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色是假货怎么办", include_case=False)
    )
    assert all(e["source_type"] != "case" for e in data["evidence"])


def test_hit_count_increments_on_retrieval(client, db):
    case_id = _write_back(client).json()["data"]["case_id"]
    client.post(f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH)
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色是假货怎么处理")
    first = retrieval_mod.retrieve_documents(db, req)
    second = retrieval_mod.retrieve_documents(db, req)
    h1 = next(e["metadata"]["hit_count"] for e in first["evidence"] if e["source_type"] == "case")
    h2 = next(e["metadata"]["hit_count"] for e in second["evidence"] if e["source_type"] == "case")
    assert h2 == h1 + 1


def test_dismiss_keeps_case_out_of_library(client, db):
    case_id = _write_back(client).json()["data"]["case_id"]
    r = client.post(f"{WRITE_BACK}/{case_id}/dismiss", headers=AUTH)
    assert r.status_code == 200 and r.json()["data"]["status"] == "dismissed"
    # 驳回后不可确认
    r2 = client.post(f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH)
    assert r2.status_code == 422
    data = retrieval_mod.retrieve_documents(
        db, RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色是假货怎么处理")
    )
    assert all(e["metadata"].get("case_id") != case_id for e in data["evidence"])


def test_case_in_degrade_path(client, db, monkeypatch):
    case_id = _write_back(client).json()["data"]["case_id"]
    client.post(f"{WRITE_BACK}/{case_id}/confirm", headers=AUTH)

    def boom(*a, **kw):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(retrieval_mod.get_vector_store(), "query", boom)
    data = retrieval_mod.retrieve_documents(
        db, RetrieveRequest(tenant_id="t_bagshop_001", query="五金掉色是假货怎么处理")
    )
    assert data["degraded"] is True
    assert "case" in [e["source_type"] for e in data["evidence"]], "FTS 降级也必须能命中案例"
    assert [e["source_type"] for e in data["evidence"]].index("case") == 0
