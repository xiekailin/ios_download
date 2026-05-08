from __future__ import annotations

import hashlib
import hmac
import os
import signal

from fastapi import BackgroundTasks

from fastapi import APIRouter, Depends, Header

from app.api.deps import AppContainer, get_container
from app.core.errors import AuthorizationError

router = APIRouter(prefix="/health", tags=["health"])


def _local_proof(secret: str, nonce: str) -> str:
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


@router.get("")
def health(nonce: str | None = None, app_container: AppContainer = Depends(get_container)) -> dict[str, object]:
    local_proof = None
    if nonce and app_container.settings.local_secret:
        local_proof = _local_proof(app_container.settings.local_secret, nonce)
    return {
        "data": {
            "status": "ok",
            "app_name": app_container.settings.app_name,
            "environment": app_container.settings.env,
            "local_proof": local_proof,
            "youtube_cookies_from_browser": app_container.settings.youtube_cookies_from_browser,
            "youtube_cookies_disabled": app_container.settings.youtube_cookies_disabled,
        }
    }


@router.post("/shutdown")
def shutdown(
    background_tasks: BackgroundTasks,
    nonce: str,
    proof: str = Header(alias="X-XDownloader-Local-Proof"),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    secret = app_container.settings.local_secret
    if not secret or not hmac.compare_digest(proof, _local_proof(secret, nonce)):
        raise AuthorizationError()
    background_tasks.add_task(os.kill, os.getpid(), signal.SIGTERM)
    return {"data": {"status": "shutting_down"}}
