"""知源控制台文档管理 DTO（非契约，OpenAPI 自述）。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    layer: str
    title: str
    file_name: str
    file_type: str
    category: str = ""
    status: str
    error_msg: str | None = None
    chunk_count: int
    created_at: datetime


class ChunkPreview(BaseModel):
    chunk_id: str
    chunk_index: int
    content: str  # 截断到 300 字符，仅供预览；完整正文以检索返回为准


class DocumentDetail(DocumentOut):
    chunks: list[ChunkPreview]
