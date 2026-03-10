"""FastAPI server for local multi-account UI."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
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


service = TradingUIService()
license_manager = LicenseManager()
hub = WebSocketHub()

app = FastAPI(title="Tradingm5 Local UI API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    async def realtime_loop() -> None:
        while True:
            try:
                snapshot = service.get_active_book()
                await hub.broadcast(
                    {
                        "type": "snapshot",
                        "timestamp_utc": _iso_now(),
                        "data": snapshot,
                    }
                )
            except Exception as exc:
                await hub.broadcast(
                    {
                        "type": "error",
                        "timestamp_utc": _iso_now(),
                        "data": {"error": str(exc)},
                    }
                )
            await asyncio.sleep(1.0)

    app.state.realtime_task = asyncio.create_task(realtime_loop())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    task = getattr(app.state, "realtime_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(Exception):
            await task


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
def healthcheck(req: HealthRequest) -> HealthResponse:
    results = service.run_healthcheck(req.account_names, req.symbol)
    return HealthResponse(ok=True, results=results)


@app.get("/api/healthcheck/{account_name}")
def healthcheck_one(account_name: str, symbol: str | None = None) -> dict[str, Any]:
    return service.run_healthcheck_one(account_name=account_name, symbol=symbol)


@app.post("/api/trade/submit-plan", response_model=PlanSubmitResponse)
def submit_plan(req: PlanSubmitRequest) -> PlanSubmitResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    payload = service.submit_plan(
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
def quick_multi(req: QuickMultiOrderRequest) -> QuickMultiOrderResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    payload = service.quick_multi_order(
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
def active_orders() -> ActiveBookResponse:
    data = service.get_active_book()
    return ActiveBookResponse(
        ok=True,
        positions=data["positions"],
        pending_orders=data["pending_orders"],
        total_profit=data["total_profit"],
    )


@app.post("/api/orders/close", response_model=CloseResponse)
def close_order(req: CloseRequest) -> CloseResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    try:
        out = service.close_positions(
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
