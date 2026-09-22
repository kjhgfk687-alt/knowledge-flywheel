"""三维转人工规则引擎（2026-09-22 需求确认）：

- 鉴定争议类问题 → 一律强制转人工（并附知识库鉴定标准引用，引用由节点层补充）；
- 未鉴定/未知鉴定状态 + 高价值（≥ KEZHOU_HIGH_VALUE_THRESHOLD）→ 强制转人工；
- 已鉴定 + 金额 ≤ KEZHOU_AUTO_REFUND_MAX → 自动流程（只读答复，不做任何写操作）；
- 其余中间地带 → 需人工审核确认（阶段四接审批队列）。

纯函数、表驱动、risk_factors 逐条可解释——这是转人工审计与简历叙事的落点。
"""

from dataclasses import dataclass
from typing import Any


class RiskOutcome:
    AUTO = "auto"
    APPROVAL = "approval"
    FORCE_HUMAN = "force_human"


@dataclass(frozen=True)
class RiskDecision:
    outcome: str
    level: str  # low | medium | high
    factors: list[str]


def evaluate_risk(order_data: dict[str, Any], dispute: bool, *, auto_refund_max: float, high_value_threshold: float) -> RiskDecision:
    amount = order_data.get("amount")
    auth = order_data.get("auth_status")  # authentic | unauthenticated | 未知（无订单时）

    if dispute:
        return RiskDecision(RiskOutcome.FORCE_HUMAN, "high", ["鉴定争议（平台规范：鉴定结论为最终依据，须人工核验）"])

    amount_text = f"¥{amount:g}" if isinstance(amount, (int, float)) else "金额未知"
    if auth == "authentic" and isinstance(amount, (int, float)) and amount <= auto_refund_max:
        return RiskDecision(RiskOutcome.AUTO, "low", [f"已鉴定正品", f"金额 {amount_text} ≤ 自动办理上限 {auto_refund_max:g}"])

    if auth != "authentic" and (not isinstance(amount, (int, float)) or amount >= high_value_threshold):
        factors = ["未鉴定" if auth != "authentic" else "已鉴定"]
        if not isinstance(amount, (int, float)):
            factors.append("金额未知（保守处理）")
        else:
            factors.append(f"金额 {amount_text} ≥ 高价值线 {high_value_threshold:g}")
        return RiskDecision(RiskOutcome.FORCE_HUMAN, "high", factors)

    return RiskDecision(RiskOutcome.APPROVAL, "medium", ["未命中自动/强制规则，转人工审核确认"])
