"""种子数据：两个演示租户。幂等（已存在则跳过）。示例文档通过上传接口注入（M4 后）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infra.db import Base, get_engine, get_session_factory
from app.infra.models import PLATFORM_TENANT_ID, Tenant

SEED_TENANTS = [
    ("t_bagshop_001", "皮具旗舰店（演示）"),
    ("t_watchshop_002", "名表专营店（演示）"),
    (PLATFORM_TENANT_ID, "平台层（共享政策，保留租户）"),
]


def main() -> None:
    Base.metadata.create_all(bind=get_engine())  # 兜底：未跑 alembic 时也能建表
    db = get_session_factory()()
    try:
        for tenant_id, name in SEED_TENANTS:
            exists = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).one_or_none()
            if exists:
                print(f"skip (exists): {tenant_id}")
                continue
            db.add(Tenant(tenant_id=tenant_id, name=name, status="active"))
            print(f"seeded: {tenant_id} - {name}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
