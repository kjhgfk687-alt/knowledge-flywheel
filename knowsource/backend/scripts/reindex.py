"""从 PG 全量重建 Chroma 索引（PG 是事实源，Chroma 是可重建索引）。

用途：元数据结构升级（如阶段三补 is_case/case_id）或索引损坏时全量重建。
用法：python scripts/reindex.py   （会先清空 Chroma 集合内全部数据再重建）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infra.db import get_session_factory
from app.infra.embedder import get_embedder
from app.infra.models import Chunk
from app.infra.vector import get_vector_store


def chunk_metadata(row: Chunk) -> dict:
    meta = row.meta or {}
    return {
        "tenant_id": row.tenant_id,
        "layer": row.layer,
        "document_id": row.document_id,
        "doc_title": meta.get("doc_title", ""),
        "chunk_index": row.chunk_index,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "category": meta.get("category", ""),
        "is_case": bool(meta.get("is_case", False)),
        "case_id": meta.get("case_id", ""),
        # 案例切片的专属字段，普通切片为空串占位
        "original_query": meta.get("original_query", ""),
        "review_result": meta.get("review_result", ""),
        "feedback_source": meta.get("feedback_source", ""),
    }


def main() -> None:
    db = get_session_factory()()
    embedder = get_embedder()
    store = get_vector_store()
    try:
        rows = db.query(Chunk).order_by(Chunk.id).all()
        print(f"rebuilding index from {len(rows)} chunks ...")
        existing = store._collection.get(include=[])
        if existing["ids"]:
            store._collection.delete(ids=existing["ids"])  # 清空重建
        for row in rows:
            store.upsert(
                chunk_ids=[row.chunk_id],
                embeddings=[embedder.embed([row.content])[0]],
                documents=[row.content],
                metadatas=[chunk_metadata(row)],
            )
        print(f"done: {store.count()} vectors in collection")
    finally:
        db.close()


if __name__ == "__main__":
    main()
