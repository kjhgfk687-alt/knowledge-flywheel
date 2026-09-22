"""ZhiyuanRetrievalClient 单元测试：httpx.MockTransport 模拟契约 v1.1 全部响应形态。

覆盖：成功/空/降级信封、全部错误类别（severity 分级）、契约违约（CRITICAL）、
串租户、连接错误/超时、重试开关（含"只重试瞬时故障"）。
"""

import json

import httpx
import pytest

from app.config import Settings
from app.retrieval.client import ZhiyuanRetrievalClient
from app.retrieval.types import RetrievalStatus

TENANT = "t_bagshop_001"
QUERY = "客户说买的包五金掉色怀疑是假货，怎么处理？"

# 契约 6.2 成功响应（三条证据，score 降序）
_OK_ENVELOPE = {
    "code": 0, "message": "ok",
    "data": {
        "query_id": "ret_9f2a7c1e", "tenant_id": TENANT,
        "retrieved": True, "degraded": False, "degrade_reason": None, "total": 3,
        "evidence": [
            {"chunk_id": "chk_a1b2c3", "content": "历史案例：……", "source_type": "case",
             "source_title": "五金掉色疑假货仅退款案例", "score": 0.9132,
             "metadata": {"doc_id": "doc_c01", "chunk_index": 0, "created_at": "2026-09-20T10:12:00+08:00",
                          "case_id": "case_2201", "original_query": "……", "review_result": "……",
                          "hit_count": 7, "feedback_source": "kezhou"}},
            {"chunk_id": "chk_d4e5f6", "content": "本店售后政策：……", "source_type": "merchant",
             "source_title": "皮具店售后处理手册 v2", "score": 0.8774,
             "metadata": {"doc_id": "doc_m11", "chunk_index": 3, "created_at": "2026-09-10T09:00:00+08:00", "category": "箱包"}},
            {"chunk_id": "chk_g7h8i9", "content": "平台规范：……", "source_type": "platform",
             "source_title": "电商平台假货争议处理规范（2026-06 版）", "score": 0.8421,
             "metadata": {"doc_id": "doc_p03", "chunk_index": 1, "created_at": "2026-06-01T00:00:00+08:00", "effective_date": "2026-06-01"}},
        ],
        "latency_ms": 412,
    },
}


def _envelope(data: dict) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def _empty_data(**overrides) -> dict:
    data = {"query_id": "ret_3b8d21aa", "tenant_id": TENANT, "retrieved": False,
            "degraded": False, "degrade_reason": None, "total": 0, "evidence": [], "latency_ms": 289}
    data.update(overrides)
    return data


def _make_client(handler, retries: int = 0) -> ZhiyuanRetrievalClient:
    settings = Settings(
        zhiyuan_base_url="http://zhiyuan-test",
        retrieve_max_retries=retries,
        retrieve_timeout_seconds=3.5,
    )
    return ZhiyuanRetrievalClient(settings, transport=httpx.MockTransport(handler))


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_ok_envelope_parses_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_OK_ENVELOPE)

    async with _make_client(handler) as client:
        result = await client.retrieve(TENANT, QUERY, top_k=5)

    assert result.status is RetrievalStatus.OK
    assert result.severity == "info"
    assert result.query_id == "ret_9f2a7c1e"
    assert [e.source_type for e in result.evidence] == ["case", "merchant", "platform"]
    assert result.evidence[0].source_title == "五金掉色疑假货仅退款案例"
    assert result.evidence[0].metadata["case_id"] == "case_2201"
    assert result.degraded is False
    assert result.latency_ms == 412


async def test_request_shape_follows_contract():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-API-Key")
        captured["body"] = json.loads(request.content)
        return _json_response(_OK_ENVELOPE)

    async with _make_client(handler) as client:
        await client.retrieve(TENANT, QUERY, top_k=3,
                              filters={"category": ["箱包"], "auth_status": ["authentic"]},
                              include_case=False)

    assert captured["url"].endswith("/api/v1/retrieve")
    assert captured["api_key"] == "zs-kz-dev-key-001"
    body = captured["body"]
    assert body["tenant_id"] == TENANT
    assert body["top_k"] == 3
    assert body["filters"] == {"category": ["箱包"], "auth_status": ["authentic"]}
    assert body["include_case"] is False
    assert body["include_platform"] is True


async def test_empty_envelope_maps_by_retrieved_flag():
    """retrieved=false 是显式空标识：即使 evidence 意外非空也判 EMPTY（契约明文禁止凭数组判断）。"""
    data = _empty_data(evidence=[{"junk": True}])

    async with _make_client(lambda r: _json_response(_envelope(data))) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.status is RetrievalStatus.EMPTY
    assert result.query_id == "ret_3b8d21aa"


