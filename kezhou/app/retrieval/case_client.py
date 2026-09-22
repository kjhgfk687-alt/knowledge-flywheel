"""知源案例回写客户端：契约 v1.3 §7 `POST /api/v1/cases`（飞轮沉淀端）。

链路语义：客舟转人工 → 人工审核写理由 → 本客户端回写知源（产生 pending 案例）
→ 知源控制台人工确认后才进入案例知识库可被检索（半自动，见契约 §7）。

与检索客户端同一套工程约定：统一结果对象、severity 分级
（CRITICAL=鉴权/契约违约、WARN=429、ERROR=其余）、不自动重试。
"""

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.retrieval.client import _CODE_NAMES, _CRITICAL_CODES, _WARN_CODES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaseWritebackResult:
    ok: bool
    case_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    severity: str = "info"
    latency_ms: int | None = None


class CaseWritebackClient(Protocol):
    async def write_back(
        self,
        *,
        tenant_id: str,
        original_query: str,
        review_result: str,
        query_id: str | None = None,
        category: str = "",
        feedback_source: str = "kezhou",
    ) -> CaseWritebackResult: ...


@dataclass(frozen=True)
class CaseListResult:
    ok: bool
    cases: tuple[dict, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


class ZhiyuanCaseWritebackClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.zhiyuan_base_url,
            timeout=settings.retrieve_timeout_seconds,
            headers={"X-API-Key": settings.zhiyuan_api_key},
            transport=transport,
            trust_env=False,  # 内网直连，避开系统代理（同检索客户端，联调实测坑）
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ZhiyuanCaseWritebackClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def write_back(
        self,
        *,
        tenant_id: str,
        original_query: str,
        review_result: str,
        query_id: str | None = None,
        category: str = "",
        feedback_source: str = "kezhou",
    ) -> CaseWritebackResult:
        payload = {
            "tenant_id": tenant_id,
            "original_query": original_query,
            "review_result": review_result,
            "query_id": query_id,
            "category": category,
            "feedback_source": feedback_source,
        }
        started = time.perf_counter()

        def latency() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:
            resp = await self._client.post("/api/v1/cases", json=payload)
        except httpx.TimeoutException:
            return CaseWritebackResult(False, error_code="CLIENT_TIMEOUT", error_message="回写等待知源响应超时", severity="error", latency_ms=latency())
        except httpx.HTTPError as e:
            return CaseWritebackResult(False, error_code="CONNECTION_ERROR", error_message=f"知源连接失败: {e}", severity="error", latency_ms=latency())

        try:
            envelope = resp.json()
            code = envelope["code"]
            message = envelope["message"]
        except Exception:
            if resp.status_code >= 500:
                return CaseWritebackResult(False, error_code="GATEWAY_ERROR", error_message=f"HTTP {resp.status_code} 且响应非契约信封（网关/代理层故障）", severity="error", latency_ms=latency())
            logger.critical("案例回写响应非契约信封 http=%s body前200字符=%.200s", resp.status_code, resp.text)
            return CaseWritebackResult(False, error_code="CONTRACT_VIOLATION", error_message=f"响应不是契约 JSON 信封（HTTP {resp.status_code}）", severity="critical", latency_ms=latency())

        if code != 0:
            name = _CODE_NAMES.get(code, f"UNKNOWN_{code}")
            severity = "critical" if name in _CRITICAL_CODES else ("warn" if name in _WARN_CODES else "error")
            return CaseWritebackResult(False, error_code=name, error_message=str(message), severity=severity, latency_ms=latency())

        data = envelope.get("data") or {}
        case_id = data.get("case_id")
        if not case_id:
            logger.critical("案例回写成功信封缺 data.case_id（契约 v1.3 §7.2）")
            return CaseWritebackResult(False, error_code="CONTRACT_VIOLATION", error_message="data.case_id 缺失（契约违约）", severity="critical", latency_ms=latency())

        logger.info("CASE_WRITEBACK ok case_id=%s query_id=%s tenant=%s latency=%sms", case_id, query_id, tenant_id, latency())
        return CaseWritebackResult(True, case_id=case_id, latency_ms=latency())

    async def list_cases(self, tenant_id: str, status: str | None = None) -> CaseListResult:
        """知源控制台案例列表代理（含飞轮指标 hit_count），按 hit_count 降序取前 10。"""
        params: dict = {"tenant_id": tenant_id}
        if status:
            params["status"] = status
        try:
            resp = await self._client.get("/api/v1/cases", params=params)
        except httpx.HTTPError as e:
            return CaseListResult(False, error_code="CONNECTION_ERROR", error_message=f"知源连接失败: {e}")
        try:
            envelope = resp.json()
            code = envelope["code"]
        except Exception:
            return CaseListResult(False, error_code="CONTRACT_VIOLATION", error_message=f"响应不是契约 JSON 信封（HTTP {resp.status_code}）")
        if code != 0:
            name = _CODE_NAMES.get(code, f"UNKNOWN_{code}")
            return CaseListResult(False, error_code=name, error_message=str(envelope.get("message")))
        cases = sorted(envelope.get("data") or [], key=lambda c: c.get("hit_count", 0), reverse=True)[:10]
        return CaseListResult(True, cases=tuple(cases))


class DryRunCaseWritebackClient:
    """测试/演示用：不真正调知源，记录调用并返回伪 case_id。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def aclose(self) -> None:
        return None

    async def list_cases(self, tenant_id: str, status: str | None = None) -> CaseListResult:
        return CaseListResult(True, cases=())

    async def write_back(self, *, tenant_id, original_query, review_result, query_id=None, category="", feedback_source="kezhou") -> CaseWritebackResult:
        self.calls.append({
            "tenant_id": tenant_id, "original_query": original_query, "review_result": review_result,
            "query_id": query_id, "category": category, "feedback_source": feedback_source,
        })
        return CaseWritebackResult(True, case_id=f"case_dryrun_{len(self.calls):04d}", latency_ms=1)


class FailingCaseWritebackClient:
    """测试用：始终失败（可注入指定错误码）。"""

    def __init__(self, error_code: str = "CONNECTION_ERROR", message: str = "知源不可达"):
        self.error_code = error_code
        self.message = message
        self.calls: list[dict] = []

    async def aclose(self) -> None:
        return None

    async def write_back(self, **kwargs) -> CaseWritebackResult:
        self.calls.append(kwargs)
        return CaseWritebackResult(False, error_code=self.error_code, error_message=self.message, severity="error")
