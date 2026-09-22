"""LLM 适配层：统一 complete(system, user) -> str 接口。

阶段一默认 MockLLM（规则式，无需密钥，端到端测试全靠它）；
接真实模型时配 KEZHOU_LLM_PROVIDER=openai_compatible + 密钥即可，
任何 OpenAI 兼容协议（GLM / DeepSeek / OpenAI）都能用。
节点层重试逻辑在节点内做，适配层不重试。
"""

import json
import logging
import re
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class LLM(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class MockLLM:
    """规则式假 LLM。

    约定（测试用标记写在用户输入里）：
    - 「〔低置信〕」开头 → 意图置信度 0.35（触发 intent_low_confidence 组合原因）
    - 包含「〔无引用〕」    → 生成不带 [n] 引用的回答（触发 citation_missing 分支）
    """

    _EMOTION_WORDS = ("投诉", "骗子", "垃圾", "受够了", "恶心", "退我钱", "315")
    # 知识问询保护词：含这些词的问题优先判知识问答（如"退货政策是什么"是问政策不是申请退货）
    _KNOWLEDGE_GUARDS = ("政策", "规定", "标准", "是什么", "怎么处理", "怎么办")
    _REFUND_WORDS = ("退款", "退货", "仅退款", "退钱")
    _ORDER_WORDS = ("订单", "物流", "发货", "快递")
    _CHITCHAT_WORDS = ("你好", "你是谁", "笑话", "天气", "在吗")

    async def complete(self, system: str, user: str) -> str:
        if "INTENT_ROUTER" in system:
            return self._route(user)
        if "RAG_GENERATE" in system:
            return self._generate(user)
        return "ok"

    def _route(self, user: str) -> str:
        # 只对"当前问题"做关键词判定。prompt 里还有画像与近期对话（含历史词，如
        # 画像中的"退款"主题、回复里的"政策"），全文匹配会被历史词污染路由
        # （2026-09-22 实测 bug：画像含"退款"导致该用户所有消息全进风控审批）。
        question = user.split("当前问题：")[-1]
        emotion = "high" if any(w in question for w in self._EMOTION_WORDS) else "normal"
        # 标记在"当前问题："之后，须用包含判断而非 startswith
        confidence = 0.35 if "〔低置信〕" in question else None
        if any(w in question for w in self._KNOWLEDGE_GUARDS):
            intent, confidence = "knowledge_qa", confidence or 0.9
        elif any(w in question for w in self._REFUND_WORDS):
            intent, confidence = "refund_request", confidence or 0.95
        elif any(w in question for w in self._ORDER_WORDS):
            intent, confidence = "order_query", confidence or 0.95
        elif any(w in question for w in self._CHITCHAT_WORDS):
            intent, confidence = "chitchat", confidence or 0.9
        else:
            intent, confidence = "knowledge_qa", confidence or 0.9
        return json.dumps({"intent": intent, "confidence": confidence, "emotion": emotion}, ensure_ascii=False)

    def _generate(self, user: str) -> str:
        if "〔无引用〕" in user:
            return "根据相关规则，建议您联系商家协商处理此事。"
        # 从证据块里提取 [n] 编号，生成带引用的回答
        ns = sorted({int(m) for m in re.findall(r"^\[(\d+)\]", user, flags=re.M)})
        if not ns:
            return "根据相关规则，建议您联系商家协商处理此事。"
        refs = " ".join(f"[{n}]" for n in ns)
        return f"根据知识库证据，建议先核实商品鉴定结论，再按售后政策引导客户处理 {refs}。"


class OpenAICompatibleLLM:
    """OpenAI 兼容 chat/completions 协议的薄封装。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]


def get_llm(provider: str, base_url: str, api_key: str, model: str) -> LLM:
    if provider == "mock":
        return MockLLM()
    if provider == "openai_compatible":
        if not api_key:
            raise ValueError("KEZHOU_LLM_API_KEY 未配置：openai_compatible 模式必须提供密钥（fail fast 在启动期）")
        return OpenAICompatibleLLM(base_url, api_key, model)
    raise ValueError(f"未知 LLM provider: {provider}")


def parse_json_loose(raw: str) -> dict:
    """容忍模型输出 ```json 围栏或前后缀文本，提取第一个 JSON 对象。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, flags=re.S)
        if brace:
            text = brace.group(0)
    return json.loads(text)
