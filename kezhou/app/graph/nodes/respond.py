"""respond：唯一出口，纯组装最终回复，无 IO 无失败分支。

- 转人工路径：reply_text 已由 human_handoff 写好，原样放行；
- 正常路径：取 draft_answer；
- citation_missing：追加"未经验证"提示（前端同时会收到 unverified 标志）。
"""

from typing import Any


async def respond(state: dict[str, Any]) -> dict[str, Any]:
    reply = state.get("reply_text") or state.get("draft_answer") or "抱歉，系统出现异常，已为您转接人工客服。"
    unverified = bool(state.get("citation_missing"))
    if unverified and not state.get("need_human"):
        reply = f"{reply}\n（本回答未引用知识库证据，仅供参考）"
    # 唯一出口统一补全响应标志：不经过 human_handoff 的路径（OK/闲聊）也要有显式 False
    return {
        "reply_text": reply,
        "unverified": unverified,
        "citations": state.get("citations", []),
        "need_human": bool(state.get("need_human")),
        "transfer_reason": state.get("transfer_reason"),
        "query_id": state.get("query_id"),
    }
