"""案例知识 API（契约 v1.3 新增回写端点 + 控制台管理端点）。

- POST /api/v1/cases              契约回写端点：客舟/审核工作台把人工审核结论写给知源（pending）
- GET  /api/v1/cases              控制台：案例列表（按租户/状态过滤）
- POST /api/v1/cases/{id}/confirm 控制台：人工确认入库（可修订审核理由/品类），确认后可被检索
- POST /api/v1/cases/{id}/dismiss 控制台：驳回，不入库
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.errors import (
    QUERY_INVALID,
    TENANT_ID_INVALID,
    api_error,
    ok,
)
from app.core.ids import new_case_id
from app.core.security import require_api_key
from app.infra.db import get_db
from app.infra.models import Case
from app.services.cases import confirm_and_index
from app.services.tenants import get_tenant_or_raise

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])

_TENANT_PATTERN = r"^[a-z0-9][a-z0-9_]{1,31}$"
MAX_TEXT = 2000


class CaseWriteBack(BaseModel):
    """契约 v1.3 §7 回写请求体。"""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=_TENANT_PATTERN, min_length=2, max_length=32)
    original_query: str
    review_result: str
    query_id: str | None = None
    category: str = ""
    feedback_source: str = "kezhou"

    @field_validator("original_query", "review_result")
    @classmethod
    def check_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        if len(v) > MAX_TEXT:
            raise ValueError(f"最长 {MAX_TEXT} 字符")
        return v

    @field_validator("query_id")
    @classmethod
    def check_query_id(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("ret_"):
            raise ValueError("query_id 须为 /retrieve 返回的 ret_ 前缀 ID")
        return v


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    tenant_id: str
    query_id: str | None
    original_query: str
    review_result: str
    category: str
    status: str
    hit_count: int
    feedback_source: str
    created_at: object
    confirmed_at: object | None


class CaseConfirmBody(BaseModel):
    """人工确认时可修订审核理由与品类（半自动链路的"确认"环节）。"""

    model_config = ConfigDict(extra="forbid")

    review_result: str | None = None
    category: str | None = None


@router.post("")
def write_back_case(body: CaseWriteBack, _api_key: str = Depends(require_api_key), db: Session = Depends(get_db)):
    """契约回写端点：创建 pending 案例，等待管理员确认后才会进入检索。"""
    get_tenant_or_raise(db, body.tenant_id)
    case = Case(
        case_id=new_case_id(),
        tenant_id=body.tenant_id,
        query_id=body.query_id,
        original_query=body.original_query,
        review_result=body.review_result,
        category=body.category[:64],
        status="pending",
        feedback_source=body.feedback_source[:32] or "kezhou",
    )
    db.add(case)
    db.commit()
    data = CaseOut.model_validate(case).model_dump(mode="json")
    return ok(data, message="案例已接收，待确认后进入案例知识库")


@router.get("")
def list_cases(tenant_id: str, status: str | None = None, db: Session = Depends(get_db)):
    import re

    if not re.fullmatch(_TENANT_PATTERN, tenant_id or ""):
        raise api_error(TENANT_ID_INVALID, f"tenant_id 格式非法: 期望 {_TENANT_PATTERN}")
    get_tenant_or_raise(db, tenant_id)
    q = db.query(Case).filter(Case.tenant_id == tenant_id)
    if status:
        if status not in ("pending", "confirmed", "dismissed"):
            raise api_error(QUERY_INVALID, "status 仅支持 pending/confirmed/dismissed")
        q = q.filter(Case.status == status)
    cases = q.order_by(Case.id.desc()).limit(100).all()
    return ok([CaseOut.model_validate(c).model_dump(mode="json") for c in cases])


@router.post("/{case_id}/confirm")
def confirm_case(case_id: str, body: CaseConfirmBody | None = None, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if case is None:
        raise api_error(404, 40400, "NOT_FOUND", f"案例不存在: {case_id}")
    if case.status == "dismissed":
        raise api_error(QUERY_INVALID, "案例已驳回，不能确认入库")
    case = confirm_and_index(
        db, case,
        review_result=body.review_result if body else None,
        category=body.category if body else None,
    )
    return ok(CaseOut.model_validate(case).model_dump(mode="json"))


@router.post("/{case_id}/dismiss")
def dismiss_case(case_id: str, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if case is None:
        raise api_error(404, 40400, "NOT_FOUND", f"案例不存在: {case_id}")
    if case.status == "confirmed":
        raise api_error(QUERY_INVALID, "案例已入库，不能驳回（如需下线请在阶段四扩展案例停用）")
    case.status = "dismissed"
    db.commit()
    return ok(CaseOut.model_validate(case).model_dump(mode="json"))
