"""ORM 模型：tenants / documents / chunks。

设计要点：
- documents.layer 与 chunks.layer 阶段一只用 "merchant"，字段现在就建（阶段二平台层零迁移）。
- chunks.content_fts：摄取时用 jieba 预分词后 to_tsvector('simple', ...) 写入，
  GIN 索引支撑契约 §5 的关键词降级路径（应用层分词，避开 Windows 装 pg_jieba）。
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base

# 平台层文档归属的保留租户：所有商户共享，检索时按 layer=platform 识别。
# /retrieve 拒绝以该 ID 作为 tenant_id 调用（契约 v1.2 保留值）。
PLATFORM_TENANT_ID = "platform"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), index=True)
    layer: Mapped[str] = mapped_column(String(16), default="merchant")  # merchant | platform
    title: Mapped[str] = mapped_column(String(256))
    file_name: Mapped[str] = mapped_column(String(256))
    file_type: Mapped[str] = mapped_column(String(16))  # pdf | md | txt
    category: Mapped[str] = mapped_column(String(64), default="")  # 商品品类；空串=未分类（契约 filters.category）
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded/parsing/indexed/failed
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", order_by="Chunk.chunk_index")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    layer: Mapped[str] = mapped_column(String(16), default="merchant")
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_fts: Mapped[str] = mapped_column(TSVECTOR)
    meta: Mapped[dict] = mapped_column("meta", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_tenant_layer", "tenant_id", "layer"),
        Index("ix_chunks_fts", "content_fts", postgresql_using="gin"),
    )


class Case(Base):
    """案例知识（飞轮沉淀）：回写后 pending，管理员确认后索引进检索（半自动链路）。

    - query_id 关联触发该案例的原始检索（契约 §3.2，链路追踪键，知源不校验存在性）；
    - confirmed 时生成一个 chunk（layer=merchant + is_case 元数据），可被检索命中；
    - hit_count 每次被检索命中 +1（飞轮核心指标，返回值为命中后的最新值）。
    """

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), index=True)
    query_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_query: Mapped[str] = mapped_column(Text)
    review_result: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | dismissed
    chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    feedback_source: Mapped[str] = mapped_column(String(32), default="kezhou")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
