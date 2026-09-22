"""阶段五测试：飞轮指标端点 + 知源案例库代理（hit_count 排序）。"""

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.retrieval.case_client import ZhiyuanCaseWritebackClient

TENANT = "t_bagshop_001"


def _settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "s5.sqlite"), llm_provider="mock", retrieve_mock_scenario="empty")


def test_metrics_endpoint_aggregates_flywheel(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        for sid, msg in [("s_m1", "这个商品到底是不是真皮的啊"), ("s_m2", "你好，你是谁呀"), ("s_m3", "查下订单ORD1001")]:
            resp = client.post("/api/v1/chat", json={"tenant_id": TENANT, "user_id": "u1", "session_id": sid, "message": msg})
            assert resp.status_code == 200

        m = client.get("/api/v1/metrics", params={"tenant_id": TENANT}).json()
        assert m["turns"] == 3
        assert m["transfers"] == 1
        assert m["transfer_rate"] > 0
        assert m["transfer_reasons"].get("retrieval_empty") == 1
        assert m["handoff_status"].get("pending") == 1
        assert m["writeback_status"].get("none") == 1
        assert m["approval_status"] == {}
        # dryrun 模式案例库代理返回空但成功
        assert m["zhiyuan_cases_ok"] is True
        assert m["zhiyuan_cases"] == []


def test_zhiyuan_case_list_proxy_sorts_by_hit_count():
    """案例库代理：按 hit_count 降序（飞轮核心指标：命中越多沉淀越有效）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["tenant_id"] == TENANT
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": [
            {"case_id": "case_a", "original_query": "q1", "review_result": "r1", "status": "confirmed", "hit_count": 3},
            {"case_id": "case_b", "original_query": "q2", "review_result": "r2", "status": "pending", "hit_count": 9},
            {"case_id": "case_c", "original_query": "q3", "review_result": "r3", "status": "dismissed", "hit_count": 1},
        ]})

    client = ZhiyuanCaseWritebackClient(
        Settings(zhiyuan_base_url="http://zhiyuan-test"),
        transport=httpx.MockTransport(handler),
    )
    import asyncio
    result = asyncio.run(client.list_cases(TENANT))

    assert result.ok is True
    assert [c["case_id"] for c in result.cases] == ["case_b", "case_a", "case_c"]


def test_zhiyuan_case_list_proxy_error_mapping():
    client = ZhiyuanCaseWritebackClient(
        Settings(zhiyuan_base_url="http://zhiyuan-test"),
        transport=httpx.MockTransport(lambda r: httpx.Response(422, json={"code": 40001, "message": "tenant_id 格式非法", "data": None})),
    )
    import asyncio
    result = asyncio.run(client.list_cases("BAD!"))
    assert result.ok is False
    assert result.error_code == "TENANT_ID_INVALID"
