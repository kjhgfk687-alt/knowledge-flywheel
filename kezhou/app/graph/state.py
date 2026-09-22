"""KeZhouState：全图共享状态（total=False，节点只写自己负责的字段）。

原则：节点只算状态，导航由 builder 里的条件边承担。
"""

from typing import TypedDict


class KeZhouState(TypedDict, total=False):
    # ---- 会话标识（API 层注入；tenant_id 来自渠道配置，绝不取自用户输入——防跨租户）----
    session_id: str
    user_id: str
    tenant_id: str

    # ---- 对话 ----
    user_input: str
    history: list[dict]            # [{"role", "content"}]，load_context 装载
    user_profile_summary: str
    long_term_injected: bool       # 决策点1补充：图恢复（checkpointer 复活状态）时避免重复注入长期记忆

    # ---- 路由 ----
    intent: str                    # knowledge_qa | order_query | refund_request | chitchat
    intent_confidence: float
    emotion_level: str             # normal | high（关键词快筛 + 模型判断取严）
    emotion_keyword_hit: bool

    # ---- 检索（retrieve 写入）----
    retrieval_status: str          # ok | empty | error（RetrievalStatus 枚举值）
    evidences: list[dict]          # Evidence 字典化列表
    query_id: str | None           # 飞轮链路关联键
    retrieval_degraded: bool
    degrade_reason: str | None

    # ---- 生成（generate 写入）----
    draft_answer: str
    citations: list[dict]          # [{n, chunk_id, source_title, source_type, from_case}]，Vue 弹卡数据源
    citation_missing: bool         # 决策点3补充：保留标记，阶段三再决定是否升级为强制转人工
    regenerated: bool
    generation_failed: bool        # LLM 两次调用均失败

    # ---- 置信度（assess_confidence 写入）----
    confidence_level: str          # confident | low
    confidence_signals: list[str]

    # ---- 订单〔阶段三〕----
    order_id: str | None
    order_data: dict
    awaiting_slot: bool

    # ---- 风控〔阶段三/四〕----
    risk_outcome: str | None       # auto | approval | force_human（导航由条件边读它）
    risk_level: str | None
    risk_factors: list[str]
    approval_request_id: str | None

    # ---- 转人工 ----
    need_human: bool
    transfer_reason: str | None    # 支持组合："intent_low_confidence+retrieval_empty"
    transfer_record_id: str | None
    tool_failed: bool              # 订单等工具基础设施故障（区别于业务性未命中）

    # ---- 响应（respond 写入）----
    reply_text: str
    unverified: bool
