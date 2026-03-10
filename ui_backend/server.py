"""FastAPI server for local multi-account UI."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_models import (
    AccountPayload,
    ActiveBookResponse,
    ApiResponse,
    CloseRequest,
    CloseResponse,
    HealthRequest,
    HealthResponse,
    LicenseActivateRequest,
    LicenseStatusResponse,
    PlanSubmitRequest,
    PlanSubmitResponse,
    QuickMultiOrderRequest,
    QuickMultiOrderResponse,
)
from .license_manager import LicenseManager
from .service import TradingUIService


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_pool = ThreadPoolExecutor(max_workers=8)


async def _run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    call = partial(fn, *args, **kwargs) if kwargs else partial(fn, *args)
    return await loop.run_in_executor(_pool, call)


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(ws)

    async def close_all(self) -> None:
        async with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close(code=1001, reason="server shutdown")
            except Exception:
                pass


service = TradingUIService()
license_manager = LicenseManager()
hub = WebSocketHub()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Startup: launch realtime broadcast loop. Shutdown: cancel it and drain the thread pool."""

    async def realtime_loop() -> None:
        while True:
            try:
                snapshot = await _run_blocking(service.get_active_book)
                await hub.broadcast(
                    {
                        "type": "snapshot",
                        "timestamp_utc": _iso_now(),
                        "data": snapshot,
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    await hub.broadcast(
                        {
                            "type": "error",
                            "timestamp_utc": _iso_now(),
                            "data": {"error": str(exc)},
                        }
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await asyncio.sleep(1.0)

    task = asyncio.create_task(realtime_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await hub.close_all()
        _pool.shutdown(wait=False)


app = FastAPI(title="Tradingm5 Local UI API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/system/ping", response_model=ApiResponse)
def ping() -> ApiResponse:
    return ApiResponse(ok=True, message="pong")


@app.get("/api/accounts")
def get_accounts() -> list[dict[str, Any]]:
    return service.get_accounts()


@app.post("/api/accounts")
def upsert_account(payload: AccountPayload) -> dict[str, Any]:
    try:
        return service.upsert_account(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/accounts/{name}", response_model=ApiResponse)
def delete_account(name: str) -> ApiResponse:
    ok = service.delete_account(name)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return ApiResponse(ok=True, message="Account deleted")


@app.post("/api/healthcheck", response_model=HealthResponse)
async def healthcheck(req: HealthRequest) -> HealthResponse:
    results = await _run_blocking(service.run_healthcheck, req.account_names, req.symbol)
    return HealthResponse(ok=True, results=results)


@app.get("/api/healthcheck/{account_name}")
async def healthcheck_one(account_name: str, symbol: str | None = None) -> dict[str, Any]:
    return await _run_blocking(service.run_healthcheck_one, account_name=account_name, symbol=symbol)


@app.post("/api/trade/submit-plan", response_model=PlanSubmitResponse)
async def submit_plan(req: PlanSubmitRequest) -> PlanSubmitResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    payload = await _run_blocking(
        service.submit_plan,
        plan_rows=req.plan_rows,
        timeout_seconds=req.timeout_seconds,
        poll_seconds=req.poll_seconds,
        request_id=req.request_id,
    )
    return PlanSubmitResponse(
        ok=True,
        request_id=payload["request_id"],
        results=payload["results"],
    )


@app.post("/api/trade/quick-multi", response_model=QuickMultiOrderResponse)
async def quick_multi(req: QuickMultiOrderRequest) -> QuickMultiOrderResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    payload = await _run_blocking(
        service.quick_multi_order,
        accounts=req.accounts,
        symbol=req.symbol,
        side=req.side,
        volume=req.volume,
        trigger_price=req.trigger_price,
        sl_price=req.sl_price,
        tp_price=req.tp_price,
        comment=req.comment,
        timeout_seconds=req.timeout_seconds,
        poll_seconds=req.poll_seconds,
        request_id=req.request_id,
    )
    return QuickMultiOrderResponse(
        ok=True,
        request_id=payload["request_id"],
        rows_submitted=int(payload.get("rows_submitted", 0)),
        results=payload["results"],
    )


@app.get("/api/orders/active", response_model=ActiveBookResponse)
async def active_orders() -> ActiveBookResponse:
    data = await _run_blocking(service.get_active_book)
    return ActiveBookResponse(
        ok=True,
        positions=data["positions"],
        pending_orders=data["pending_orders"],
        total_profit=data["total_profit"],
    )


@app.post("/api/orders/close", response_model=CloseResponse)
async def close_order(req: CloseRequest) -> CloseResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    try:
        out = await _run_blocking(
            service.close_positions,
            account_name=req.account,
            symbol=req.symbol,
            side=req.side,
            volume=req.volume,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CloseResponse(ok=True, **out)


@app.get("/api/license/status", response_model=LicenseStatusResponse)
def license_status() -> LicenseStatusResponse:
    status = license_manager.status()
    return LicenseStatusResponse(
        ok=status.status in ("trial_active", "license_valid"),
        status=status.status,  # type: ignore[arg-type]
        expires_at=status.expires_at,
        trial_days_left=status.trial_days_left,
        machine_id=status.machine_id,
        error=status.error,
    )


@app.post("/api/license/activate", response_model=LicenseStatusResponse)
def license_activate(req: LicenseActivateRequest) -> LicenseStatusResponse:
    status = license_manager.activate_from_file(req.license_key_path)
    return LicenseStatusResponse(
        ok=status.status == "license_valid",
        status=status.status,  # type: ignore[arg-type]
        expires_at=status.expires_at,
        trial_days_left=status.trial_days_left,
        machine_id=status.machine_id,
        error=status.error,
    )


@app.websocket("/ws/realtime")
async def ws_realtime(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        await websocket.send_json(
            {
                "type": "status",
                "timestamp_utc": _iso_now(),
                "data": {"message": "connected"},
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)


# Serve local UI assets
_static_dir = Path(__file__).resolve().parent / "web"
if _static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="ui")

    @app.get("/")
    def ui_index() -> FileResponse:
        return FileResponse(_static_dir / "index.html")
