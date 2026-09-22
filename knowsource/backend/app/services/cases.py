"""案例知识服务：回写 → 人工确认 → 索引进检索（半自动链路，飞轮的沉淀端）。

状态机：pending（回写进来，待确认）→ confirmed（已索引，可被检索命中）/ dismissed（不入库）。
案例切片 layer=merchant + is_case=true + case_id 元数据：与商户文档同层同租户隔离，
检索时由编排层按 is_case 拆分排序（案例 → 商户 → 平台，契约 v1.3 §3.5）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ids import new_chunk_id
from app.infra.embedder import get_embedder
from app.infra.models import Case, Chunk, Document
from app.infra.vector import get_vector_store
from app.services.ingest import segment_for_fts


def case_content(case: Case) -> str:
    """案例切片正文：原始问题 + 审核理由，保持与契约 §6 示例一致的叙事形态。"""
    return f"历史案例：客户问题：{case.original_query}\n审核结论：{case.review_result}"


def confirm_and_index(db: Session, case: Case, *, review_result: str | None = None, category: str | None = None) -> Case:
    """确认案例并索引进检索。允许在确认时修订审核理由与品类（人工确认环节）。"""
    if case.status == "confirmed":
        return case
    if review_result is not None and review_result.strip():
        case.review_result = review_result.strip()
    if category is not None:
        case.category = category[:64]

    now_iso = datetime.now(timezone.utc).isoformat()
    document = Document(
        tenant_id=case.tenant_id,
        layer="merchant",
        title=f"案例：{case.original_query[:64]}",
        file_name=f"{case.case_id}.md",
        file_type="case",
        category=case.category,
        status="indexed",
    )
    db.add(document)
    db.flush()  # 取 document.id

    content = case_content(case)
    chunk_id = new_chunk_id()
    db.add(
        Chunk(
            chunk_id=chunk_id,
            document_id=document.id,
            tenant_id=case.tenant_id,
            layer="merchant",
            chunk_index=0,
            content=content,
            content_fts=func.to_tsvector("simple", segment_for_fts(content)),
            meta={
                "doc_title": document.title,
                "category": case.category,
                "is_case": True,
                "case_id": case.case_id,
                "original_query": case.original_query,
                "review_result": case.review_result,
                "feedback_source": case.feedback_source,
            },
        )
    )
    db.commit()

    get_vector_store().upsert(
        chunk_ids=[chunk_id],
        embeddings=[get_embedder().embed([content])[0]],
        documents=[content],
        metadatas=[
            {
                "tenant_id": case.tenant_id,
                "layer": "merchant",
                "document_id": document.id,
                "doc_title": document.title,
                "chunk_index": 0,
                "created_at": now_iso,
                "category": case.category,
                "is_case": True,
                "case_id": case.case_id,
                "original_query": case.original_query,
                "review_result": case.review_result,
                "feedback_source": case.feedback_source,
            }
        ],
    )

    case.status = "confirmed"
    case.chunk_id = chunk_id
    case.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    return case


def register_hit(db: Session, case_id: str) -> int | None:
    """检索命中案例时 hit_count +1，返回命中后的最新值；案例不存在返回 None。"""
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if case is None:
        return None
    case.hit_count = (case.hit_count or 0) + 1
    db.commit()
    return case.hit_count
