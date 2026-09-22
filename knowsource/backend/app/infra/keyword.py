"""关键词降级检索：PG 全文检索（jieba 预分词 + tsvector + GIN）。

契约 §5：向量路径失败/超时时的兜底。
查询语义：分词后 OR 连接（降级路径偏召回），ts_rank 排序，名次线性归一化到 0-1。
"""
from __future__ import annotations

import re

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.infra.models import Chunk
from app.services.ingest import segment_for_fts

_SANITIZE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _tsquery_or(query: str) -> str | None:
    """jieba 分词 + 清洗 + OR 连接，构造 to_tsquery 的查询串。无有效词返回 None。"""
    tokens = []
    for tok in segment_for_fts(query).split():
        clean = _SANITIZE.sub("", tok)
        if clean:
            tokens.append(clean)
    return " | ".join(tokens) if tokens else None


def keyword_search(
    db: Session,
    tenant_id: str | None,
    layers: list[str],
    query: str,
    top_k: int,
    categories: list[str] | None = None,
    include_case: bool = True,
) -> list[dict]:
    """返回 [{chunk_id, content, score, metadata}]，与 VectorStore.query 输出同构。
    tenant_id=None 查平台层（共享，仅按 layer 过滤）。"""
    tsq_str = _tsquery_or(query)
    if not tsq_str:
        return []
    tsq = func.to_tsquery("simple", tsq_str)
    conds = [Chunk.layer.in_(layers), Chunk.content_fts.op("@@")(tsq)]
    if tenant_id is not None:
        conds.insert(0, Chunk.tenant_id == tenant_id)
    if not include_case:
        conds.append(Chunk.meta["is_case"].astext == "false")
    q = db.query(Chunk).filter(*conds)
    if categories:
        # meta JSONB：无该键/未分类的切片不参与品类过滤
        q = q.filter(Chunk.meta["category"].astext.in_(categories))
    rows = q.order_by(desc(func.ts_rank(Chunk.content_fts, tsq))).limit(top_k).all()
    if not rows:
        return []

    # ts_rank 原始值分布零散，阶段一按名次线性归一化（第1名=1.0，保底0.1），单调性与排序一致
    n = len(rows)
    return [
        {
            "chunk_id": row.chunk_id,
            "content": row.content,
            "score": round(max(0.1, 1.0 - 0.9 * i / max(1, n - 1)) if n > 1 else 1.0, 4),
            "metadata": {
                "tenant_id": row.tenant_id,
                "layer": row.layer,
                "document_id": row.document_id,
                **(row.meta or {}),
            },
        }
        for i, row in enumerate(rows)
    ]
