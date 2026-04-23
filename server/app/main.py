from __future__ import annotations

from contextlib import asynccontextmanager

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
