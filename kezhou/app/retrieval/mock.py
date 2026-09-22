"""阶段一 Mock 检索客户端：与真实 client 同接口，按场景开关模拟知源的各种响应。

场景由 KEZHOU_RETRIEVE_MOCK_SCENARIO 控制：
  ok       正常返回三条证据（case/merchant/platform，模拟契约 6.2）
  empty    检索正常执行但无证据（retrieved=false，模拟契约 6.3）
  degraded 向量故障走关键词降级（degraded=true，模拟契约 6.4）
  error    知源返回 503 RETRIEVAL_UNAVAILABLE 错误信封
  timeout  客户端等不到响应（本地超时，拿不到错误信封）
"""

import logging
import time

from app.retrieval.types import Evidence, RetrievalResult, RetrievalStatus

logger = logging.getLogger(__name__)

# 模拟契约 6.2 示例里的三条证据，score 降序
_CANNED_EVIDENCE = [
    Evidence(
        chunk_id="chk_a1b2c3",
        content="历史案例：客户购买腰带后怀疑五金掉色要求仅退款。审核结论：经鉴定为正品，"
        "掉色属正常磨损，已引导客户走售后质保而非仅退款。",
        source_type="case",
        source_title="五金掉色疑假货仅退款案例",
        score=0.9132,
        metadata={
            "doc_id": "doc_c01",
            "chunk_index": 0,
            "created_at": "2026-09-20T10:12:00+08:00",
            "case_id": "case_2201",
            "original_query": "客户咬定五金掉色是假货，威胁投诉，怎么破？",
            "review_result": "鉴定正品，属正常磨损，走质保流程，不下架不退款",
            "hit_count": 7,
            "feedback_source": "kezhou",
        },
    ),
    Evidence(
        chunk_id="chk_d4e5f6",
        content="本店售后政策：客户主张商品为假货时，须先提交平台鉴定入口出具的鉴定结果；"
        "鉴定为正品且客户仍拒绝收货的，按七天无理由退货处理，运费由客户承担。",
        source_type="merchant",
        source_title="皮具店售后处理手册 v2",
        score=0.8774,
        metadata={"doc_id": "doc_m11", "chunk_index": 3, "created_at": "2026-09-10T09:00:00+08:00", "category": "箱包"},
    ),
    Evidence(
        chunk_id="chk_g7h8i9",
        content="平台规范：商家不得以'商品已拆封'为由拒绝七天无理由退货，但定制类商品除外；"
        "涉及假货争议的，平台鉴定结论为最终依据。",
        source_type="platform",
        source_title="电商平台假货争议处理规范（2026-06 版）",
        score=0.8421,
        metadata={"doc_id": "doc_p03", "chunk_index": 1, "created_at": "2026-06-01T00:00:00+08:00", "effective_date": "2026-06-01"},
    ),
]


class MockRetrievalClient:
    def __init__(self, scenario: str = "ok"):
        assert scenario in {"ok", "empty", "degraded", "error", "timeout"}, scenario
        self.scenario = scenario
        self.call_count = 0  # 测试用：记录调用次数

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
        include_platform: bool = True,
        include_case: bool = True,
    ) -> RetrievalResult:
        self.call_count += 1
        time.sleep(0.01)  # 模拟网络耗时，保持异步调度可见

        if self.scenario == "ok":
            return RetrievalResult(
                status=RetrievalStatus.OK,
                query_id="ret_mock_ok_0001",
                evidence=list(_CANNED_EVIDENCE)[:top_k],
                latency_ms=120,
            )
        if self.scenario == "empty":
            # 检索为空是合法响应：有 query_id、retrieved=false（契约 6.3）
            return RetrievalResult(
                status=RetrievalStatus.EMPTY,
                query_id="ret_mock_empty_0001",
                latency_ms=289,
            )
        if self.scenario == "degraded":
            return RetrievalResult(
                status=RetrievalStatus.OK,
                query_id="ret_mock_degraded_0001",
                evidence=[_CANNED_EVIDENCE[1]],
                degraded=True,
                degrade_reason="vector_store_unavailable",
                latency_ms=173,
            )
        if self.scenario == "error":
            # 知源双路径均失败：503 错误信封（契约第 4 节）
            return RetrievalResult(
                status=RetrievalStatus.ERROR,
                error_code="RETRIEVAL_UNAVAILABLE",
                error_message="向量检索与关键词降级路径均失败",
                severity="error",
                latency_ms=3000,
            )
        # timeout：客户端侧超时，拿不到任何信封
        return RetrievalResult(
            status=RetrievalStatus.ERROR,
            error_code="CLIENT_TIMEOUT",
            error_message="客户端等待知源响应超时（3.5s）",
            severity="error",
            latency_ms=3500,
        )
