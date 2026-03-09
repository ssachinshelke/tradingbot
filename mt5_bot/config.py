from dataclasses import dataclass
import json
import os
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class BotConfig:
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str | None
    mt5_portable: bool
    default_symbol: str
    risk_per_trade: float
    max_daily_loss_pct: float
    max_open_trades: int
    sl_pips: float
    tp_pips: float
    deviation: int
    magic_number: int
    timeframe: str
    fast_ma: int
    slow_ma: int
    poll_interval_seconds: int
    cooldown_seconds: int
    max_spread_pips: float
    enable_session_filter: bool
    session_start_utc: str
    session_end_utc: str
    journal_path: str
    max_connect_retries: int
    max_symbol_open_trades: int
    max_symbol_volume: float
    enable_break_even: bool
    break_even_trigger_pips: float
    break_even_offset_pips: float
    enable_trailing_stop: bool
    trailing_start_pips: float
    trailing_distance_pips: float
    enable_partial_tp: bool
    partial_tp_trigger_pips: float
    partial_tp_close_pct: float
    accounts_file: str
    dispatch_journal_path: str
    sync_send_delay_ms: int
    strategy_name: str
    strategy_class_path: str | None


@dataclass(frozen=True)
class AccountConfig:
    name: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str | None
    mt5_portable: bool


def _get_required(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def load_config() -> BotConfig:
    enable_session_filter = os.getenv("ENABLE_SESSION_FILTER", "false").strip().lower() in ("1", "true", "yes")
    mt5_portable = os.getenv("MT5_PORTABLE", "false").strip().lower() in ("1", "true", "yes")
    enable_break_even = os.getenv("ENABLE_BREAK_EVEN", "true").strip().lower() in ("1", "true", "yes")
    enable_trailing_stop = os.getenv("ENABLE_TRAILING_STOP", "true").strip().lower() in ("1", "true", "yes")
    enable_partial_tp = os.getenv("ENABLE_PARTIAL_TP", "true").strip().lower() in ("1", "true", "yes")
    return BotConfig(
        mt5_login=int(_get_required("MT5_LOGIN")),
        mt5_password=_get_required("MT5_PASSWORD"),
        mt5_server=_get_required("MT5_SERVER"),
        mt5_path=os.getenv("MT5_PATH", "").strip() or None,
        mt5_portable=mt5_portable,
        default_symbol=os.getenv("DEFAULT_SYMBOL", "EURUSD"),
        risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
        max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03")),
        max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "3")),
        sl_pips=float(os.getenv("SL_PIPS", "25")),
        tp_pips=float(os.getenv("TP_PIPS", "50")),
        deviation=int(os.getenv("DEVIATION", "20")),
        magic_number=int(os.getenv("MAGIC_NUMBER", "20260302")),
        timeframe=os.getenv("TIMEFRAME", "M5").strip().upper(),
        fast_ma=int(os.getenv("FAST_MA", "20")),
        slow_ma=int(os.getenv("SLOW_MA", "50")),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "15")),
        cooldown_seconds=int(os.getenv("COOLDOWN_SECONDS", "60")),
        max_spread_pips=float(os.getenv("MAX_SPREAD_PIPS", "2.5")),
        enable_session_filter=enable_session_filter,
        session_start_utc=os.getenv("SESSION_START_UTC", "06:00"),
        session_end_utc=os.getenv("SESSION_END_UTC", "20:00"),
        journal_path=os.getenv("JOURNAL_PATH", "trade_journal.csv"),
        max_connect_retries=int(os.getenv("MAX_CONNECT_RETRIES", "5")),
        max_symbol_open_trades=int(os.getenv("MAX_SYMBOL_OPEN_TRADES", "2")),
        max_symbol_volume=float(os.getenv("MAX_SYMBOL_VOLUME", "2.0")),
        enable_break_even=enable_break_even,
        break_even_trigger_pips=float(os.getenv("BREAK_EVEN_TRIGGER_PIPS", "10")),
        break_even_offset_pips=float(os.getenv("BREAK_EVEN_OFFSET_PIPS", "1")),
        enable_trailing_stop=enable_trailing_stop,
        trailing_start_pips=float(os.getenv("TRAILING_START_PIPS", "15")),
        trailing_distance_pips=float(os.getenv("TRAILING_DISTANCE_PIPS", "10")),
        enable_partial_tp=enable_partial_tp,
        partial_tp_trigger_pips=float(os.getenv("PARTIAL_TP_TRIGGER_PIPS", "20")),
        partial_tp_close_pct=float(os.getenv("PARTIAL_TP_CLOSE_PCT", "0.5")),
        accounts_file=os.getenv("ACCOUNTS_FILE", "accounts.json"),
        dispatch_journal_path=os.getenv("DISPATCH_JOURNAL_PATH", "dispatch_journal.csv"),
        sync_send_delay_ms=int(os.getenv("SYNC_SEND_DELAY_MS", "300")),
        strategy_name=os.getenv("STRATEGY_NAME", "ma_cross").strip(),
        strategy_class_path=os.getenv("STRATEGY_CLASS_PATH", "").strip() or None,
    )


def load_accounts(path: str) -> list[AccountConfig]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Accounts file not found: {path}")
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Accounts file must contain a JSON array")
    accounts: list[AccountConfig] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Account entry #{idx} must be an object")
        name = str(item.get("name", f"account-{idx}")).strip()
        login = int(item["mt5_login"])
        password = str(item["mt5_password"])
        server = str(item["mt5_server"])
        mt5_path = str(item.get("mt5_path", "")).strip() or None
        mt5_portable = bool(item.get("mt5_portable", False))
        accounts.append(
            AccountConfig(
                name=name,
                mt5_login=login,
                mt5_password=password,
                mt5_server=server,
                mt5_path=mt5_path,
                mt5_portable=mt5_portable,
            )
        )
    if not accounts:
        raise ValueError("Accounts file is empty")
    return accounts
