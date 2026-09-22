"""检索客户端层：types 为已确认的接口契约（三态结果对象），mock 为阶段一实现。

阶段二用 ZhiyuanRetrievalClient（REST 调知源 /retrieve）替换 mock，
节点层零改动——依赖注入时换实例即可。
"""

from app.retrieval.types import (
    Evidence,
    RetrievalClient,
    RetrievalResult,
    RetrievalStatus,
)

__all__ = ["Evidence", "RetrievalClient", "RetrievalResult", "RetrievalStatus"]
