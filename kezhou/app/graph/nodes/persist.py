"""persist：终点节点，写短期记忆 + 节流更新长期画像 + 会话压缩。

异常约定：全部失败仅告警，绝不影响已生成的响应；幂等靠 message_id 唯一约束。
"""

import logging
from typing import Any

from app.config import Settings
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def make_persist(settings: Settings, store: MemoryStore):
    async def persist(state: dict[str, Any]) -> dict[str, Any]:
        session_id = state.get("session_id", "")
        user_id = state.get("user_id", "")
        tenant_id = state.get("tenant_id", "")
        try:
            store.append_message(session_id, user_id, tenant_id, "user", state.get("user_input", ""))
            store.append_message(session_id, user_id, tenant_id, "assistant", state.get("reply_text", ""))
        except Exception:
            logger.error("短期记忆写入失败 session=%s", session_id, exc_info=True)

        try:
            if state.get("intent") not in (None, "chitchat"):
                turns = store.bump_profile(user_id, tenant_id, state.get("user_input", ""))
                if turns % settings.long_term_update_interval == 0:
                    logger.info("长期画像已节流更新 user=%s turns=%s", user_id, turns)
        except Exception:
            logger.error("长期画像更新失败 user=%s", user_id, exc_info=True)

        try:
            dropped = store.compact_session(session_id, keep=settings.history_window)
            if dropped:
                logger.info("会话压缩完成 session=%s 压缩消息数=%s", session_id, dropped)
        except Exception:
            logger.error("会话压缩失败 session=%s", session_id, exc_info=True)

        return {}

    return persist
