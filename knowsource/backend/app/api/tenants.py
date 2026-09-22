"""控制台辅助 API：租户列表（前端租户切换器用）。非契约。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.models import Tenant

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.get("")
def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).order_by(Tenant.id).all()
    return {"code": 0, "message": "ok", "data": [{"tenant_id": t.tenant_id, "name": t.name, "status": t.status} for t in tenants]}
