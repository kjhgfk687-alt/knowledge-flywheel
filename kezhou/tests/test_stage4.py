"""阶段四测试：审批队列、转人工审核、案例回写（飞轮闭环）与候选策略。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.review_policy import is_writeback_candidate
from app.retrieval.case_client import (
    DryRunCaseWritebackClient,
    FailingCaseWritebackClient,
    ZhiyuanCaseWritebackClient,
)

TENANT = "t_bagshop_001"


def _settings(tmp_path, **kw) -> Settings:
    return Settings(db_path=str(tmp_path / "s4.sqlite"), llm_provider="mock", **kw)


def _chat(client: TestClient, message: str, session_id: str) -> dict:
    resp = client.post(
        "/api/v1/chat",
        json={"tenant_id": TENANT, "user_id": "u1", "session_id": session_id, "message": message},
    )
    assert resp.status_code == 200
    return resp.json()


# ---- 候选策略（决策点④）----

def test_candidate_policy():
    assert is_writeback_candidate("retrieval_empty")
    assert is_writeback_candidate("low_confidence")
    assert is_writeback_candidate("intent_low_confidence+retrieval_empty")  # 组合原因取末段
    assert not is_writeback_candidate("risk_hit")
    assert not is_writeback_candidate("emotion_high")
    assert not is_writeback_candidate("retrieval_error")
    assert not is_writeback_candidate("tool_error")
    assert not is_writeback_candidate(None)


# ---- 审批队列 ----

def test_approval_flow_end_to_end(tmp_path):
    """中间地带退货 → create_approval 落队 → 列表 → 批准 → 重复审核 409。"""
    dryrun_writeback = DryRunCaseWritebackClient()
    with TestClient(create_app(_settings(tmp_path), case_writeback=dryrun_writeback)) as client:
        body = _chat(client, "我要退款，订单ORD1004", "s_ap1")  # ¥8800 已鉴定 → approval
        assert body["risk_outcome"] == "approval"
        assert body["approval_request_id"]
        assert "审批单号" in body["reply_text"]

        listed = client.get("/api/v1/approvals", params={"status": "pending"}).json()["approvals"]
        target = next(a for a in listed if a["id"] == body["approval_request_id"])
        assert target["order_id"] == "ORD1004"
        assert target["amount"] == 8800.0
        assert target["risk_factors"]

        ok_review = client.post(
            f"/api/v1/approvals/{body['approval_request_id']}/review",
            json={"action": "approve", "note": "凭证齐全，同意退货"},
        )
        assert ok_review.status_code == 200
        assert ok_review.json()["approval"]["status"] == "approved"

        dup = client.post(f"/api/v1/approvals/{body['approval_request_id']}/review", json={"action": "reject"})
        assert dup.status_code == 409

        missing = client.post("/api/v1/approvals/ap_nope/review", json={"action": "approve"})
        assert missing.status_code == 404

        bad = client.post(f"/api/v1/approvals/{body['approval_request_id']}/review", json={"action": "maybe"})
        assert bad.status_code == 422


def test_dispute_refund_still_forces_human_not_approval(tmp_path):
    """争议类是强制转人工（risk_hit），不进审批队列。"""
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = _chat(client, "我要退款，这包怀疑是假货", "s_dp1")
        assert body["risk_outcome"] == "force_human"
        assert body["need_human"] is True
        assert body["approval_request_id"] is None
        assert client.get("/api/v1/approvals", params={"status": "pending"}).json()["approvals"] == []


# ---- 转人工审核 + 案例回写（飞轮闭环）----

def test_handoff_review_writes_back_candidate_case(tmp_path):
    """retrieval_empty 的转人工记录审核后回写知源：query_id 串联检索→转人工→审核理由。"""
    wb = DryRunCaseWritebackClient()
    with TestClient(create_app(_settings(tmp_path, retrieve_mock_scenario="empty"), case_writeback=wb)) as client:
        body = _chat(client, "这个商品到底是不是真皮的啊", "s_h1")
        assert body["need_human"] is True
        assert body["transfer_reason"] == "retrieval_empty"
        handoff_id = client.get("/api/v1/handoffs", params={"session_id": "s_h1"}).json()["handoffs"][0]["id"]

        review = client.post(
            f"/api/v1/handoffs/{handoff_id}/review",
            json={"review_result": "经核实为真皮，已向客户解释材质工艺并附检测说明"},
        )
        assert review.status_code == 200
        data = review.json()
        assert data["record"]["status"] == "reviewed"
        assert data["writeback"]["status"] == "success"
        assert data["writeback"]["case_id"].startswith("case_")

        # 飞轮链路字段完整：query_id 是当次 /retrieve 的返回值
        assert len(wb.calls) == 1
        call = wb.calls[0]
        assert call["query_id"] == body["query_id"] and call["query_id"].startswith("ret_")
        assert call["tenant_id"] == TENANT
        assert call["original_query"] == "这个商品到底是不是真皮的啊"
        assert "真皮" in call["review_result"]

        # 记录落库 + 重复审核 409
        assert data["record"]["writeback_status"] == "success"
        dup = client.post(f"/api/v1/handoffs/{handoff_id}/review", json={"review_result": "x"})
        assert dup.status_code == 409


def test_handoff_review_skips_non_candidate(tmp_path):
    """emotion_high 不是知识盲区：审核后不回写，避免污染案例库。"""
    wb = DryRunCaseWritebackClient()
    with TestClient(create_app(_settings(tmp_path), case_writeback=wb)) as client:
        body = _chat(client, "东西是垃圾！我要投诉你们！", "s_h2")
        assert body["transfer_reason"] == "emotion_high"
        handoff_id = client.get("/api/v1/handoffs", params={"session_id": "s_h2"}).json()["handoffs"][0]["id"]

        review = client.post(f"/api/v1/handoffs/{handoff_id}/review", json={"review_result": "已安抚并处理"})
        assert review.status_code == 200
        assert review.json()["writeback"]["status"] == "skipped"
        assert wb.calls == []
        assert review.json()["record"]["writeback_status"] == "none"


def test_handoff_writeback_failure_is_recorded_and_retryable(tmp_path):
    """回写失败不丢结论：记录 failed + 可重试。"""
    wb = FailingCaseWritebackClient(error_code="CONNECTION_ERROR", message="知源不可达")
    with TestClient(create_app(_settings(tmp_path, retrieve_mock_scenario="empty"), case_writeback=wb)) as client:
        _chat(client, "这个商品到底是不是真皮的啊", "s_h3")
        handoff_id = client.get("/api/v1/handoffs", params={"session_id": "s_h3"}).json()["handoffs"][0]["id"]

        review = client.post(f"/api/v1/handoffs/{handoff_id}/review", json={"review_result": "已核实并答复客户"})
        assert review.status_code == 200
        assert review.json()["writeback"]["status"] == "failed"
        assert review.json()["writeback"]["error_code"] == "CONNECTION_ERROR"
        assert client.get("/api/v1/handoffs", params={"session_id": "s_h3"}).json()["handoffs"][0]["writeback_status"] == "failed"

        retry = client.post(f"/api/v1/handoffs/{handoff_id}/writeback")
        assert retry.status_code == 200
        assert retry.json()["status"] == "failed"  # 仍失败但可重试，结论不丢

        # 未审核的记录不能直接回写
        _chat(client, "完全查不到这个的东西啊", "s_h4")
        pending_id = client.get("/api/v1/handoffs", params={"session_id": "s_h4"}).json()["handoffs"][0]["id"]
        early = client.post(f"/api/v1/handoffs/{pending_id}/writeback")
        assert early.status_code == 409


# ---- 回写客户端单元测试（契约 v1.3 §7 响应形态）----

def _wb_client(handler) -> ZhiyuanCaseWritebackClient:
    return ZhiyuanCaseWritebackClient(
        Settings(zhiyuan_base_url="http://zhiyuan-test"),
        transport=httpx.MockTransport(handler),
    )


async def test_writeback_client_success_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "code": 0, "message": "案例已接收，待确认后进入案例知识库",
            "data": {"case_id": "case_541ec359b457", "status": "pending", "hit_count": 0},
        })

    async with _wb_client(handler) as client:
        result = await client.write_back(
            tenant_id=TENANT, original_query="这个商品到底是不是真皮的啊",
            review_result="经核实为真皮", query_id="ret_abc",
        )
    assert result.ok is True
    assert result.case_id == "case_541ec359b457"


async def test_writeback_client_error_mapping():
    cases = [
        ({"code": 40002, "message": "review_result 为空", "data": None}, 422, "QUERY_INVALID", "error"),
        ({"code": 40101, "message": "密钥不正确", "data": None}, 401, "AUTH_KEY_INVALID", "critical"),
        ({"code": 40401, "message": "租户不存在", "data": None}, 404, "TENANT_NOT_FOUND", "error"),
    ]
    for envelope, status, want_code, want_severity in cases:
        async with _wb_client(lambda r, e=envelope, s=status: httpx.Response(s, json=e)) as client:
            result = await client.write_back(tenant_id=TENANT, original_query="q", review_result="r")
        assert result.ok is False
        assert result.error_code == want_code
        assert result.severity == want_severity


async def test_writeback_client_contract_violation_is_critical():
    async with _wb_client(lambda r: httpx.Response(200, text="<html>bad gateway</html>")) as client:
        result = await client.write_back(tenant_id=TENANT, original_query="q", review_result="r")
    assert result.error_code == "CONTRACT_VIOLATION"
    assert result.severity == "critical"
