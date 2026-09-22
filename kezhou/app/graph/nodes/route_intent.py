"""route_intent：LLM 一次结构化调用产出 intent + emotion + 置信度。

异常约定：LLM 失败/输出不合法 → 重试 1 次 → 仍失败默认 knowledge_qa + normal。
路由挂掉不能变成拒答——检索链路是最安全的兜底路径（决策点2）。
"""

import logging
from typing import Any

from app.config import Settings
from app.llm import LLM, parse_json_loose

logger = logging.getLogger(__name__)

_ALLOWED_INTENTS = {"knowledge_qa", "order_query", "refund_request", "chitchat"}

INTENT_SYSTEM = (
    "INTENT_ROUTER：你是电商客服的意图路由器。根据用户问题只输出 JSON（不要多余文字）："
    '{"intent": "knowledge_qa|order_query|refund_request|chitchat", '
    '"confidence": 0到1的小数, "emotion": "normal|high"}。'
    "emotion=high 表示用户情绪激烈（愤怒、威胁投诉等）。"
)


def make_route_intent(settings: Settings, llm: LLM):
    async def route_intent(state: dict[str, Any]) -> dict[str, Any]:
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in state.get("history", [])[-4:])
        profile = state.get("user_profile_summary") or "（无画像）"
        user = (
            f"用户画像：{profile}\n近期对话：\n{history_text}\n当前问题：{state.get('user_input', '')}"
        )

        intent, confidence, emotion = "knowledge_qa", 0.9, "normal"
        for attempt in (1, 2):
            try:
                raw = await llm.complete(INTENT_SYSTEM, user)
                data = parse_json_loose(raw)
                cand = str(data.get("intent", ""))
                if cand in _ALLOWED_INTENTS:
                    intent = cand
                confidence = min(1.0, max(0.0, float(data.get("confidence", 0.9))))
                emotion = "high" if data.get("emotion") == "high" else "normal"
                break
            except Exception as e:
                logger.warning("意图识别失败（第 %s 次）：%s", attempt, e)
        else:
            logger.error("意图识别两次失败，兜底 knowledge_qa 继续 session=%s", state.get("session_id"))

        # 情绪合并：关键词快筛与模型判断任一 high 即 high（取严）
        emotion_final = "high" if (state.get("emotion_keyword_hit") or emotion == "high") else "normal"
        return {
            "intent": intent,
            "intent_confidence": confidence,
            "emotion_level": emotion_final,
        }

    return route_intent
