"""Run local UI backend server."""

from __future__ import annotations

from datetime import datetime
import logging.config
import multiprocessing as mp
from pathlib import Path
import threading
import traceback
from typing import Any
import webbrowser

import uvicorn


def _build_log_config(log_file: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
            },
            "file_default": {
                "class": "logging.FileHandler",
                "formatter": "default",
                "filename": str(log_file),
                "encoding": "utf-8",
            },
            "file_access": {
                "class": "logging.FileHandler",
                "formatter": "access",
                "filename": str(log_file),
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default", "file_default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["default", "file_default"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["access", "file_access"], "level": "INFO", "propagate": False},
        },
    }


def main() -> None:
    # Safety guard: child worker processes must never launch UI/server.
    if mp.parent_process() is not None:
        return
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"ui_backend_{ts}.log"
    crash_file = logs_dir / f"bootstrap_crash_{ts}.log"
    url = "http://127.0.0.1:8787"
    try:
        from ui_backend.server import app
        # Open browser shortly after startup so users can see UI immediately.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8787,
            reload=False,
            timeout_graceful_shutdown=2,
            log_config=_build_log_config(log_file),
        )
    except Exception:
        crash_file.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    mp.freeze_support()
    main()
