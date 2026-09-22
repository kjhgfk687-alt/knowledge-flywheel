"""租户校验：格式合法但不存在 → 40401；被禁用 → 40301（契约 §4）。"""
from sqlalchemy.orm import Session

from app.core.errors import TENANT_DISABLED, TENANT_NOT_FOUND, api_error
from app.infra.models import Tenant


def get_tenant_or_raise(db: Session, tenant_id: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).one_or_none()
    if tenant is None:
        raise api_error(TENANT_NOT_FOUND, f"租户不存在: {tenant_id}")
    if tenant.status != "active":
        raise api_error(TENANT_DISABLED, f"租户已被禁用: {tenant_id}")
    return tenant
