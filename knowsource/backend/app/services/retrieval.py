"""检索编排：向量路径 → （超时/失败）→ PG FTS 降级 → 组装契约响应。

契约 §5：降级返回不报错；两条路径都失败才 50301；总预算超限 50401。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import (
    RETRIEVAL_TIMEOUT,
    RETRIEVAL_UNAVAILABLE,
    TENANT_ID_INVALID,
    api_error,
)
from app.core.ids import new_query_id
from app.infra.embedder import get_embedder
from app.infra.keyword import keyword_search
from app.infra.models import PLATFORM_TENANT_ID
from app.infra.vector import get_vector_store
from app.schemas.retrieve import RetrieveRequest
from app.services.cases import register_hit
from app.services.tenants import get_tenant_or_raise

# 模块级线程池：把嵌入/向量调用挪出请求线程，便于施加内部超时预算
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ks-vector")


class _VectorPathError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _run_with_timeout(fn, timeout_s: float):
    future = _executor.submit(fn)
    return future.result(timeout=timeout_s)  # TimeoutError 由调用方捕获


def _vector_path(query: str, tenant_id: str, layers: list[str], top_k: int, extra_where: list[dict] | None) -> list[dict]:
    s = get_settings()
    budget = s.vector_timeout_ms / 1000
    try:
        # ---- 嵌入阶段：失败/超时 → embedding_failed ----
        try:
            embedding = _run_with_timeout(lambda: get_embedder().embed([query])[0], budget)
        except FutureTimeoutError:
            raise _VectorPathError("embedding_failed") from None
        except Exception as exc:  # noqa: BLE001
            raise _VectorPathError("embedding_failed") from exc
        # ---- 向量检索阶段：失败/超时 → vector_timeout / vector_store_unavailable ----
        try:
            return _run_with_timeout(
                lambda: get_vector_store().query(embedding, tenant_id, layers, top_k, extra_where),
                budget,
            )
        except FutureTimeoutError:
            raise _VectorPathError("vector_timeout") from None
        except Exception as exc:  # noqa: BLE001 —— 向量库连接失败等
            raise _VectorPathError("vector_store_unavailable") from exc
    except _VectorPathError:
        raise


def _resolve_source_type(meta: dict) -> str:
    """阶段一只有 merchant；阶段三案例切片将带 meta["case_id"] → source_type=case。"""
    if meta.get("case_id"):
        return "case"
    layer = meta.get("layer", "merchant")
    return "merchant" if layer == "merchant" else "platform"


def _to_evidence_meta(meta: dict) -> dict:
    """Chroma/FTS 元数据 → 契约 §3.4 响应 metadata。"""
    doc_id = f"doc_{meta.get('document_id', '')}"
    out = {
        "doc_id": doc_id,
        "chunk_index": int(meta.get("chunk_index", 0)),
        "created_at": meta.get("created_at") or "",
    }
    if meta.get("category"):
        out["category"] = meta["category"]
    # 案例字段随阶段三出现，存在即透出
    for key in ("case_id", "original_query", "review_result", "hit_count", "feedback_source"):
        if meta.get(key) is not None:
            out[key] = meta[key]
    return out


def retrieve_documents(db: Session, req: RetrieveRequest) -> dict:
    """执行检索，返回契约 §3.2 的 data 结构（dict）。

    双层排序（契约 v1.2 §3.5）：商户层证据在前（层内按 score 降序），
    平台层证据作为兜底约束附后；include_platform=true 且平台层有相关证据时，
    平台层保留最后 1 个席位（top_k≥2 时），total 不超过 top_k。
    """
    s = get_settings()
    started = time.perf_counter()
    query_id = new_query_id()

    if req.tenant_id == PLATFORM_TENANT_ID:
        raise api_error(TENANT_ID_INVALID, "platform 为平台层保留标识，不能作为 tenant_id 检索")
    get_tenant_or_raise(db, req.tenant_id)

    extra_where: list[dict] = []
    categories: list[str] | None = None
    if req.filters:
        if req.filters.category:
            categories = req.filters.category
            extra_where.append({"category": {"$in": categories}})
        if req.filters.auth_status:
            extra_where.append({"auth_status": {"$in": list(req.filters.auth_status)}})
    if not req.include_case:
        # include_case=false：商户层查询中排除案例切片（契约 §2 开关）
        extra_where.append({"is_case": {"$eq": False}})

    # ---- 向量路径：商户层（含案例切片）+ （可选）平台层，嵌入只算一次 ----
    degraded = False
    degrade_reason: str | None = None
    try:
        merchant_hits = _vector_path(req.query, req.tenant_id, ["merchant"], req.top_k, extra_where or None)
        platform_hits = (
            _vector_path(req.query, PLATFORM_TENANT_ID, ["platform"], req.top_k, extra_where or None)
            if req.include_platform
            else []
        )
    except _VectorPathError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms > s.retrieval_total_budget_ms:
            # 向量路径已耗尽总预算：不再尝试降级（契约 §5 二级失败）
            raise api_error(
                RETRIEVAL_TIMEOUT, f"检索总耗时超预算（{s.retrieval_total_budget_ms}ms）"
            ) from exc
        # ---- 契约 §5 降级：两层都切 PG FTS 关键词兜底 ----
        degraded = True
        degrade_reason = exc.reason
        try:
            merchant_hits = keyword_search(
                db, req.tenant_id, ["merchant"], req.query, req.top_k, categories,
                include_case=req.include_case,
            )
            platform_hits = (
                keyword_search(db, None, ["platform"], req.query, req.top_k, categories)
                if req.include_platform
                else []
            )
        except Exception as keyword_exc:  # noqa: BLE001
            raise api_error(
                RETRIEVAL_UNAVAILABLE, f"向量检索与关键词降级路径均失败: {keyword_exc}"
            ) from keyword_exc

    def to_evidence(hits: list[dict]) -> list[dict]:
        return [
            {
                "chunk_id": hit["chunk_id"],
                "content": hit["content"],
                "source_type": _resolve_source_type(hit["metadata"]),
                "source_title": hit["metadata"].get("doc_title", ""),
                "score": float(hit["score"]),
                "metadata": _to_evidence_meta(hit["metadata"]),
            }
            for hit in hits
            if float(hit["score"]) >= s.min_score  # 相关性下限：零相关的填充项不作为证据返回
        ]

    # 案例切片混在商户层结果里，按元数据拆分后排序：案例 → 商户 → 平台（契约 v1.3 §3.5）
    all_merchant = to_evidence(merchant_hits)
    case_evidence = [e for e in all_merchant if e["source_type"] == "case"]
    pure_merchant = [e for e in all_merchant if e["source_type"] != "case"]
    platform_evidence = to_evidence(platform_hits)

    evidence = _merge_layered(case_evidence, pure_merchant, platform_evidence, req.top_k)
    _register_case_hits(db, evidence)

    latency_ms = int((time.perf_counter() - started) * 1000)

    return {
        "query_id": query_id,
        "tenant_id": req.tenant_id,
        "retrieved": len(evidence) > 0,
        "degraded": degraded,
        "degrade_reason": degrade_reason,
        "total": len(evidence),
        "evidence": evidence,
        "latency_ms": latency_ms,
    }


def _register_case_hits(db: Session, evidence: list[dict]) -> None:
    """案例证据命中即 hit_count +1（飞轮核心指标），回填为命中后的最新值。"""
    for e in evidence:
        case_id = e["metadata"].get("case_id")
        if not case_id:
            continue
        latest = register_hit(db, case_id)
        if latest is not None:
            e["metadata"]["hit_count"] = latest


def _merge_layered(cases: list[dict], merchant: list[dict], platform: list[dict], top_k: int) -> list[dict]:
    """契约 v1.3 排序策略：案例 → 商户 → 平台，total ≤ top_k。

    案例层优先命中（飞轮核心），商户层其次，平台层兜底保留最后 1 个席位
    （top_k ≥ 2 且平台层有相关证据时）；上层不足时下层顺位补足剩余名额。
    """
    reserve = 1 if (platform and top_k >= 2) else 0
    rest = top_k - reserve
    merged = cases[:rest]
    merged.extend(merchant[: max(rest - len(merged), 0)])
    merged.extend(platform[: max(top_k - len(merged), 0)])
    return merged
