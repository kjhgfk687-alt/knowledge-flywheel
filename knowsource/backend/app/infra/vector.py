"""Chroma 封装：embedded 模式，可重建索引（事实源在 PG）。

score 语义：Chroma cosine distance d∈[0,2]，相似度 = clamp(1-d, 0, 1)，即契约 §3 的 0-1 分。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import chromadb

from app.config import get_settings

COLLECTION_NAME = "knowsource"


def _chroma_settings() -> chromadb.Settings:
    # 关闭遥测：内网/受限网络下会尝试上报导致卡顿
    return chromadb.Settings(anonymized_telemetry=False)


def _build_where(
    tenant_id: str | None,
    layers: list[str],
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """商户层强制带租户隔离；tenant_id=None 表示平台层共享查询（按 layer 识别）。
    extra 为契约 filters 生成的附加条件（category / auth_status）。
    注意：Chroma 对元数据缺失的键做 $in 匹配时会排除该条，正合"有该字段才参与过滤"的语义。"""
    conditions: list[dict[str, Any]] = []
    if tenant_id is not None:
        conditions.append({"tenant_id": {"$eq": tenant_id}})
    if len(layers) == 1:
        conditions.append({"layer": {"$eq": layers[0]}})
    elif len(layers) > 1:
        conditions.append({"layer": {"$in": layers}})
    conditions.extend(extra or [])
    if len(conditions) == 1:
        return conditions[0]
    if not conditions:
        raise ValueError("检索条件不能为空（拒绝全库扫描）")
    return {"$and": conditions}


class VectorStore:
    def __init__(self, path: str):
        self._client = chromadb.PersistentClient(path=path, settings=_chroma_settings())
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        self._collection.upsert(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(
        self,
        embedding: list[float],
        tenant_id: str | None,
        layers: list[str],
        top_k: int,
        extra_where: list[dict[str, Any]] | None = None,
    ) -> list[dict]:
        """返回 [{chunk_id, content, score, metadata}]，按相似度降序。
        tenant_id=None 查平台层（共享），否则强制租户隔离。"""
        res = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=_build_where(tenant_id, layers, extra_where),
            include=["documents", "metadatas", "distances"],
        )
        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        return [
            {
                "chunk_id": cid,
                "content": doc,
                "score": round(max(0.0, min(1.0, 1.0 - dist)), 4),
                "metadata": dict(meta or {}),
            }
            for cid, doc, meta, dist in zip(ids, docs, metas, dists)
        ]

    def count(self) -> int:
        return self._collection.count()

    def delete_by_document(self, document_id: int) -> None:
        self._collection.delete(where={"document_id": {"$eq": document_id}})


@lru_cache
def get_vector_store() -> VectorStore:
    return VectorStore(get_settings().chroma_dir)
