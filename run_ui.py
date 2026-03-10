"""Run local UI backend server."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "ui_backend.server:app",
        host="127.0.0.1",
        port=8787,
        reload=False,
    )


if __name__ == "__main__":
    main()
