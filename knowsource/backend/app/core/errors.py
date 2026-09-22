"""契约 §4 统一错误信封。

所有响应（成功与错误）同构：{"code": int, "message": str, "data": any}。
- code=0 成功；非 0 为契约错误码。
- FastAPI/Starlette 的原生错误一律经异常处理器改写为本信封，客户端只按契约表解析。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """业务可预期错误：直接携带契约 HTTP 状态码 + 错误码。"""

    def __init__(self, http_status: int, code: int, identifier: str, message: str):
        self.http_status = http_status
        self.code = code
        self.identifier = identifier
        self.message = message
        super().__init__(message)


def envelope(code: int, message: str, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}


def ok(data: Any, message: str = "ok") -> dict:
    return envelope(0, message, data)


def _json(status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(code, message, None))


# ---- 契约 §4 错误码常量（业务代码抛 ApiError 时引用，测试逐条断言） ----
AUTH_KEY_MISSING = (401, 40100, "AUTH_KEY_MISSING")
AUTH_KEY_INVALID = (401, 40101, "AUTH_KEY_INVALID")
TENANT_ID_INVALID = (422, 40001, "TENANT_ID_INVALID")
QUERY_INVALID = (422, 40002, "QUERY_INVALID")
TOP_K_INVALID = (422, 40003, "TOP_K_INVALID")
FILTERS_INVALID = (422, 40004, "FILTERS_INVALID")
BODY_NOT_JSON = (422, 40005, "BODY_NOT_JSON")
TENANT_NOT_FOUND = (404, 40401, "TENANT_NOT_FOUND")
TENANT_DISABLED = (403, 40301, "TENANT_DISABLED")
RETRIEVAL_UNAVAILABLE = (503, 50301, "RETRIEVAL_UNAVAILABLE")
RETRIEVAL_TIMEOUT = (504, 50401, "RETRIEVAL_TIMEOUT")
RATE_LIMITED = (429, 42901, "RATE_LIMITED")
INTERNAL_ERROR = (500, 50000, "INTERNAL_ERROR")


def api_error(spec: tuple[int, int, str], message: str) -> ApiError:
    """按契约常量构造 ApiError：api_error(TENANT_NOT_FOUND, "租户不存在: t_x")"""
    status, code, ident = spec
    return ApiError(status, code, ident, message)


def _map_validation_error(exc: RequestValidationError) -> ApiError:
    """把 FastAPI 请求校验错误映射到契约 422 族错误码。

    映射规则：body 整体非 JSON → 40005；按出错字段定位到对应错误码；
    无法定位的参数形状问题归入 40004（请求结构非法），message 带出具体字段。
    """
    errors = exc.errors()
    first = errors[0] if errors else {}

    # 请求体不是合法 JSON（loc 形如 ("body",) 或 ("body", 0)，type 含 json）
    first_loc = [str(p) for p in first.get("loc", [])]
    if first_loc and first_loc[0] == "body" and "json" in str(first.get("type", "")):
        return ApiError(422, 40005, "BODY_NOT_JSON", "请求体不是合法 JSON")

    def find_loc() -> str:
        for e in errors:
            loc = [str(p) for p in e.get("loc", [])]
            if len(loc) >= 2:
                return loc[1]
        return ""

    field = find_loc()
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}" for e in errors[:3]
    )
    if field == "tenant_id":
        return ApiError(422, 40001, "TENANT_ID_INVALID", f"tenant_id 缺失或格式非法: {detail}")
    if field in ("query", "original_query", "review_result"):
        # 契约 v1.3：/retrieve 的 query 与 /cases 回写的文本字段同属 40002 族
        return ApiError(422, 40002, "QUERY_INVALID", f"文本字段为空或超长: {detail}")
    if field == "top_k":
        return ApiError(422, 40003, "TOP_K_INVALID", f"top_k 需为 1-20 的整数: {detail}")
    return ApiError(422, 40004, "FILTERS_INVALID", f"请求参数非法（{field or 'body'}）: {detail}")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return _json(exc.http_status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        e = _map_validation_error(exc)
        return _json(e.http_status, e.code, e.message)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        # FastAPI 对 UTF-8 解码失败的 body 抛 HTTPException(400, "error parsing the body")，
        # 不走 pydantic 校验路径；按契约归入 40005
        if exc.status_code == 400 and "parsing the body" in str(exc.detail):
            return _json(422, 40005, "请求体不是合法 JSON 或编码错误（须为 UTF-8）")
        # 其余路由不存在 / 方法不允许等框架级 HTTP 错误，保持信封同构
        return _json(exc.status_code, exc.status_code * 100, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        return _json(500, 50000, "服务器内部错误")
