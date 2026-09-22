"""检索结果三态类型：2026-09-22 已确认的 retrieval_client 接口设计。

对齐知源契约 v1.0：
- 检索为空是 200 + retrieved=false，不是错误 → EMPTY 状态，不凭 evidence==[] 判断；
- degraded=true 时下游应下调置信度（契约第 5 节客舟侧消费约定）；
- severity 分级（用户确认的修订①）：CRITICAL = 鉴权错误/契约违约，不得静默混入普通 ERROR。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol

Severity = Literal["info", "warn", "error", "critical"]


class RetrievalStatus(str, Enum):
    OK = "ok"        # retrieved=true，证据非空
    EMPTY = "empty"  # retrieved=false，检索正常执行但无证据（不是错误）
    ERROR = "error"  # 网络/超时/契约错误，检索没有成功


@dataclass(frozen=True)
class Evidence:
    """单条证据：source_type / source_title 必须保留，供引用溯源（契约 3.3）。"""

    chunk_id: str
    content: str
    source_type: str  # platform / merchant / case
    source_title: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    status: RetrievalStatus
    query_id: str | None = None          # 飞轮链路关联键；未拿到响应时为 None
    evidence: list[Evidence] = field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None    # vector_store_unavailable / embedding_failed / vector_timeout
    error_code: str | None = None        # 契约错误标识（如 RETRIEVAL_TIMEOUT）或客户端本地码
    error_message: str | None = None
    severity: Severity = "info"
    latency_ms: int | None = None


class RetrievalClient(Protocol):
    """知源检索客户端统一接口：mock 与真实实现都遵守，Retrieve 节点只认这个协议。"""

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        include_platform: bool = True,
        include_case: bool = True,
    ) -> RetrievalResult: ...
