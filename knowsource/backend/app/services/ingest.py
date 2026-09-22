"""摄取管道：解析 → 切分 → 向量化 → PG + Chroma 双写。

状态机（documents.status）：uploaded → parsing → indexed | failed(error_msg)。
PG 是事实源；Chroma 只存向量与检索用元数据，索引损坏可从 PG 重建。
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import new_chunk_id
from app.infra.embedder import EmbeddingProvider
from app.infra.models import Chunk, Document
from app.infra.vector import get_vector_store

jieba.setLogLevel(60)  # 关闭分词日志噪音


def parse_file(file_type: str, raw: bytes) -> str:
    """按文件类型抽取纯文本。不支持的类型在端点层已拦截。"""
    if file_type == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8", errors="replace")


def segment_for_fts(text: str) -> str:
    """jieba 预分词 + 空格连接，供 to_tsvector('simple', ...) 使用。"""
    return " ".join(tok for tok in jieba.cut(text) if tok.strip())


def _split(text: str) -> list[str]:
    s = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=s.chunk_size,
        chunk_overlap=s.chunk_overlap,
        separators=["\n\n", "\n", "！", "？", "；", "，", " ", ""],
        keep_separator=False,
    )
    return [p.strip() for p in splitter.split_text(text) if p.strip()]


def ingest_document(db: Session, document: Document, raw: bytes, embedder: EmbeddingProvider) -> Document:
    """对已创建（status=uploaded）的 Document 执行完整摄取。任何失败落 failed 状态并抛出。"""
    try:
        document.status = "parsing"
        db.commit()

        text = parse_file(document.file_type, raw)
        if not text.strip():
            raise ValueError("解析后无有效文本（扫描版 PDF 或空文件）")

        pieces = _split(text)
        if not pieces:
            raise ValueError("切分后无有效切片")
        now_iso = datetime.now(timezone.utc).isoformat()

        embeddings = embedder.embed(pieces)

        chunk_ids: list[str] = []
        metadatas: list[dict] = []
        for i, piece in enumerate(pieces):
            chunk_id = new_chunk_id()
            chunk_ids.append(chunk_id)
            db.add(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    layer=document.layer,
                    chunk_index=i,
                    content=piece,
                    content_fts=func.to_tsvector("simple", segment_for_fts(piece)),
                    meta={
                        "doc_title": document.title,
                        "category": document.category,
                        # 普通文档切片的案例标识（案例切片由 services/cases.py 写入）
                        "is_case": False,
                        "case_id": "",
                    },
                )
            )
            metadatas.append(
                {
                    # Chroma 元数据值必须是 str/int/float/bool，None 会报错；
                    # is_case/case_id 全量写入，支撑 include_case=false 的过滤条件
                    "tenant_id": document.tenant_id,
                    "layer": document.layer,
                    "document_id": document.id,
                    "doc_title": document.title,
                    "chunk_index": i,
                    "created_at": now_iso,
                    "category": document.category,
                    "is_case": False,
                    "case_id": "",
                }
            )
        db.commit()

        get_vector_store().upsert(
            chunk_ids=chunk_ids,
            embeddings=embeddings,
            documents=pieces,
            metadatas=metadatas,
        )

        document.chunk_count = len(pieces)
        document.status = "indexed"
        document.error_msg = None
        db.commit()
        return document
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要显式落库为 failed
        db.rollback()
        document.status = "failed"
        document.error_msg = str(exc)[:500]
        db.commit()
        raise
