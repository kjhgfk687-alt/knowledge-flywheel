"""测试环境：独立测试库 + 独立 Chroma 目录 + hash 嵌入，全部在导入 app 前注入环境变量。

测试库按需重建（knowsource_test），不动开发库 knowsource。
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATABASE_URL"] = "postgresql+psycopg://ks:ks@localhost:5432/knowsource_test"
os.environ["CHROMA_DIR"] = str(Path(__file__).resolve().parent / "chroma-test")
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["KS_API_KEY"] = "test-key"
os.environ["MIN_SCORE"] = "0.05"

import psycopg
import pytest

ADMIN_DSN = "host=localhost port=5432 dbname=postgres user=ks password=ks"


def _recreate_test_db() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS knowsource_test")
        conn.execute("CREATE DATABASE knowsource_test")


def _reset_chroma() -> None:
    shutil.rmtree(os.environ["CHROMA_DIR"], ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _database():
    _recreate_test_db()
    _reset_chroma()

    from app.infra.db import Base, get_engine, get_session_factory
    from app.infra.models import PLATFORM_TENANT_ID, Tenant  # 确保全部模型注册进 metadata

    Base.metadata.create_all(bind=get_engine())
    db = get_session_factory()()
    db.add_all(
        [
            Tenant(tenant_id="t_bagshop_001", name="皮具旗舰店", status="active"),
            Tenant(tenant_id="t_watchshop_002", name="名表专营店", status="active"),
            Tenant(tenant_id="t_disabled_9", name="禁用租户", status="disabled"),
            Tenant(tenant_id=PLATFORM_TENANT_ID, name="平台层（共享）", status="active"),
        ]
    )
    db.commit()
    db.close()
    yield
    _reset_chroma()


@pytest.fixture
def db():
    from app.infra.db import get_session_factory

    session = get_session_factory()()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.infra.db import get_db, get_session_factory
    from app.main import app

    def _override_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


AUTH = {"X-API-Key": "test-key"}
