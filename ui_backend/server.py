"""FastAPI server for local multi-account UI."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_models import (
    AccountImportRequest,
    AccountImportContentRequest,
    AccountImportResponse,
    AccountPayload,
    ActiveBookResponse,
    ApiResponse,
    CloseRequest,
    CloseResponse,
    HealthRequest,
    HealthResponse,
    LicenseActivateRequest,
    LicenseActivateContentRequest,
    LicenseRequestCreateRequest,
    LicenseRequestCreateResponse,
    LicenseStatusResponse,
    PortableCreateRequest,
    PortableCreateResponse,
    PlanSubmitRequest,
    PlanSubmitResponse,
    PendingCancelRequest,
    PendingCancelResponse,
    QuickMultiOrderRequest,
    QuickMultiOrderResponse,
)
from .license_manager import LicenseManager, LicenseStatus
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
        self._last_client_activity = datetime.now(timezone.utc)
        self._ever_had_client = False

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
            self._last_client_activity = datetime.now(timezone.utc)
            self._ever_had_client = True

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            self._last_client_activity = datetime.now(timezone.utc)

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

    async def client_count(self) -> int:
        async with self._lock:
            return len(self._clients)

    async def idle_seconds(self) -> float:
        async with self._lock:
            return max((datetime.now(timezone.utc) - self._last_client_activity).total_seconds(), 0.0)

    @property
    def ever_had_client(self) -> bool:
        return self._ever_had_client


service = TradingUIService()
license_manager = LicenseManager(trusted_time_provider=service.get_trusted_time_utc)
hub = WebSocketHub()
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Startup: launch realtime broadcast loop. Shutdown: cancel it and drain the thread pool."""

    async def realtime_loop() -> None:
        idle_shutdown_sec = max(5, int(os.getenv("UI_NO_CLIENT_SHUTDOWN_SECONDS", "25") or "25"))
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
            try:
                count = await hub.client_count()
                if hub.ever_had_client and count == 0 and (await hub.idle_seconds()) >= idle_shutdown_sec:
                    logger.info("No UI clients for %ss, shutting down app process", idle_shutdown_sec)
                    os._exit(0)
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


@app.post("/api/system/shutdown", response_model=ApiResponse)
async def system_shutdown() -> ApiResponse:
    async def _die() -> None:
        await asyncio.sleep(0.4)
        os._exit(0)

    asyncio.create_task(_die())
    return ApiResponse(ok=True, message="Shutting down")


@app.get("/api/accounts")
def get_accounts() -> list[dict[str, Any]]:
    try:
        return service.get_accounts()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts")
