"""通知适配层：转人工事件的出口。

阶段三 WSNotifier：日志照打 + 向所有已连接的坐席端（/ws/agent）广播转人工事件。
设计约束：通知绝不允许阻塞用户对话路径——单客户端发送限时 0.5s，超时/失败即丢弃该连接
（慢消费者不能拖住正在对话的用户）；无坐席在线时广播为 no-op，日志始终是权威记录。
"""

import asyncio
import json
import logging
from typing import Protocol

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 0.5


class Notifier(Protocol):
    async def notify_handoff(self, record: dict) -> None: ...


class LogNotifier:
    async def notify_handoff(self, record: dict) -> None:
        logger.info(
            "HANDOFF tenant=%s session=%s reason=%s query_id=%s id=%s",
            record.get("tenant_id"),
            record.get("session_id"),
            record.get("transfer_reason"),
            record.get("query_id"),
            record.get("id"),
        )


class WSNotifier:
    """坐席端 WebSocket 连接管理 + 转人工事件广播。"""

    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        logger.info("坐席端已连接（当前在线 %s）", len(self._clients))

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        logger.info("坐席端断开（当前在线 %s）", len(self._clients))

    async def notify_handoff(self, record: dict) -> None:
        logger.info(
            "HANDOFF tenant=%s session=%s reason=%s query_id=%s id=%s",
            record.get("tenant_id"),
            record.get("session_id"),
            record.get("transfer_reason"),
            record.get("query_id"),
            record.get("id"),
        )
        payload = json.dumps({"type": "handoff", "record": record}, ensure_ascii=False, default=str)
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=_SEND_TIMEOUT_SECONDS)
            except Exception:
                self._clients.discard(ws)
                logger.warning("坐席端推送超时/失败，已移除连接", exc_info=True)
