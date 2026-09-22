"""知源前端控制台内部 API：文档上传 / 列表 / 详情。不属于契约，OpenAPI 自述。

阶段二起支持双层：
- 商户层（layer=merchant）：必须归属某个租户，租户隔离；
- 平台层（layer=platform）：共享知识，归属保留租户 platform，无需租户上下文。
阶段一 dev 模式不做控制台鉴权（设计 §3 边界约定，阶段三加管理员登录收口）。
"""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.errors import (
    QUERY_INVALID,
    TENANT_ID_INVALID,
    api_error,
    ok,
)
from app.infra.db import get_db
from app.infra.models import PLATFORM_TENANT_ID, Chunk, Document
from app.schemas.document import ChunkPreview, DocumentDetail, DocumentOut
from app.services.ingest import ingest_document
from app.services.tenants import get_tenant_or_raise

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

ALLOWED_TYPES = {"pdf": ".pdf", "md": ".md", "txt": ".txt"}
SUFFIX_TO_TYPE = {v: k for k, v in ALLOWED_TYPES.items()}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_TENANT_PATTERN = r"^[a-z0-9][a-z0-9_]{1,31}$"
_LAYERS = {"merchant", "platform"}


def _validate_tenant_id(tenant_id: str) -> str:
    if not re.fullmatch(_TENANT_PATTERN, tenant_id or ""):
        raise api_error(TENANT_ID_INVALID, f"tenant_id 格式非法: 期望 {_TENANT_PATTERN}")
    return tenant_id


@router.post("/upload")
async def upload_document(
    tenant_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    category: str = Form(default=""),
    layer: str = Form(default="merchant"),
    db: Session = Depends(get_db),
):
    if layer not in _LAYERS:
        raise api_error(QUERY_INVALID, f"layer 非法: {layer}，仅支持 merchant/platform")

    if layer == "merchant":
        _validate_tenant_id(tenant_id or "")
        get_tenant_or_raise(db, tenant_id)
    else:
        tenant_id = PLATFORM_TENANT_ID  # 平台层归属保留租户，商户无需上下文

    suffix = Path(file.filename or "").suffix.lower()
    file_type = SUFFIX_TO_TYPE.get(suffix)
    if file_type is None:
        raise api_error(QUERY_INVALID, f"不支持的文件类型: {suffix or '未知'}，仅支持 pdf/md/txt")

    raw = await file.read()
    if not raw:
        raise api_error(QUERY_INVALID, "上传文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise api_error(QUERY_INVALID, "文件超过 10MB 上限")

    document = Document(
        tenant_id=tenant_id,
        layer=layer,
        title=(title or Path(file.filename or "未命名").stem)[:256],
        file_name=file.filename or "未命名",
        file_type=file_type,
        category=category[:64],
        status="uploaded",
    )
    db.add(document)
    db.commit()

    from app.infra.embedder import get_embedder

    try:
        ingest_document(db, document, raw, get_embedder())
    except Exception as exc:  # noqa: BLE001 —— failed 状态已落库，向前端返回明确错误
        return ok(DocumentOut.model_validate(document).model_dump(mode="json"), f"文档摄取失败: {exc}")

    return ok(DocumentOut.model_validate(document).model_dump(mode="json"))


@router.get("")
def list_documents(tenant_id: str | None = None, layer: str = "merchant", db: Session = Depends(get_db)):
    if layer not in _LAYERS:
        raise api_error(QUERY_INVALID, f"layer 非法: {layer}，仅支持 merchant/platform")
    q = db.query(Document).filter(Document.layer == layer)
    if layer == "merchant":
        _validate_tenant_id(tenant_id or "")
        get_tenant_or_raise(db, tenant_id)
        q = q.filter(Document.tenant_id == tenant_id)
    docs = q.order_by(Document.id.desc()).all()
    return ok([DocumentOut.model_validate(d).model_dump(mode="json") for d in docs])


@router.get("/{document_id}")
def get_document(document_id: int, tenant_id: str | None = None, layer: str = "merchant", db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).one_or_none()
    if document is None:
        raise api_error(404, 40400, "NOT_FOUND", f"文档不存在: {document_id}")
    if document.layer == "merchant":
        _validate_tenant_id(tenant_id or "")
        get_tenant_or_raise(db, tenant_id)
        if document.tenant_id != tenant_id:
            raise api_error(404, 40400, "NOT_FOUND", f"文档不存在于该租户: {document_id}")

    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.chunk_index)
        .limit(50)
        .all()
    )
    detail = DocumentDetail(
        **DocumentOut.model_validate(document).model_dump(),
        chunks=[
            ChunkPreview(
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                content=c.content[:300],
            )
            for c in chunks
        ],
    )
    return ok(detail.model_dump(mode="json"))
