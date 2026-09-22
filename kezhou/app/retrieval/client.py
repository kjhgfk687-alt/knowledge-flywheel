"""知源 /retrieve 真 REST 客户端（阶段二）。契约 v1.1：
工作区 `2026-09-22_接口契约+项目设计+简历/知源retrieve接口契约.md`（字段名/结构不得擅改）。

行为约定（2026-09-22 已确认设计）：
- 统一三态 RetrievalResult；空判定严格按 `retrieved` 字段，不凭 evidence==[]；
- severity 分级：CRITICAL = 鉴权(40100/40101) + 契约违约（响应结构不符）；
  WARN = 429 限流；ERROR = 其余（超时/5xx/连接失败/串租户/4xx）；
- 重试开关 KEZHOU_RETRIEVE_MAX_RETRIES（默认 0=不重试）：只对瞬时故障重试
  （连接错误/客户端超时/503/504/500 信封）；鉴权与参数类 4xx 不会自愈，不重试；
- 超时 KEZHOU_RETRIEVE_TIMEOUT_SECONDS=3.5 > 知源 3s 总预算：刻意留余量让知源
  的 504 错误信封到达并被归类，而不是客户端裸超时（知源预算变更须同步）；
- 客户端不做参数校验、不做 score 过滤——422 交给知源判，证据原样上交节点层；
- 响应 tenant_id 回显与请求不一致 → TENANT_MISMATCH（防串租户，契约 3.2）。
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.retrieval.types import Evidence, RetrievalResult, RetrievalStatus

logger = logging.getLogger(__name__)

# 契约第 4 节错误码 → 错误标识（信封只带数字码，客户端负责翻译成可读标识）
_CODE_NAMES = {
    40100: "AUTH_KEY_MISSING",
    40101: "AUTH_KEY_INVALID",
    40001: "TENANT_ID_INVALID",
    40002: "QUERY_INVALID",
    40003: "TOP_K_INVALID",
    40004: "FILTERS_INVALID",
    40005: "BODY_NOT_JSON",
    40401: "TENANT_NOT_FOUND",
    40301: "TENANT_DISABLED",
    50301: "RETRIEVAL_UNAVAILABLE",
    50401: "RETRIEVAL_TIMEOUT",
    42901: "RATE_LIMITED",
    50000: "INTERNAL_ERROR",
}
_CRITICAL_CODES = {"AUTH_KEY_MISSING", "AUTH_KEY_INVALID"}
_WARN_CODES = {"RATE_LIMITED"}
# 允许重试的瞬时故障（连接层 + 知源侧服务性失败）
_RETRYABLE_CODES = {
    "CLIENT_TIMEOUT", "CONNECTION_ERROR", "GATEWAY_ERROR",
    "RETRIEVAL_UNAVAILABLE", "RETRIEVAL_TIMEOUT", "INTERNAL_ERROR",
}

_EVIDENCE_REQUIRED = ("chunk_id", "content", "source_type", "source_title", "score", "metadata")


def _error(code: str, message: str, severity: str, latency_ms: int, status: RetrievalStatus = RetrievalStatus.ERROR) -> RetrievalResult:
    return RetrievalResult(
        status=status, error_code=code, error_message=message, severity=severity, latency_ms=latency_ms
    )


class ZhiyuanRetrievalClient:
    """Retrieve 节点只认 RetrievalClient 协议；本实现与 Mock 可直接互换。"""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.zhiyuan_base_url,
            timeout=settings.retrieve_timeout_seconds,
            headers={"X-API-Key": settings.zhiyuan_api_key},
            transport=transport,  # 测试注入 MockTransport 用
            trust_env=False,  # 内网服务直连：Windows 系统代理会劫持 localhost 流量
            # （联调实测：代理对无服务端口返回 503 空体，被误判为契约违约 CRITICAL）
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ZhiyuanRetrievalClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        include_platform: bool = True,
        include_case: bool = True,
    ) -> RetrievalResult:
        payload = {
            "tenant_id": tenant_id,
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "include_platform": include_platform,
            "include_case": include_case,
        }
        max_attempts = 1 + max(0, self._settings.retrieve_max_retries)
        started = time.perf_counter()
        result: RetrievalResult | None = None
        for attempt in range(1, max_attempts + 1):
            result = await self._attempt(tenant_id, payload, started)
            if result.status is not RetrievalStatus.ERROR or result.error_code not in _RETRYABLE_CODES:
                return result
            if attempt < max_attempts:
                logger.warning(
                    "知源检索瞬时失败（第 %s/%s 次尝试）code=%s msg=%s",
                    attempt, max_attempts, result.error_code, result.error_message,
                )
                await asyncio.sleep(0.2 * attempt)
        assert result is not None
        return result

    async def _attempt(self, tenant_id: str, payload: dict[str, Any], started: float) -> RetrievalResult:
        def latency() -> int:
            return int((time.perf_counter() - started) * 1000)

        # ---- 传输层 ----
        try:
            resp = await self._client.post("/api/v1/retrieve", json=payload)
        except httpx.TimeoutException:
            return _error("CLIENT_TIMEOUT", f"客户端等待知源响应超时（{self._settings.retrieve_timeout_seconds}s）", "error", latency())
        except httpx.HTTPError as e:
            return _error("CONNECTION_ERROR", f"知源连接失败: {e}", "error", latency())

        # ---- 信封层：非 JSON 或缺 code/message 键 = 契约违约（CRITICAL）----
        try:
            envelope = resp.json()
            envelope_code = envelope["code"]
            envelope_message = envelope["message"]
        except Exception:
            if resp.status_code >= 500:
                # 5xx 且非信封：网关/代理层故障（空响应体、HTML 错误页），基础设施问题而非知源违约
                return _error("GATEWAY_ERROR", f"HTTP {resp.status_code} 且响应非契约信封（网关/代理层故障）", "error", latency())
            logger.critical("知源响应非契约信封 http=%s body的前200字符=%.200s", resp.status_code, resp.text)
            return _error("CONTRACT_VIOLATION", f"响应不是契约 JSON 信封（HTTP {resp.status_code}）", "critical", latency())

        if envelope_code != 0:
            name = _CODE_NAMES.get(envelope_code, f"UNKNOWN_{envelope_code}")
            if name in _CRITICAL_CODES:
                severity = "critical"
            elif name in _WARN_CODES:
                severity = "warn"
            else:
                severity = "error"
            return _error(name, str(envelope_message), severity, latency())

        # ---- data 层 ----
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("retrieved"), bool):
            logger.critical("知源成功信封缺 data.retrieved 布尔字段（契约 3.2）")
            return _error("CONTRACT_VIOLATION", "data.retrieved 缺失或非布尔（契约违约）", "critical", latency())
        if data.get("tenant_id") != tenant_id:
            logger.error("知源回显 tenant_id=%s 与请求 %s 不一致，疑似串租户", data.get("tenant_id"), tenant_id)
            return _error("TENANT_MISMATCH", "响应租户与请求租户不一致", "error", latency())

        # 状态严格由 retrieved 标志决定（契约 3.2 明文禁止凭 evidence==[] 判断）；
        # 证据字段校验只在确有证据（retrieved=true）时进行
        status = RetrievalStatus.OK if data["retrieved"] else RetrievalStatus.EMPTY
        raw_evidence = data.get("evidence") or []

        evidences: list[Evidence] = []
        for i, item in enumerate(raw_evidence if status is RetrievalStatus.OK else []):
            missing = [k for k in _EVIDENCE_REQUIRED if k not in item]
            if missing:
                logger.critical("证据第 %s 条缺必填字段 %s（契约 3.3）", i, missing)
                return _error("CONTRACT_VIOLATION", f"evidence[{i}] 缺字段 {missing}（契约违约）", "critical", latency())
            evidences.append(Evidence(
                chunk_id=item["chunk_id"],
                content=item["content"],
                source_type=item["source_type"],
                source_title=item["source_title"],
                score=float(item["score"]),
                metadata=item["metadata"],
            ))
        if status is RetrievalStatus.EMPTY and raw_evidence:
            logger.warning("retrieved=false 但 evidence 非空（%s 条），按契约以标志为准丢弃", len(raw_evidence))

        return RetrievalResult(
            status=status,
            query_id=data.get("query_id"),
            evidence=evidences,
            degraded=bool(data.get("degraded")),
            degrade_reason=data.get("degrade_reason"),
            latency_ms=int(data["latency_ms"]) if isinstance(data.get("latency_ms"), int) else latency(),
        )
