"""摄取-检索 round-trip：上传 → 切分 → 双写 → 检索 → 证据（对应设计 M8）。"""
from app.infra.embedder import get_embedder
from app.infra.models import Document
from app.services.ingest import ingest_document
from app.schemas.retrieve import RetrieveRequest
from app.services.retrieval import retrieve_documents

from tests.conftest import AUTH

LONG_DOC = (
    "# 测试文档\n\n"
    "。" .join([f"第{i}段，皮具五金件的电镀层在潮湿环境下会出现氧化斑点，需要区分正常磨损与质量缺陷" for i in range(1, 12)])
    + "。\n\n## 仅退款规范\n仅退款适用于运输破损与严重描述不符两类场景，其余引导退货退款。\n"
)


def _ingest(db, tenant_id="t_bagshop_001", category="箱包", content=LONG_DOC, title="摄取测试文档") -> Document:
    doc = Document(
        tenant_id=tenant_id, layer="merchant", title=title,
        file_name="t.md", file_type="md", category=category, status="uploaded",
    )
    db.add(doc)
    db.commit()
    ingest_document(db, doc, content.encode("utf-8"), get_embedder())
    return doc


def test_ingest_creates_indexed_chunks(db):
    doc = _ingest(db)
    assert doc.status == "indexed" and doc.error_msg is None
    assert doc.chunk_count >= 2  # 长文档必须被切开
    assert all(len(c.content) <= 400 for c in doc.chunks)


def test_ingest_failure_marks_failed(db):
    doc = Document(
        tenant_id="t_bagshop_001", layer="merchant", title="坏文档",
        file_name="t.pdf", file_type="pdf", category="", status="uploaded",
    )
    db.add(doc)
    db.commit()
    try:
        ingest_document(db, doc, b"this is not a pdf", get_embedder())
        raised = False
    except Exception:
        raised = True
    assert raised
    db.refresh(doc)
    assert doc.status == "failed" and doc.error_msg


def test_roundtrip_retrieve_hits_new_document(db):
    doc = _ingest(db, title="roundtrip 专属文档", content="电子发票开具流程：订单完成后在订单详情页申请电子发票，七个工作日内开具。")
    # include_case=False：本用例验证文档 roundtrip，不掺杂案例排序（案例优先有自己的用例）
    req = RetrieveRequest(tenant_id="t_bagshop_001", query="怎么开电子发票", top_k=5, include_case=False)
    data = retrieve_documents(db, req)
    assert data["retrieved"] is True
    hit_titles = {e["source_title"] for e in data["evidence"]}
    assert "roundtrip 专属文档" in hit_titles
    assert data["evidence"][0]["source_type"] == "merchant"


def test_category_filter_narrows_results(db):
    _ingest(db, category="箱包", title="箱包类文档", content="真皮包具的保养方法：避免暴晒，使用专用护理油每月保养一次。")
    _ingest(db, category="服饰", title="服饰类文档", content="真丝衬衫的洗涤要求：三十度以下水温手洗，不可漂白，低温熨烫。")

    base = dict(tenant_id="t_bagshop_001", query="包具保养 真丝衬衫洗涤", top_k=10)
    unfiltered = retrieve_documents(db, RetrieveRequest(**base))
    filtered = retrieve_documents(db, RetrieveRequest(**base, filters={"category": ["服饰"]}))

    # 未过滤：两份文档的切片都可能出现
    cats_unfiltered = {e["metadata"].get("category") for e in unfiltered["evidence"]}
    assert {"箱包", "服饰"} <= cats_unfiltered
    # 品类过滤生效：只剩服饰类证据
    assert filtered["evidence"], "过滤后仍应有服饰类结果"
    assert all(e["metadata"].get("category") == "服饰" for e in filtered["evidence"])
    assert "箱包" not in {e["metadata"].get("category") for e in filtered["evidence"]}


def test_ingest_via_upload_endpoint(client):
    content = "退换货时效：签收后七天内支持无理由退货，十五天内支持质量问题退货。".encode("utf-8")
    r = client.post(
        "/api/v1/documents/upload",
        data={"tenant_id": "t_bagshop_001", "category": "箱包", "title": "退换货时效说明"},
        files={"file": ("policy.md", content, "text/markdown")},
    )
    assert r.status_code == 200 and r.json()["code"] == 0
    data = r.json()["data"]
    assert data["status"] == "indexed" and data["chunk_count"] >= 1

    # 上传立即可检索
    r2 = client.post(
        "/api/v1/retrieve", headers=AUTH,
        json={"tenant_id": "t_bagshop_001", "query": "无理由退货时效是多久"},
    )
    titles = {e["source_title"] for e in r2.json()["data"]["evidence"]}
    assert "退换货时效说明" in titles

    # 不支持的文件类型
    r3 = client.post(
        "/api/v1/documents/upload",
        data={"tenant_id": "t_bagshop_001"},
        files={"file": ("x.exe", b"MZ...", "application/octet-stream")},
    )
    assert r3.status_code == 422
