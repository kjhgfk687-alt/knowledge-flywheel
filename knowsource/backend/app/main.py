"""知源（KnowSource）后端入口。

职责边界：/api/v1/retrieve 是客舟唯一可调用的契约端点；
/api/v1/documents/* 是知源前端控制台内部 API，不属于契约。
"""
from fastapi import FastAPI

from app.config import get_settings
from app.core.errors import ok, register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    register_exception_handlers(app)

    @app.get("/healthz")
    async def healthz():
        return ok({"status": "healthy", "version": settings.app_version})

    # 路由随模块交付挂载（M4: documents，M6: retrieve，P2: tenants，P3: cases）
    from app.api import cases, documents, retrieve, tenants

    app.include_router(retrieve.router)
    app.include_router(documents.router)
    app.include_router(tenants.router)
    app.include_router(cases.router)

    return app


app = create_app()
