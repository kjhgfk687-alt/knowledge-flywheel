"""风控层：三维转人工规则（鉴定状态 × 品类 × 金额），表驱动、输出可解释。"""

from app.risk.rules import RiskOutcome, evaluate_risk

__all__ = ["RiskOutcome", "evaluate_risk"]
