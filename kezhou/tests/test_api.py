"""API 冒烟测试：真实 Runtime（AsyncSqliteSaver + 临时库）走 HTTP 层。"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(tmp_path, scenario: str = "ok"):
    settings = Settings(
        db_path=str(tmp_path / "api.sqlite"),
        retrieve_mock_scenario=scenario,
        llm_provider="mock",
    )
    return TestClient(create_app(settings))


def test_chat_happy_path(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/chat",
            json={"tenant_id": "t_bagshop_001", "user_id": "u1", "message": "客户说买的包五金掉色怀疑是假货怎么处理？"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["need_human"] is False
    assert "[1]" in body["reply_text"]
    assert body["citations"][0]["from_case"] is True
    assert body["query_id"].startswith("ret_")
    assert body["session_id"].startswith("s_")


def test_chat_invalid_tenant_rejected(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/v1/chat",
            json={"tenant_id": "BAD TENANT!", "message": "你好"},
        )
    assert resp.status_code == 422


def test_healthz(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "kezhou"
