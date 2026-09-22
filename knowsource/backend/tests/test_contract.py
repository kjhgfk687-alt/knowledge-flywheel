"""契约一致性测试：§4 错误码逐条覆盖 + §3 成功结构断言（对应设计 M8）。"""
import pytest

from app.infra.embedder import get_embedder
from app.infra.models import Document
from app.services.ingest import ingest_document
from tests.conftest import AUTH

RETRIEVE = "/api/v1/retrieve"
DOC = {"tenant_id": "t_bagshop_001", "query": "五金掉色", "top_k": 3}


# ---- 契约 §4：错误码 ----

def test_auth_key_missing(client):
    r = client.post(RETRIEVE, json=DOC)
    assert r.status_code == 401 and r.json()["code"] == 40100


def test_auth_key_invalid(client):
    r = client.post(RETRIEVE, headers={"X-API-Key": "nope"}, json=DOC)
    assert r.status_code == 401 and r.json()["code"] == 40101


def test_tenant_id_invalid(client):
    for bad in (None, "T_UPPER", "a", "x" * 40, "t with space"):
        body = {"query": "x"}
        if bad is not None:
            body["tenant_id"] = bad
        r = client.post(RETRIEVE, headers=AUTH, json=body)
        assert r.status_code == 422 and r.json()["code"] == 40001, f"tenant_id={bad!r}: {r.text}"


def test_query_invalid(client):
    for bad in (None, "", "   ", "长" * 600):
        body = {"tenant_id": "t_bagshop_001"}
        if bad is not None:
            body["query"] = bad
        r = client.post(RETRIEVE, headers=AUTH, json=body)
        assert r.status_code == 422 and r.json()["code"] == 40002, f"query={bad!r}: {r.text}"


def test_top_k_invalid(client):
    for bad in (0, -1, 21, "five"):
        r = client.post(RETRIEVE, headers=AUTH, json={"tenant_id": "t_bagshop_001", "query": "x", "top_k": bad})
        assert r.status_code == 422 and r.json()["code"] == 40003, f"top_k={bad!r}: {r.text}"


def test_filters_invalid(client):
    bodies = [
        {"tenant_id": "t_bagshop_001", "query": "x", "filters": {"unknown_key": 1}},
        {"tenant_id": "t_bagshop_001", "query": "x", "filters": {"category": []}},
        {"tenant_id": "t_bagshop_001", "query": "x", "filters": {"auth_status": ["not-a-status"]}},
    ]
    for body in bodies:
        r = client.post(RETRIEVE, headers=AUTH, json=body)
        assert r.status_code == 422 and r.json()["code"] == 40004, f"body={body}: {r.text}"


def test_body_not_json(client):
    r = client.post(RETRIEVE, headers={**AUTH, "Content-Type": "application/json"}, content=b"not-json{")
    assert r.status_code == 422 and r.json()["code"] == 40005


def test_tenant_not_found(client):
    r = client.post(RETRIEVE, headers=AUTH, json={"tenant_id": "t_missing_404", "query": "x"})
    assert r.status_code == 404 and r.json()["code"] == 40401


def test_tenant_disabled(client):
    r = client.post(RETRIEVE, headers=AUTH, json={"tenant_id": "t_disabled_9", "query": "x"})
    assert r.status_code == 403 and r.json()["code"] == 40301


# ---- 契约 §3：成功结构 ----

def test_success_envelope_and_evidence_shape(client, db):
    doc = Document(
        tenant_id="t_bagshop_001", layer="merchant", title="契约结构测试文档",
        file_name="c.md", file_type="md", category="箱包", status="uploaded",
    )
    db.add(doc)
    db.commit()
    ingest_document(
        db, doc,
        "五金掉色处理规范：掉色属正常磨损，引导客户走质保流程修复。".encode("utf-8"),
        get_embedder(),
    )

    r = client.post(RETRIEVE, headers=AUTH, json=DOC)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    for key in ("query_id", "tenant_id", "retrieved", "degraded", "degrade_reason", "total", "evidence", "latency_ms"):
        assert key in data, key
    assert data["query_id"].startswith("ret_")
    assert data["degraded"] is False and data["degrade_reason"] is None
    assert data["total"] == len(data["evidence"])
    for e in data["evidence"]:
        for key in ("chunk_id", "content", "source_type", "source_title", "score", "metadata"):
            assert key in e, key
        assert e["chunk_id"].startswith("chk_")
        assert e["source_type"] in ("platform", "merchant", "case")
        assert 0.0 <= e["score"] <= 1.0
        assert {"doc_id", "chunk_index", "created_at"} <= set(e["metadata"])
    scores = [e["score"] for e in data["evidence"]]
    # 契约 v1.2 排序：score 仅保证层内降序；商户层证据整体位于平台层之前
    for source_type in ("merchant", "platform", "case"):
        layer_scores = [e["score"] for e in data["evidence"] if e["source_type"] == source_type]
        assert layer_scores == sorted(layer_scores, reverse=True), source_type
    source_seq = [e["source_type"] for e in data["evidence"]]
    assert "platform" not in source_seq or set(source_seq[source_seq.index("platform") :]) <= {"platform"}


def test_empty_result_is_explicit(client):
    r = client.post(RETRIEVE, headers=AUTH, json={"tenant_id": "t_watchshop_002", "query": "月球上能不能给手机充电"})
    data = r.json()["data"]
    assert r.json()["code"] == 0
    assert data["retrieved"] is False
    assert data["total"] == 0 and data["evidence"] == []
    assert data["degraded"] is False
