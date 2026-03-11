"""API and WebSocket contracts for local UI service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    ok: bool
    message: str | None = None


class AccountPayload(BaseModel):
    name: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str | None = None
    mt5_portable: bool = True


class PortableCreateRequest(BaseModel):
    source_dir: str
    target_root: str | None = None
    names_csv: str = "acc1,acc2"
    append_accounts: bool = True


class PortableCreateResponse(ApiResponse):
    target_root: str
    created_count: int
    created: list[dict[str, Any]] = Field(default_factory=list)


class AccountImportRequest(BaseModel):
    file_path: str = "account.json"


class AccountImportContentRequest(BaseModel):
    filename: str = "account.json"
    content_b64: str


class AccountImportResponse(ApiResponse):
    file_path: str
    imported_count: int
    skipped_count: int = 0
    max_accounts: int
    accounts: list[dict[str, Any]] = Field(default_factory=list)


class AccountView(BaseModel):
    name: str
    mt5_login: int
    mt5_server: str
    mt5_path: str | None = None
    mt5_portable: bool = False
    has_password: bool = True


class HealthRequest(BaseModel):
    account_names: list[str] | None = None
    symbol: str | None = None


class AccountHealth(BaseModel):
    name: str
    login: int | None = None
    ok: bool
    server: str | None = None
    balance: float | None = None
    equity: float | None = None
    symbol: str | None = None
    spread_pips: float | None = None
    error: str | None = None


class HealthResponse(ApiResponse):
    results: list[AccountHealth] = Field(default_factory=list)


class PlanSubmitRequest(BaseModel):
    plan_rows: list[dict[str, Any]]
    timeout_seconds: int = 3600
    poll_seconds: float = 1.0
    request_id: str | None = None


class PlanSubmitResponse(ApiResponse):
    request_id: str
    results: list[dict[str, Any]] = Field(default_factory=list)


class QuickMultiOrderRequest(BaseModel):
    accounts: list[str]
    symbol: str
    side: Literal["buy", "sell"]
    volume: float
    trigger_price: float | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    comment: str = ""
    timeout_seconds: int = 3600
    poll_seconds: float = 1.0
    request_id: str | None = None


class QuickMultiOrderResponse(ApiResponse):
    request_id: str
    rows_submitted: int
    results: list[dict[str, Any]] = Field(default_factory=list)


class PositionRow(BaseModel):
    account: str
    login: int
    ticket: int
    symbol: str
    side: Literal["buy", "sell"]
    volume: float
    price_open: float
    profit: float
    sl: float
    tp: float


class PendingOrderRow(BaseModel):
    account: str
    login: int
    ticket: int
    symbol: str
    order_type: str
    volume: float
    price_open: float
    sl: float
    tp: float


class ActiveBookResponse(ApiResponse):
    positions: list[PositionRow] = Field(default_factory=list)
    pending_orders: list[PendingOrderRow] = Field(default_factory=list)
    total_profit: float = 0.0


class CloseRequest(BaseModel):
    account: str
    symbol: str
    side: Literal["buy", "sell", "all"] = "all"
    volume: float | None = None
    ticket: int | None = None


class CloseResponse(ApiResponse):
    account: str
    symbol: str
    closed_count: int = 0
    details: list[dict[str, Any]] = Field(default_factory=list)


class PendingCancelRequest(BaseModel):
    account: str
    ticket: int


class PendingCancelResponse(ApiResponse):
    account: str
    ticket: int
    symbol: str | None = None
    retcode: int | None = None
    order: int | None = None
    comment: str | None = None


class LicenseStatusResponse(ApiResponse):
    status: Literal["trial_active", "trial_expired", "license_valid", "license_invalid"]
    expires_at: datetime | None = None
    trial_days_left: int | None = None
    machine_id: str | None = None
    error: str | None = None


class LicenseActivateRequest(BaseModel):
    license_key_path: str


class LicenseActivateContentRequest(BaseModel):
    filename: str = "license.json"
    content_b64: str


class LicenseRequestCreateRequest(BaseModel):
    output_path: str = "license_request.json"


class LicenseRequestCreateResponse(ApiResponse):
    file_path: str
    machine_hash: str
    requested_at_utc: str


class StreamPayload(BaseModel):
    type: Literal["snapshot", "status", "error"]
    timestamp_utc: str
    data: dict[str, Any]