def upsert_account(payload: AccountPayload) -> dict[str, Any]:
    try:
        return service.upsert_account(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts/create-portable", response_model=PortableCreateResponse)
async def create_portable_accounts(req: PortableCreateRequest) -> PortableCreateResponse:
    try:
        out = await _run_blocking(
            service.create_portable_copies,
            source_dir=req.source_dir,
            names_csv=req.names_csv,
            target_root=req.target_root,
            append_accounts=req.append_accounts,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        logger.exception("create-portable failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("create-portable success: created=%s target=%s", out.get("created_count"), out.get("target_root"))
    return PortableCreateResponse(ok=True, **out)


@app.post("/api/accounts/import-file", response_model=AccountImportResponse)
async def import_accounts_file(req: AccountImportRequest) -> AccountImportResponse:
    try:
        out = await _run_blocking(service.import_accounts_from_file, req.file_path)
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        logger.exception("import-accounts failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "import-accounts success: imported=%s skipped=%s file=%s",
        out.get("imported_count"),
        out.get("skipped_count"),
        out.get("file_path"),
    )
    return AccountImportResponse(ok=True, **out)


@app.post("/api/accounts/import-content", response_model=AccountImportResponse)
async def import_accounts_content(req: AccountImportContentRequest) -> AccountImportResponse:
    try:
        raw_bytes = base64.b64decode(req.content_b64.encode("utf-8"), validate=True)
        payload = json.loads(raw_bytes.decode("utf-8", errors="strict"))
        out = await _run_blocking(
            service.import_accounts_from_data,
            raw=payload,
            source_path=Path((req.filename or "account.json").strip() or "account.json"),
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid account JSON upload: {exc}") from exc
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        logger.exception("import-accounts-content failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "import-accounts-content success: imported=%s skipped=%s file=%s",
        out.get("imported_count"),
        out.get("skipped_count"),
        out.get("file_path"),
    )
    return AccountImportResponse(ok=True, **out)


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


@app.get("/api/symbols/{account_name}")
async def symbols_for_account(account_name: str, q: str | None = None, limit: int = 30) -> dict[str, Any]:
    try:
        items = await _run_blocking(
            service.search_symbols,
            account_name=account_name,
            query=q,
            limit=limit,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "account": account_name, "count": len(items), "items": items}


@app.get("/api/symbols/validate/{account_name}")
async def validate_symbol(account_name: str, symbol: str) -> dict[str, Any]:
    try:
        out = await _run_blocking(service.validate_symbol, account_name=account_name, symbol=symbol)
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return out


@app.post("/api/trade/submit-plan", response_model=PlanSubmitResponse)
async def submit_plan(req: PlanSubmitRequest) -> PlanSubmitResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    try:
        payload = await _run_blocking(
            service.submit_plan,
            plan_rows=req.plan_rows,
            timeout_seconds=req.timeout_seconds,
            poll_seconds=req.poll_seconds,
            request_id=req.request_id,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
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
    try:
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
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    return QuickMultiOrderResponse(
        ok=True,
        request_id=payload["request_id"],
        rows_submitted=int(payload.get("rows_submitted", 0)),
        results=payload["results"],
    )


@app.get("/api/orders/active", response_model=ActiveBookResponse)
async def active_orders() -> ActiveBookResponse:
    try:
        data = await _run_blocking(service.get_active_book)
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
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
            ticket=req.ticket,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CloseResponse(ok=True, **out)


@app.post("/api/orders/cancel-pending", response_model=PendingCancelResponse)
async def cancel_pending(req: PendingCancelRequest) -> PendingCancelResponse:
    lic = license_manager.status()
    if lic.status in ("trial_expired", "license_invalid"):
        raise HTTPException(status_code=403, detail=lic.error or "License invalid")
    try:
        out = await _run_blocking(
            service.cancel_pending_order,
            account_name=req.account,
            ticket=req.ticket,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PendingCancelResponse(ok=True, **out)


@app.get("/api/history/closed")
async def closed_history(
    account_name: str | None = None,
    days: int = 7,
    limit: int = 300,
    mode: str = "closed",
) -> dict[str, Any]:
    try:
        rows = await _run_blocking(
            service.get_deals_history,
            account_name=account_name,
            days=days,
            limit=limit,
            mode=mode,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "count": len(rows), "mode": mode, "items": rows}


@app.get("/api/system/logs")
def system_logs(limit: int = 20) -> dict[str, Any]:
    items = service.get_log_files(limit=limit)
    return {"ok": True, "count": len(items), "items": items}


@app.get("/api/system/preflight")
async def system_preflight() -> dict[str, Any]:
    try:
        lic = license_manager.status()
    except Exception as exc:
        # Preflight must never 500 on license state persistence/IO errors.
        lic = LicenseStatus(
            status="license_invalid",
            machine_id=license_manager.machine_id,
            error=str(exc),
        )
    try:
        payload = await _run_blocking(
            service.get_preflight_report,
            license_status=lic.status,
            license_error=lic.error,
        )
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trusted_market_time_utc: str | None = None
    try:
        t = await _run_blocking(service.get_trusted_time_utc)
        if t is not None:
            trusted_market_time_utc = t.astimezone(timezone.utc).isoformat()
    except Exception:
        trusted_market_time_utc = None

    return {
        "ok": True,
        **payload,
        "license": {
            "status": lic.status,
            "trial_days_left": lic.trial_days_left,
            "expires_at_utc": lic.expires_at.astimezone(timezone.utc).isoformat() if lic.expires_at else None,
            "machine_id": lic.machine_id,
            "error": lic.error,
        },
        "trusted_market_time_utc": trusted_market_time_utc,
        "license_protection_notes": [
            "Machine-bound fingerprint ties license/trial to hardware tuple.",
            "Multiple integrity anchors (registry + hidden file + signed state chain) detect tamper/reformat attempts.",
            "Trusted market time checks block clock rollback when strict mode is enabled.",
        ],
    }


@app.get("/api/system/mt5-discover")
async def mt5_discover() -> dict[str, Any]:
    try:
        out = await _run_blocking(service.discover_mt5_installations)
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=503, detail="Server shutting down") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **out}


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


@app.post("/api/license/activate-content", response_model=LicenseStatusResponse)
def license_activate_content(req: LicenseActivateContentRequest) -> LicenseStatusResponse:
    try:
        raw_bytes = base64.b64decode(req.content_b64.encode("utf-8"), validate=True)
        raw_text = raw_bytes.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid license file upload: {exc}") from exc
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tf:
        tf.write(raw_text)
        tmp_path = tf.name
    try:
        status = license_manager.activate_from_file(tmp_path)
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
    return LicenseStatusResponse(
        ok=status.status == "license_valid",
        status=status.status,  # type: ignore[arg-type]
        expires_at=status.expires_at,
        trial_days_left=status.trial_days_left,
        machine_id=status.machine_id,
        error=status.error,
    )


@app.post("/api/license/request", response_model=LicenseRequestCreateResponse)
def license_request_create(req: LicenseRequestCreateRequest) -> LicenseRequestCreateResponse:
    try:
        out = license_manager.create_license_request_file(
            output_path=req.output_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LicenseRequestCreateResponse(ok=True, **out)


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
