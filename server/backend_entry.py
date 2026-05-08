from __future__ import annotations

import os

import uvicorn

from app.main import app


def main() -> None:
    host = os.getenv("XDL_HOST", "127.0.0.1")
    port = int(os.getenv("XDL_PORT", "18767"))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
