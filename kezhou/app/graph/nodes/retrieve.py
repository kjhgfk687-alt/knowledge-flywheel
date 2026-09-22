"""retrieve：调用检索客户端（阶段一 Mock / 阶段二真实现），只更新状态，不做路由。

三分支（已确认设计）：OK / EMPTY / ERROR 由 RetrievalResult.status 携带；
日志按 severity 分级：CRITICAL=鉴权/契约违约，ERROR=超时/5xx/连接/TENANT_MISMATCH，WARN=429。
"""

import logging
from typing import Any

from app.config import Settings
from app.retrieval.types import RetrievalClient, RetrievalStatus

logger = logging.getLogger(__name__)

_SEVERITY_TO_LOG = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}


def make_retrieve(settings: Settings, client: RetrievalClient):
    async def retrieve(state: dict[str, Any]) -> dict[str, Any]:
        result = await client.retrieve(tenant_id=state["tenant_id"], query=state["user_input"])

        logger.log(
            _SEVERITY_TO_LOG.get(result.severity, logging.ERROR),
            "retrieve status=%s query_id=%s tenant=%s degraded=%s error=%s latency=%sms",
            result.status.value,
            result.query_id,
            state["tenant_id"],
            result.degraded,
            result.error_code,
            result.latency_ms,
        )

        return {
            "retrieval_status": result.status.value,
            "evidences": [
                {
                    "chunk_id": e.chunk_id,
                    "content": e.content,
                    "source_type": e.source_type,
                    "source_title": e.source_title,
                    "score": e.score,
                    "metadata": e.metadata,
                }
                for e in result.evidence
            ],
            "query_id": result.query_id,
            "retrieval_degraded": result.degraded,
            "degrade_reason": result.degrade_reason,
        }

    return retrieve


def after_retrieve(state: dict[str, Any]) -> str:
    """条件边：OK → generate；EMPTY/ERROR → human_handoff（区别由节点内派生 transfer_reason）。"""
    return "generate" if state.get("retrieval_status") == RetrievalStatus.OK.value else "human_handoff"
