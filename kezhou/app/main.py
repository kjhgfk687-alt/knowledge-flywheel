"""客舟 FastAPI 入口：POST /api/v1/chat（阶段三加 /ws/agent）。

端口约定：客舟 8200（知源 8100），见契约 v1.0 第 1 节。
"""

import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import TENANT_ID_PATTERN, get_settings
from app.review_policy import is_writeback_candidate
from app.runtime import Runtime
from app.schemas import (
    ApprovalReviewRequest,
    ChatRequest,
    ChatResponse,
    Citation,
    HandoffReviewRequest,
)

logger = logging.getLogger(__name__)

_TENANT_RE = re.compile(TENANT_ID_PATTERN)


def state_to_response(session_id: str, state: dict) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        reply_text=state.get("reply_text", ""),
        citations=[Citation(**c) for c in state.get("citations", [])],
        need_human=bool(state.get("need_human")),
        unverified=bool(state.get("unverified")),
        transfer_reason=state.get("transfer_reason"),
        query_id=state.get("query_id"),
        intent=state.get("intent"),
        retrieval_degraded=bool(state.get("retrieval_degraded")),
        risk_level=state.get("risk_level"),
        risk_factors=state.get("risk_factors", []),
        risk_outcome=state.get("risk_outcome"),
        approval_request_id=state.get("approval_request_id"),
    )


def create_app(settings=None, case_writeback=None) -> FastAPI:
    settings = settings or get_settings()
    runtime = Runtime(settings)
    override_case_writeback = case_writeback

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start()
        if override_case_writeback is not None:
            runtime.case_writeback = override_case_writeback  # 测试/部署注入
        yield
        await runtime.stop()

    app = FastAPI(title="客舟 KeZhou", version="0.2.0", lifespan=lifespan)
    # Vue dev server（客舟 5174；知源前端占用 5173）跨域；生产同源部署后可收窄
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        message = req.message.strip()
        if not message or len(message) > 512:
            raise HTTPException(status_code=422, detail="message 去首尾空白后须为 1-512 字符")
        if not _TENANT_RE.match(req.tenant_id):
            raise HTTPException(status_code=422, detail=f"tenant_id 格式非法: 期望 {TENANT_ID_PATTERN}")
        session_id = req.session_id or f"s_{uuid.uuid4().hex[:12]}"
        state = await runtime.chat(session_id, req.user_id, req.tenant_id, message)
        return state_to_response(session_id, state)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "kezhou", "mode": settings.retrieve_mock_scenario}

    # 坐席端 WebSocket：接收转人工实时通知（阶段五管理台消费）
    @app.websocket("/ws/agent")
    async def ws_agent(websocket: WebSocket) -> None:
        await runtime.notifier.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # 保活循环（客户端 ping/忽略）
        except WebSocketDisconnect:
            runtime.notifier.disconnect(websocket)

    # ---- 审批队列（高风险操作，阶段四）----

    @app.get("/api/v1/approvals")
    async def list_approvals(status: str | None = None, session_id: str | None = None) -> dict:
        return {"approvals": runtime.store.load_approvals(status=status, session_id=session_id)}

    @app.post("/api/v1/approvals/{approval_id}/review")
    async def review_approval(approval_id: str, body: ApprovalReviewRequest) -> dict:
        action = {"approve": "approved", "reject": "rejected"}.get(body.action)
        if action is None:
            raise HTTPException(status_code=422, detail="action 须为 approve 或 reject")
        record = runtime.store.review_approval(approval_id, action, body.note)
        if record is None:
            existing = runtime.store.get_approval(approval_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"审批单不存在: {approval_id}")
            raise HTTPException(status_code=409, detail=f"该审批单已处理（status={existing['status']}）")
        return {"approval": record}

    # ---- 转人工审核与案例回写（飞轮沉淀端，阶段四）----

    @app.get("/api/v1/handoffs")
    async def list_handoffs(status: str | None = None, session_id: str | None = None) -> dict:
        return {"handoffs": runtime.store.load_handoffs(session_id=session_id, status=status)}

    async def _do_writeback(record: dict, category: str | None) -> dict:
        result = await runtime.case_writeback.write_back(
            tenant_id=record["tenant_id"],
            original_query=record["query"],
            review_result=record["review_result"],
            query_id=record.get("query_id"),
            category=category or "",
            feedback_source="kezhou",
        )
        if result.ok:
            runtime.store.mark_writeback(record["id"], "success", case_id=result.case_id)
            return {"status": "success", "case_id": result.case_id}
        if result.severity == "critical":
            logger.critical("案例回写契约级失败 handoff=%s code=%s", record["id"], result.error_code)
        else:
            logger.error("案例回写失败 handoff=%s code=%s msg=%s", record["id"], result.error_code, result.error_message)
        runtime.store.mark_writeback(record["id"], "failed", error=f"{result.error_code}: {result.error_message}")
        return {"status": "failed", "error_code": result.error_code, "error_message": result.error_message}

    @app.post("/api/v1/handoffs/{handoff_id}/review")
    async def review_handoff(handoff_id: str, body: HandoffReviewRequest) -> dict:
        record = runtime.store.review_handoff(handoff_id, body.review_result.strip())
        if record is None:
            existing = runtime.store.get_handoff(handoff_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"转人工记录不存在: {handoff_id}")
            raise HTTPException(status_code=409, detail=f"该记录已审核（status={existing['status']}）")
        if is_writeback_candidate(record["transfer_reason"]):
            writeback = await _do_writeback(record, body.category)
        else:
            writeback = {"status": "skipped", "reason": "非回写候选（仅知识/回答质量类原因沉淀为案例）"}
        return {"record": runtime.store.get_handoff(handoff_id), "writeback": writeback}

    @app.post("/api/v1/handoffs/{handoff_id}/writeback")
    async def retry_writeback(handoff_id: str) -> dict:
        record = runtime.store.get_handoff(handoff_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"转人工记录不存在: {handoff_id}")
        if record["status"] != "reviewed":
            raise HTTPException(status_code=409, detail="请先完成人工审核再回写")
        if not is_writeback_candidate(record["transfer_reason"]):
            raise HTTPException(status_code=422, detail="非回写候选原因，不沉淀为案例知识")
        return await _do_writeback(record, None)

    # ---- 飞轮指标（阶段五管理台）----

    @app.get("/api/v1/metrics")
    async def metrics(tenant_id: str | None = None) -> dict:
        payload = {**runtime.store.stats(), "zhiyuan_cases": [], "zhiyuan_cases_ok": True}
        if tenant_id:
            listed = await runtime.case_writeback.list_cases(tenant_id)
            if listed.ok:
                payload["zhiyuan_cases"] = list(listed.cases)
            else:
                payload["zhiyuan_cases_ok"] = False
                payload["zhiyuan_cases_error"] = f"{listed.error_code}: {listed.error_message}"
        return payload

    return app


app = create_app()
