"""契约 §2/§3 请求与响应 DTO。字段定义与《知源retrieve接口契约 v1.0》一一对应，勿单独改动。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings

DegradeReason = Literal["vector_store_unavailable", "embedding_failed", "vector_timeout"]
SourceType = Literal["platform", "merchant", "case"]


class RetrieveFilters(BaseModel):
    """契约 §2：filters 只允许两个键；null 与缺省等价；空数组非法。"""

    model_config = ConfigDict(extra="forbid")

    category: list[str] | None = None
    auth_status: list[Literal["authentic", "fake", "unknown"]] | None = None

    @field_validator("category")
    @classmethod
    def check_category(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("空数组视为非法（与不过滤语义冲突），请传 null 或省略")
        if len(v) > 10:
            raise ValueError("最多 10 个品类")
        if any(not c.strip() for c in v):
            raise ValueError("品类元素不能为空字符串")
        return v

    @field_validator("auth_status")
    @classmethod
    def check_auth_status(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("空数组视为非法（与不过滤语义冲突），请传 null 或省略")
        return v


class RetrieveRequest(BaseModel):
    """契约 §2 请求体。校验失败经 errors.py 映射为 40001-40005。"""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{1,31}$", min_length=2, max_length=32)
    query: str
    top_k: int = Field(default=5, ge=1)
    filters: RetrieveFilters | None = None
    include_platform: bool = True
    include_case: bool = True

    @field_validator("query")
    @classmethod
    def check_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query 去首尾空白后不能为空")
        if len(v) > 512:
            raise ValueError("query 最长 512 字符")
        return v

    @field_validator("top_k")
    @classmethod
    def check_top_k(cls, v: int) -> int:
        max_k = get_settings().top_k_max
        if v > max_k:
            raise ValueError(f"top_k 最大 {max_k}")
        return v


class EvidenceOut(BaseModel):
    """契约 §3.3 Evidence 单条结构。"""

    chunk_id: str
    content: str
    source_type: SourceType
    source_title: str
    score: float
    metadata: dict


class RetrieveData(BaseModel):
    """契约 §3.2 成功响应 data 结构。"""

    query_id: str
    tenant_id: str
    retrieved: bool
    degraded: bool
    degrade_reason: DegradeReason | None = None
    total: int
    evidence: list[EvidenceOut]
    latency_ms: int
