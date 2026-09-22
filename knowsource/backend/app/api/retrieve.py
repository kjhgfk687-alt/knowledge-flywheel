"""POST /api/v1/retrieve —— 契约 §1-§6 唯一对外检索端点（客舟调用）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import ok
from app.core.security import require_api_key
from app.infra.db import get_db
from app.schemas.retrieve import RetrieveData, RetrieveRequest
from app.services.retrieval import retrieve_documents

router = APIRouter(prefix="/api/v1", tags=["retrieve"])


@router.post("/retrieve")
def retrieve(
    req: RetrieveRequest,
    _api_key: str = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    """契约端点：成功 code=0 + data；参数错误 422 族；降级仍在 code=0 内以 degraded=true 标注。"""
    data = retrieve_documents(db, req)
    payload = RetrieveData.model_validate(data)
    return ok(payload.model_dump(mode="json"))
