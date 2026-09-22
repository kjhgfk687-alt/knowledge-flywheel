"""契约 §1：服务间共享密钥校验（X-API-Key）。"""
import secrets

from fastapi import Header

from app.config import get_settings
from app.core.errors import AUTH_KEY_INVALID, AUTH_KEY_MISSING, api_error


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """依赖注入用：校验 X-API-Key，通过后原样返回（留作日志追踪）。"""
    if not x_api_key:
        raise api_error(AUTH_KEY_MISSING, "未携带 X-API-Key 请求头")
    if not secrets.compare_digest(x_api_key, get_settings().ks_api_key):
        raise api_error(AUTH_KEY_INVALID, "X-API-Key 不正确")
    return x_api_key
