"""risk_assess（阶段三）：退货/退款三维风控。

出口（builder 条件边）：
- auto（已鉴定+低额+无争议）→ respond，答复自动办理话术（只读，不执行任何写操作）；
- approval（中间地带）→ respond，答复需人工确认话术（阶段四在此接 create_approval 审批队列）；
- force_human → human_handoff(risk_hit)。鉴定争议类会检索"鉴定标准"并附 [n] 引用。

订单工具任何故障都优雅降级（按无订单数据评估 + ERROR 日志），不让工具故障击穿风控流程。
"""

import logging
from typing import Any

from app.risk.rules import RiskOutcome, evaluate_risk
from app.tools.order import OrderNotFound, OrderTool, extract_order_id
from app.retrieval.types import RetrievalClient, RetrievalStatus

logger = logging.getLogger(__name__)

_DISPUTE_QUERY = "假货争议 鉴定标准 鉴定流程 平台鉴定结论"

_AUTO_REPLY = (
    "您的订单 {order_id} 符合自动退货条件（已鉴定为正品、金额 ¥{amount:g} 在自动办理范围内），"
    "将按售后政策为您自动办理退货退款。"
)
_DISPUTE_REPLY_HEAD = "您反馈的问题涉及商品真伪鉴定争议。按平台规范，平台鉴定结论为最终依据"


def make_risk_assess(settings, order_tool: OrderTool, client: RetrievalClient):
    async def risk_assess(state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")
        dispute = any(k in user_input for k in settings.refund_dispute_keywords)
        order_id = extract_order_id(user_input, *(m.get("content") for m in state.get("history", [])[-3:]))

        order_data: dict[str, Any] = {}
        if order_id:
            try:
                order_data = await order_tool.get(order_id)
            except OrderNotFound:
                logger.info("风控评估：订单 %s 不存在，按无订单数据处理", order_id)
            except Exception as e:
                # 优雅降级：工具故障不击穿风控流程，按无订单数据评估（保守方向）
                logger.error("风控评估取订单失败 order_id=%s: %s", order_id, e)

        decision = evaluate_risk(
            order_data, dispute,
            auto_refund_max=settings.auto_refund_max,
            high_value_threshold=settings.high_value_threshold,
        )
        logger.warning(
            "RISK outcome=%s level=%s factors=%s session=%s",
            decision.outcome, decision.level, decision.factors, state.get("session_id"),
        )

        updates: dict[str, Any] = {
            "order_id": order_id,
            "order_data": order_data,
            "risk_outcome": decision.outcome,
            "risk_level": decision.level,
            "risk_factors": decision.factors,
        }

        if decision.outcome == RiskOutcome.AUTO and order_data:
            updates["reply_text"] = _AUTO_REPLY.format(order_id=order_data["order_id"], amount=order_data["amount"])
            return updates
        if decision.outcome == RiskOutcome.APPROVAL:
            # 回复话术由 create_approval 节点生成（带审批单号），本节点只产出风控结论
            return updates

        # force_human：鉴定争议类附知识库鉴定标准引用（[n] 可点，前端弹卡溯源）
        if dispute:
            result = await client.retrieve(state["tenant_id"], _DISPUTE_QUERY)
            if result.status is RetrievalStatus.OK and result.evidence:
                refs = " ".join(f"[{i}]" for i in range(1, len(result.evidence) + 1))
                updates["reply_text"] = (
                    f"{_DISPUTE_REPLY_HEAD} {refs}，此类请求须由人工结合鉴定结果为您处理。"
                )
                updates["citations"] = [
                    {
                        "n": i,
                        "chunk_id": ev.chunk_id,
                        "source_title": ev.source_title,
                        "source_type": ev.source_type,
                        "from_case": ev.source_type == "case",
                    }
                    for i, ev in enumerate(result.evidence, 1)
                ]
            else:
                logger.warning("鉴定标准检索未命中（status=%s），转人工话术不带引用", result.status.value)
        return updates  # force_human → human_handoff 追加转人工话术

    return risk_assess


def after_risk_assess(state: dict[str, Any]) -> str:
    outcome = state.get("risk_outcome")
    if outcome == "force_human":
        return "human_handoff"
    if outcome == "approval":
        return "create_approval"
    return "respond"