async def test_degraded_envelope_keeps_flag():
    data = _empty_data(retrieved=True, degraded=True, degrade_reason="vector_store_unavailable",
                       total=1, evidence=[_OK_ENVELOPE["data"]["evidence"][1]])
    data["query_id"] = "ret_77c0e9d2"

    async with _make_client(lambda r: _json_response(_envelope(data))) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.status is RetrievalStatus.OK
    assert result.degraded is True
    assert result.degrade_reason == "vector_store_unavailable"


async def test_error_envelopes_severity_mapping():
    cases = [
        (503, {"code": 50301, "message": "双路径均失败", "data": None}, "RETRIEVAL_UNAVAILABLE", "error"),
        (504, {"code": 50401, "message": "总耗时超预算", "data": None}, "RETRIEVAL_TIMEOUT", "error"),
        (401, {"code": 40101, "message": "密钥不正确", "data": None}, "AUTH_KEY_INVALID", "critical"),
        (401, {"code": 40100, "message": "未携带密钥", "data": None}, "AUTH_KEY_MISSING", "critical"),
        (429, {"code": 42901, "message": "触发限流", "data": None}, "RATE_LIMITED", "warn"),
        (404, {"code": 40401, "message": "租户不存在", "data": None}, "TENANT_NOT_FOUND", "error"),
        (422, {"code": 40002, "message": "query 超长", "data": None}, "QUERY_INVALID", "error"),
    ]
    for http_status, envelope, want_code, want_severity in cases:
        async with _make_client(lambda r, e=envelope, s=http_status: _json_response(e, s)) as client:
            result = await client.retrieve(TENANT, QUERY)
        assert result.status is RetrievalStatus.ERROR, envelope
        assert result.error_code == want_code, envelope
        assert result.severity == want_severity, envelope
        assert result.query_id is None


async def test_non_json_response_is_contract_violation_critical():
    async with _make_client(lambda r: httpx.Response(200, text="<html>gateway</html>")) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "CONTRACT_VIOLATION"
    assert result.severity == "critical"


async def test_5xx_non_envelope_is_gateway_error_not_contract_violation():
    """5xx 且非信封（空体/HTML 错误页）= 网关层故障，error 级可重试；不是知源契约违约。"""
    async with _make_client(lambda r: httpx.Response(503, text="")) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "GATEWAY_ERROR"
    assert result.severity == "error"


async def test_success_envelope_without_data_is_contract_violation():
    async with _make_client(lambda r: _json_response({"code": 0, "message": "ok"})) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "CONTRACT_VIOLATION"
    assert result.severity == "critical"


async def test_retrieved_field_missing_is_contract_violation():
    data = {k: v for k, v in _empty_data(retrieved=True).items() if k != "retrieved"}

    async with _make_client(lambda r: _json_response(_envelope(data))) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "CONTRACT_VIOLATION"
    assert result.severity == "critical"


async def test_evidence_missing_required_field_is_contract_violation():
    bad = {k: v for k, v in _OK_ENVELOPE["data"]["evidence"][0].items() if k != "source_title"}
    data = _empty_data(retrieved=True, total=1, evidence=[bad])

    async with _make_client(lambda r: _json_response(_envelope(data))) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "CONTRACT_VIOLATION"
    assert result.severity == "critical"
    assert "source_title" in (result.error_message or "")


async def test_tenant_mismatch_is_error():
    data = _empty_data(tenant_id="t_other_shop")

    async with _make_client(lambda r: _json_response(_envelope(data))) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "TENANT_MISMATCH"
    assert result.severity == "error"


async def test_connection_error_and_timeout():
    async with _make_client(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))) as client:
        result = await client.retrieve(TENANT, QUERY)
    assert result.error_code == "CONNECTION_ERROR"
    assert result.severity == "error"

    async with _make_client(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))) as client:
        result = await client.retrieve(TENANT, QUERY)
    assert result.error_code == "CLIENT_TIMEOUT"
    assert result.severity == "error"


async def test_retry_recovers_from_transient_failure():
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("first try fails")
        return _json_response(_OK_ENVELOPE)

    async with _make_client(flaky, retries=2) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.status is RetrievalStatus.OK
    assert calls["n"] == 2


async def test_no_retry_by_default():
    calls = {"n": 0}

    def broken(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("always fails")

    async with _make_client(broken) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "CONNECTION_ERROR"
    assert calls["n"] == 1  # 默认 retrieve_max_retries=0


async def test_auth_error_not_retried_even_when_enabled():
    """鉴权错误不会自愈：重试开关开启也不得重试。"""
    calls = {"n": 0}

    def unauthorized(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _json_response({"code": 40101, "message": "密钥不正确", "data": None}, 401)

    async with _make_client(unauthorized, retries=3) as client:
        result = await client.retrieve(TENANT, QUERY)

    assert result.error_code == "AUTH_KEY_INVALID"
    assert calls["n"] == 1
