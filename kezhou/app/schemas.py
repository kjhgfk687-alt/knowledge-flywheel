"""API 数据模型：ChatRequest / ChatResponse。

ChatResponse.citations 是 Vue 引用弹卡的数据源：
[n] 可点上标 → 弹卡展示 source_title / source_type / chunk_id，
from_case=true 时前端加"来自历史审核案例"标签。
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    tenant_id: str = Field(..., description="租户 ID，由渠道/组件配置决定，不取自终端用户输入")
    user_id: str = Field(default="guest", description="访客 ID（组件生成）")
    session_id: str | None = Field(default=None, description="会话 ID，缺省新建")
    message: str = Field(..., min_length=1, max_length=512, description="用户消息（去首尾空白后 1-512 字）")


class Citation(BaseModel):
    n: int
    chunk_id: str
    source_title: str
    source_type: str  # platform / merchant / case
    from_case: bool


class ChatResponse(BaseModel):
    session_id: str
    reply_text: str
    citations: list[Citation] = []
    need_human: bool = False
    unverified: bool = False
    transfer_reason: str | None = None
    query_id: str | None = None
    intent: str | None = None
    retrieval_degraded: bool = False
    risk_level: str | None = None
    risk_factors: list[str] = []
    risk_outcome: str | None = None
    approval_request_id: str | None = None


class ApprovalReviewRequest(BaseModel):
    action: str = Field(..., description="approve | reject")
    note: str | None = Field(default=None, max_length=2000, description="审批备注（驳回原因等）")


class HandoffReviewRequest(BaseModel):
    review_result: str = Field(..., min_length=1, max_length=2000, description="人工审核结论（回写知源的审核理由）")
    category: str | None = Field(default=None, max_length=64, description="商品品类（回写时修订）")
