from __future__ import annotations

from contextlib import asynccontextmanager
import hmac

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.deps import build_container
from app.api.router import api_router
from app.core.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.container = build_container()
    try:
        yield
    finally:
        app.state.container.close()


app = FastAPI(title="X Downloader API", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.middleware("http")
async def verify_local_secret(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        settings = request.app.state.container.settings
        if settings.cloud_mode:
            return await call_next(request)
        client_host = request.client.host if request.client else ""
        if client_host not in _LOOPBACK_HOSTS:
            return JSONResponse(status_code=403, content={"error": {"code": "local_service_forbidden", "message": "local service only accepts loopback requests", "user_message": "本地后端只允许本机访问。", "details": {}}})
        if request.url.path != "/api/v1/health":
            secret = settings.local_secret
            provided_secret = request.headers.get("X-XDownloader-Local-Secret", "")
            if not secret or not hmac.compare_digest(provided_secret, secret):
                return JSONResponse(status_code=403, content={"error": {"code": "local_service_untrusted", "message": "local service secret mismatch", "user_message": "本地后端校验失败，请重启应用。", "details": {}}})
    return await call_next(request)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "request validation failed",
                "user_message": "请求参数不合法。",
                "details": {"errors": exc.errors()},
            }
        },
    )
