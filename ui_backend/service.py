"""Backend orchestration service for local UI."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
from threading import Lock
from typing import Any
from uuid import uuid4

from mt5_bot import mt5
from mt5_bot.advanced_plan import execute_advanced_order_plan, parse_advanced_order_rows
from mt5_bot.client import TradingBot
from mt5_bot.config import AccountConfig, BotConfig, load_config

logger = logging.getLogger("uvicorn.error")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _order_type_name(order_type: int) -> str:
    mapping = {
        0: "BUY",
        1: "SELL",
        2: "BUY_LIMIT",
        3: "SELL_LIMIT",
        4: "BUY_STOP",
        5: "SELL_STOP",
        6: "BUY_STOP_LIMIT",
        7: "SELL_STOP_LIMIT",
    }
    return mapping.get(int(order_type), str(order_type))


class TradingUIService:
    def __init__(self) -> None:
        self.cfg: BotConfig = self._load_config_for_ui()
        if not os.getenv("ACCOUNTS_FILE", "").strip():
            # UI/release mode uses a single default account file for simpler onboarding.
            self.cfg = replace(self.cfg, accounts_file="account.json")
        self._accounts_file = Path(self.cfg.accounts_file)
        self._req_lock = Lock()
        self._journal_lock = Lock()
        self._request_cache: dict[str, dict[str, Any]] = {}
        self._closed_journal_path = Path("logs") / "closed_deals_journal.jsonl"
        self._closed_journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_ui_accounts = max(1, int(os.getenv("UI_MAX_ACCOUNTS", "2") or "2"))
        self._ensure_default_accounts_file()

    def _ensure_default_accounts_file(self) -> None:
        if self._accounts_file.exists():
            return
        self._accounts_file.parent.mkdir(parents=True, exist_ok=True)
        self._accounts_file.write_text("[]", encoding="utf-8")

    @staticmethod
    def _load_config_for_ui() -> BotConfig:
        """UI should still boot even when .env MT5 credentials are absent."""
        try:
            return load_config()
        except Exception:
            return BotConfig(
                mt5_login=0,
                mt5_password="",
                mt5_server="",
                mt5_path=os.getenv("MT5_PATH", "").strip() or None,
                mt5_portable=os.getenv("MT5_PORTABLE", "false").strip().lower() in ("1", "true", "yes"),
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
                enable_session_filter=os.getenv("ENABLE_SESSION_FILTER", "false").strip().lower() in ("1", "true", "yes"),
                session_start_utc=os.getenv("SESSION_START_UTC", "06:00"),
                session_end_utc=os.getenv("SESSION_END_UTC", "20:00"),
                journal_path=os.getenv("JOURNAL_PATH", "trade_journal.csv"),
                max_connect_retries=int(os.getenv("MAX_CONNECT_RETRIES", "5")),
                max_symbol_open_trades=int(os.getenv("MAX_SYMBOL_OPEN_TRADES", "2")),
                max_symbol_volume=float(os.getenv("MAX_SYMBOL_VOLUME", "2.0")),
                enable_break_even=os.getenv("ENABLE_BREAK_EVEN", "true").strip().lower() in ("1", "true", "yes"),
                break_even_trigger_pips=float(os.getenv("BREAK_EVEN_TRIGGER_PIPS", "10")),
                break_even_offset_pips=float(os.getenv("BREAK_EVEN_OFFSET_PIPS", "1")),
                enable_trailing_stop=os.getenv("ENABLE_TRAILING_STOP", "true").strip().lower() in ("1", "true", "yes"),
                trailing_start_pips=float(os.getenv("TRAILING_START_PIPS", "15")),
                trailing_distance_pips=float(os.getenv("TRAILING_DISTANCE_PIPS", "10")),
                enable_partial_tp=os.getenv("ENABLE_PARTIAL_TP", "true").strip().lower() in ("1", "true", "yes"),
                partial_tp_trigger_pips=float(os.getenv("PARTIAL_TP_TRIGGER_PIPS", "20")),
                partial_tp_close_pct=float(os.getenv("PARTIAL_TP_CLOSE_PCT", "0.5")),
                accounts_file=os.getenv("ACCOUNTS_FILE", "account.json"),
                dispatch_journal_path=os.getenv("DISPATCH_JOURNAL_PATH", "dispatch_journal.csv"),
                sync_send_delay_ms=int(os.getenv("SYNC_SEND_DELAY_MS", "300")),
                strategy_name=os.getenv("STRATEGY_NAME", "ma_cross").strip(),
                strategy_class_path=os.getenv("STRATEGY_CLASS_PATH", "").strip() or None,
            )

    def _load_accounts(self) -> list[AccountConfig]:
        path = self._accounts_file
        if not path.exists():
            self._ensure_default_accounts_file()
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Accounts file must contain a JSON array")
        accounts: list[AccountConfig] = []
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Account entry #{idx} must be an object")
            login = int(item.get("mt5_login", 0) or 0)
            # Treat login=0 rows as editable templates, not active accounts.
            if login <= 0:
                continue
            server = str(item.get("mt5_server", "")).strip()
            password = str(item.get("mt5_password", ""))
            if not server or not password:
                continue
            accounts.append(
                AccountConfig(
                    name=str(item.get("name", f"account-{idx}")).strip(),
                    mt5_login=login,
                    mt5_password=password,
                    mt5_server=server,
                    mt5_path=(str(item.get("mt5_path", "")).strip() or None),
                    mt5_portable=bool(item.get("mt5_portable", False)),
                )
            )
        return accounts

    def _save_accounts(self, accounts: list[AccountConfig]) -> None:
        serializable = [asdict(a) for a in accounts]
        self._accounts_file.parent.mkdir(parents=True, exist_ok=True)
        self._accounts_file.write_text(
            json.dumps(serializable, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _make_account_config(base: BotConfig, account: AccountConfig) -> BotConfig:
        return replace(
            base,
            mt5_login=account.mt5_login,
            mt5_password=account.mt5_password,
            mt5_server=account.mt5_server,
            mt5_path=account.mt5_path or base.mt5_path,
            mt5_portable=account.mt5_portable,
        )

    def get_accounts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for acc in self._load_accounts():
            rows.append(
                {
                    "name": acc.name,
                    "mt5_login": acc.mt5_login,
                    "mt5_server": acc.mt5_server,
                    "mt5_path": acc.mt5_path,
                    "mt5_portable": acc.mt5_portable,
                    "has_password": bool(acc.mt5_password),
                }
            )
        return rows

    def get_trusted_time_utc(self) -> datetime | None:
        """Try to read broker/server time from any configured account."""
        for account in self._load_accounts():
            bot = TradingBot(self._make_account_config(self.cfg, account))
            try:
                bot.start()
                snap = bot.client.account_snapshot()
                ts = snap.time
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                return ts
            except Exception:
                continue
            finally:
                try:
                    bot.stop()
                except Exception:
                    pass
        return None

    def search_symbols(
        self,
        account_name: str,
        query: str | None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        account = next((a for a in self._load_accounts() if a.name == account_name), None)
        if account is None:
            raise ValueError(f"Unknown account: {account_name}")
        bot = TradingBot(self._make_account_config(self.cfg, account))
        q = (query or "").strip().lower()
        max_items = max(1, min(int(limit), 200))
        try:
            bot.start()
            if q:
                symbols = bot.client.symbols_get(group=f"*{q}*")
                if not symbols:
                    symbols = bot.client.symbols_get()
            else:
                symbols = bot.client.symbols_get()
        finally:
            try:
                bot.stop()
            except Exception:
                pass

        matched: list[dict[str, Any]] = []
        for s in symbols:
            name = str(s.get("name", "") or "")
            path = str(s.get("path", "") or "")
            desc = str(s.get("description", "") or "")
            hay = f"{name} {path} {desc}".lower()
            if q and q not in hay:
                continue
            matched.append(
                {
                    "name": name,
                    "description": desc,
                    "path": path,
                    "visible": bool(s.get("visible", False)),
                }
            )

        if q:
            matched.sort(key=lambda row: (0 if row["name"].lower().startswith(q) else 1, row["name"]))
        else:
            matched.sort(key=lambda row: row["name"])
        return matched[:max_items]

    def validate_symbol(self, account_name: str, symbol: str) -> dict[str, Any]:
        account = next((a for a in self._load_accounts() if a.name == account_name), None)
        if account is None:
            return {"ok": False, "account": account_name, "symbol": symbol, "error": "Unknown account"}
        bot = TradingBot(self._make_account_config(self.cfg, account))
        try:
            bot.start()
            bot.client.ensure_symbol(symbol)
            return {"ok": True, "account": account_name, "symbol": symbol}
        except Exception as exc:
            return {"ok": False, "account": account_name, "symbol": symbol, "error": str(exc)}
        finally:
            try:
                bot.stop()
            except Exception:
                pass

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        accounts = self._load_accounts()
        incoming_name = str(payload["name"]).strip()
        exists = any(a.name == incoming_name for a in accounts)
        if not exists and len(accounts) >= self._max_ui_accounts:
            raise ValueError(
                f"Account limit reached ({self._max_ui_accounts}). "
                "Delete an account before adding a new one."
            )
        updated = AccountConfig(
            name=incoming_name,
            mt5_login=int(payload["mt5_login"]),
            mt5_password=str(payload["mt5_password"]),
            mt5_server=str(payload["mt5_server"]),
            mt5_path=(str(payload.get("mt5_path", "")).strip() or None),
            mt5_portable=bool(payload.get("mt5_portable", False)),
        )
        out: list[AccountConfig] = []
        replaced = False
        for item in accounts:
            if item.name == updated.name:
                out.append(updated)
                replaced = True
            else:
                out.append(item)
        if not replaced:
            out.append(updated)
        self._save_accounts(out)
        return {
            "name": updated.name,
            "mt5_login": updated.mt5_login,
            "mt5_server": updated.mt5_server,
            "mt5_path": updated.mt5_path,
            "mt5_portable": updated.mt5_portable,
            "has_password": bool(updated.mt5_password),
        }

    def import_accounts_from_file(self, file_path: str) -> dict[str, Any]:
        src = Path((file_path or "").strip() or "account.json")
        if not src.exists():
            raise ValueError(f"Accounts file not found: {src}")
        raw = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Accounts import file must contain a JSON array")
        imported: list[AccountConfig] = []
        skipped = 0
        seen: set[str] = set()
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Account entry #{idx} must be an object")
            name = str(item.get("name", f"account-{idx}")).strip()
            if not name:
                raise ValueError(f"Account entry #{idx} has empty name")
            if name in seen:
                continue
            seen.add(name)
            login = int(item.get("mt5_login", 0) or 0)
            server = str(item.get("mt5_server", "")).strip()
            password = str(item.get("mt5_password", ""))
            if login <= 0 or not server or not password:
                skipped += 1
                continue
            imported.append(
                AccountConfig(
                    name=name,
                    mt5_login=login,
                    mt5_password=password,
                    mt5_server=server,
                    mt5_path=(str(item.get("mt5_path", "")).strip() or None),
                    mt5_portable=bool(item.get("mt5_portable", False)),
                )
            )
        if len(imported) > self._max_ui_accounts:
            raise ValueError(
                f"Import has {len(imported)} accounts, but max allowed is {self._max_ui_accounts}."
            )
        self._accounts_file = src
        self._save_accounts(imported)
        return {
            "file_path": str(src.resolve()),
            "imported_count": len(imported),
            "skipped_count": skipped,
            "max_accounts": self._max_ui_accounts,
            "accounts": self.get_accounts(),
        }

    def create_portable_copies(
        self,
        source_dir: str,
        names_csv: str,
        target_root: str | None,
        append_accounts: bool = True,
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise ValueError("Auto-create portable MT5 folders is currently supported on Windows only.")
        source = Path(str(source_dir).strip())
        terminal = source / "terminal64.exe"
        if not terminal.exists():
            raise ValueError(f"terminal64.exe not found in source folder: {source}")
        root = Path(target_root.strip()) if target_root and str(target_root).strip() else Path("mt5-portable")
        root.mkdir(parents=True, exist_ok=True)
        names = [n.strip() for n in (names_csv or "").split(",") if n.strip()]
        if not names:
            names = ["acc1", "acc2"]
        if not names:
            raise ValueError("Provide at least one copy name (comma-separated).")

        created: list[dict[str, Any]] = []
        for name in names:
            dst = root / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(source, dst)
            bat = dst / "start-portable.bat"
            bat.write_text('@echo off\r\nstart "" "%~dp0terminal64.exe" /portable\r\n', encoding="utf-8")
            created.append(
                {
                    "name": name,
                    "mt5_path": str(dst / "terminal64.exe"),
                    "start_script": str(bat),
                }
            )

        if append_accounts and created:
            accounts = self._load_accounts()
            existing_names = {a.name for a in accounts}
            free_slots = max(0, self._max_ui_accounts - len(accounts))
            to_append = []
            for item in created:
                if item["name"] in existing_names:
                    continue
                if len(to_append) >= free_slots:
                    break
                to_append.append(
                    AccountConfig(
                        name=item["name"],
                        mt5_login=0,
                        mt5_password="fill_me",
                        mt5_server="MetaQuotes-Demo",
                        mt5_path=item["mt5_path"],
                        mt5_portable=True,
                    )
                )
            if to_append:
                self._save_accounts(accounts + to_append)

        # Keep a single local account.json and update created portable paths in it.
        account_json_template = Path("account.json")
        existing_rows: list[dict[str, Any]] = []
        if account_json_template.exists():
            try:
                raw = json.loads(account_json_template.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    existing_rows = [r for r in raw if isinstance(r, dict)]
            except Exception:
                existing_rows = []
        by_name: dict[str, dict[str, Any]] = {}
        for row in existing_rows:
            name = str(row.get("name", "")).strip()
            if name:
                by_name[name] = dict(row)
        for item in created:
            name = str(item["name"])
            current = by_name.get(name, {})
            current.setdefault("name", name)
            current.setdefault("mt5_login", 0)
            current.setdefault("mt5_password", "fill_me")
            current.setdefault("mt5_server", "MetaQuotes-Demo")
            current["mt5_path"] = item["mt5_path"]
            current["mt5_portable"] = True
            by_name[name] = current
        merged = list(by_name.values())
        account_json_template.write_text(json.dumps(merged, indent=2), encoding="utf-8")

        return {
            "target_root": str(root.resolve()),
            "created_count": len(created),
            "created": created,
            "max_accounts": self._max_ui_accounts,
        }

    def delete_account(self, name: str) -> bool:
        accounts = self._load_accounts()
        kept = [a for a in accounts if a.name != name]
        if len(kept) == len(accounts):
            return False
        self._save_accounts(kept)
        return True

    def run_healthcheck(self, account_names: list[str] | None, symbol: str | None) -> list[dict[str, Any]]:
        accounts = self._load_accounts()
        if account_names:
            selected = set(account_names)
            accounts = [a for a in accounts if a.name in selected]
        if not accounts:
            return []
        check_symbol = symbol or self.cfg.default_symbol
        # Frozen Windows builds can be fragile with multiprocessing workers.
        # Prefer deterministic in-process checks for UI reliability.
        rows: list[dict[str, Any]] = []
        for account in accounts:
            bot = TradingBot(self._make_account_config(self.cfg, account))
            try:
                bot.start()
                snap = bot.client.account_snapshot()
                bot.client.ensure_symbol(check_symbol)
                spread = bot.spread_in_pips(check_symbol)
                rows.append(
                    {
                        "name": account.name,
                        "login": account.mt5_login,
                        "ok": True,
                        "server": snap.server,
                        "balance": snap.balance,
                        "equity": snap.equity,
                        "symbol": check_symbol,
                        "spread_pips": spread,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "name": account.name,
                        "login": account.mt5_login,
                        "ok": False,
                        "error": str(exc),
                    }
                )
            finally:
                try:
                    bot.stop()
                except Exception:
                    pass
        return rows

    def run_healthcheck_one(self, account_name: str, symbol: str | None) -> dict[str, Any]:
        rows = self.run_healthcheck([account_name], symbol)
        if not rows:
            return {
                "name": account_name,
                "ok": False,
                "error": f"Account not found: {account_name}",
            }
        return rows[0]

    def _collect_book_for_account(self, account: AccountConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
        cfg = self._make_account_config(self.cfg, account)
        bot = TradingBot(cfg)
        positions_out: list[dict[str, Any]] = []
        pending_out: list[dict[str, Any]] = []
        total_profit = 0.0
        try:
            bot.start()
            positions = bot.client.positions()
            for p in positions:
                side = "buy" if int(p.type) == int(mt5.POSITION_TYPE_BUY) else "sell"
                profit = float(getattr(p, "profit", 0.0) or 0.0)
                total_profit += profit
                positions_out.append(
                    {
                        "account": account.name,
                        "login": account.mt5_login,
                        "ticket": int(p.ticket),
                        "symbol": str(p.symbol),
                        "side": side,
                        "volume": float(p.volume),
                        "price_open": float(p.price_open),
                        "profit": profit,
                        "sl": float(p.sl),
                        "tp": float(p.tp),
                    }
                )
            orders = bot.client.active_orders()
            for o in orders:
                pending_out.append(
                    {
                        "account": account.name,
                        "login": account.mt5_login,
                        "ticket": int(o.ticket),
                        "symbol": str(o.symbol),
                        "order_type": _order_type_name(int(o.type)),
                        "volume": float(o.volume_current),
                        "price_open": float(o.price_open),
                        "sl": float(o.sl),
                        "tp": float(o.tp),
                    }
                )
        finally:
            try:
                bot.stop()
            except Exception:
                pass
        return positions_out, pending_out, total_profit

    def get_active_book(self) -> dict[str, Any]:
        positions: list[dict[str, Any]] = []
        pending_orders: list[dict[str, Any]] = []
        total_profit = 0.0
        for account in self._load_accounts():
            try:
                pos, ords, profit = self._collect_book_for_account(account)
                positions.extend(pos)
                pending_orders.extend(ords)
                total_profit += profit
            except Exception:
                continue
        return {
            "positions": positions,
            "pending_orders": pending_orders,
            "total_profit": round(total_profit, 2),
        }

    def get_deals_history(
        self,
        account_name: str | None,
        days: int = 7,
        limit: int = 300,
        mode: str = "closed",
    ) -> list[dict[str, Any]]:
        days_safe = max(1, min(int(days), 365))
        limit_safe = max(1, min(int(limit), 2000))
        to_dt = datetime.now(timezone.utc)
        if days_safe == 1:
            # "Today" should align to local calendar day, not rolling 24h.
            now_local = datetime.now().astimezone()
            start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=now_local.tzinfo)
            from_dt = start_local.astimezone(timezone.utc)
        else:
            from_dt = to_dt - timedelta(days=days_safe)
        mode_safe = (mode or "closed").strip().lower()
        if mode_safe not in {"closed", "all"}:
            mode_safe = "closed"

        accounts = self._load_accounts()
        if account_name:
            accounts = [a for a in accounts if a.name == account_name]
        if not accounts:
            return []

        deal_entry_in = int(getattr(mt5, "DEAL_ENTRY_IN", 0))
        rows: list[dict[str, Any]] = []

        for account in accounts:
            bot = TradingBot(self._make_account_config(self.cfg, account))
            try:
                bot.start()
                deals = bot.client.history_deals(date_from=from_dt, date_to=to_dt)
                if not deals:
                    # Some terminals/brokers may not return recent-window records reliably.
                    # Fallback to full history and filter client-side.
                    deals = bot.client.history_deals()
                for d in deals:
                    # Keep all non-entry deals (OUT / OUT_BY / INOUT etc.) to avoid
                    # missing broker-specific close entry codes.
                    entry = int(getattr(d, "entry", -1))
                    if mode_safe == "closed" and entry == deal_entry_in:
                        continue
                    t = int(getattr(d, "time", 0) or 0)
                    t_msc = int(getattr(d, "time_msc", 0) or 0)
                    if t <= 0 and t_msc > 0:
                        t = int(t_msc // 1000)
                    if t <= 0:
                        continue
                    ts = datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t > 0 else None
                    when = datetime.fromtimestamp(t, tz=timezone.utc)
                    if when < from_dt:
                        continue
                    side = "buy" if int(getattr(d, "type", -1)) == int(getattr(mt5, "DEAL_TYPE_BUY", 0)) else "sell"
                    rows.append(
                        {
                            "record_kind": "deal",
                            "account": account.name,
                            "login": account.mt5_login,
                            "deal_ticket": int(getattr(d, "ticket", 0) or 0),
                            "order_ticket": int(getattr(d, "order", 0) or 0),
                            "position_id": int(getattr(d, "position_id", 0) or 0),
                            "symbol": str(getattr(d, "symbol", "") or ""),
                            "side": side,
                            "volume": float(getattr(d, "volume", 0.0) or 0.0),
                            "price": float(getattr(d, "price", 0.0) or 0.0),
                            "profit": float(getattr(d, "profit", 0.0) or 0.0),
                            "swap": float(getattr(d, "swap", 0.0) or 0.0),
                            "commission": float(getattr(d, "commission", 0.0) or 0.0),
                            "comment": str(getattr(d, "comment", "") or ""),
                            "entry_type": entry,
                            "executed_at_utc": ts,
                        }
                    )
                if mode_safe == "all":
                    orders = bot.client.history_orders(date_from=from_dt, date_to=to_dt)
                    if not orders:
                        orders = bot.client.history_orders()
                    for o in orders:
                        order_ticket = int(getattr(o, "ticket", 0) or 0)
                        setup = int(getattr(o, "time_setup", 0) or 0)
                        done = int(getattr(o, "time_done", 0) or 0)
                        ts_epoch = done if done > 0 else setup
                        if ts_epoch <= 0:
                            continue
                        when = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
                        if when < from_dt:
                            continue
                        order_type = int(getattr(o, "type", -1) or -1)
                        side = "buy" if order_type in {0, 2, 4, 6} else "sell"
                        rows.append(
                            {
                                "record_kind": "order",
                                "account": account.name,
                                "login": account.mt5_login,
                                "deal_ticket": 0,
                                "order_ticket": order_ticket,
                                "position_id": int(getattr(o, "position_id", 0) or 0),
                                "symbol": str(getattr(o, "symbol", "") or ""),
                                "side": side,
                                "volume": float(getattr(o, "volume_initial", 0.0) or 0.0),
                                "price": float(getattr(o, "price_open", 0.0) or 0.0),
                                "profit": 0.0,
                                "swap": 0.0,
                                "commission": 0.0,
                                "comment": str(getattr(o, "comment", "") or ""),
                                "entry_type": -1,
                                "executed_at_utc": when.isoformat(),
                            }
                        )
            except Exception:
                # Never fail the whole history endpoint due to one bad account
                # (e.g., temporary MT5 session/login issue).
                continue
            finally:
                try:
                    bot.stop()
                except Exception:
                    pass

        rows.sort(key=lambda r: r.get("executed_at_utc") or "", reverse=True)
        journal_rows = (
            self._load_closed_journal(account_name=account_name, from_dt=from_dt)
            if mode_safe in {"closed", "all"}
            else []
        )
        rows.extend(journal_rows)
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in sorted(rows, key=lambda r: r.get("executed_at_utc") or "", reverse=True):
            key = (
                f"{row.get('record_kind','')}|{row.get('account','')}|{row.get('deal_ticket',0)}|"
                f"{row.get('order_ticket',0)}|{row.get('position_id',0)}|"
                f"{row.get('entry_type','')}|{row.get('executed_at_utc','')}"
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped[:limit_safe]

    def get_closed_deals(
        self,
        account_name: str | None,
        days: int = 7,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Backward-compatible wrapper for old endpoint callers."""
        return self.get_deals_history(
            account_name=account_name,
            days=days,
            limit=limit,
            mode="closed",
        )

    def get_log_files(self, limit: int = 20) -> list[dict[str, Any]]:
        logs_dir = Path("logs")
        if not logs_dir.exists():
            return []
        files = sorted(
            logs_dir.glob("ui_backend_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        out: list[dict[str, Any]] = []
        for p in files[: max(1, min(int(limit), 200))]:
            st = p.stat()
            out.append(
                {
                    "name": p.name,
                    "path": str(p.resolve()),
                    "size_bytes": int(st.st_size),
                    "modified_at_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return out

    def get_preflight_report(self, license_status: str, license_error: str | None = None) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add_check(code: str, title: str, ok: bool, message: str, *, severity: str = "fail") -> None:
            checks.append(
                {
                    "code": code,
                    "title": title,
                    "ok": bool(ok),
                    "severity": severity,
                    "message": message,
                }
            )

        # License must be active or valid for production trading.
        lic_ok = license_status in {"trial_active", "license_valid"}
        add_check(
            "license",
            "License status",
            lic_ok,
            license_status if lic_ok else (license_error or f"License state: {license_status}"),
        )

        accounts: list[AccountConfig] = []
        accounts_load_ok = True
        accounts_load_error = ""
        try:
            accounts = self._load_accounts()
        except Exception as exc:
            accounts_load_ok = False
            accounts_load_error = str(exc)
            logger.exception("Preflight accounts load failed: %s", exc)

        count_ok = accounts_load_ok and (1 <= len(accounts) <= self._max_ui_accounts)
        add_check(
            "accounts_count",
            "Accounts configured",
            count_ok,
            (
                f"{len(accounts)} configured (limit {self._max_ui_accounts})"
                if accounts_load_ok
                else f"Could not parse accounts file: {accounts_load_error}"
            ),
        )

        # MT5 module/runtime sanity.
        mt5_ok = False
        mt5_msg = "MetaTrader5 module unavailable"
        try:
            init_fn = getattr(mt5, "initialize", None)
            mt5_ok = callable(init_fn)
            mt5_msg = "MetaTrader5 API import is available" if mt5_ok else mt5_msg
        except Exception as exc:
            mt5_msg = str(exc)
        add_check("mt5_runtime", "MT5 runtime", mt5_ok, mt5_msg)

        # Platform note: macOS build may run UI but MT5 runtime is Windows-first.
        is_windows = os.name == "nt"
        add_check(
            "platform",
            "Platform suitability",
            is_windows,
            "Windows runtime detected" if is_windows else "Non-Windows runtime (trading may be limited)",
            severity="warn",
        )

        # Accounts file exists and is writable.
        file_ok = self._accounts_file.exists()
        add_check(
            "accounts_file",
            "Accounts file",
            file_ok,
            str(self._accounts_file.resolve()) if file_ok else f"Missing file: {self._accounts_file}",
        )

        # Logs writable.
        logs_ok = True
        logs_msg = "Logs folder is writable"
        try:
            logs_dir = Path("logs")
            logs_dir.mkdir(parents=True, exist_ok=True)
            probe = logs_dir / ".preflight_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            logs_ok = False
            logs_msg = f"Cannot write logs: {exc}"
        add_check("logs_write", "Log write access", logs_ok, logs_msg)

        # Broker/account reachability check.
        if accounts_load_ok and accounts:
            try:
                results = self.run_healthcheck(None, self.cfg.default_symbol)
                ok_count = sum(1 for r in results if r.get("ok"))
                add_check(
                    "healthcheck",
                    "Account healthcheck",
                    ok_count == len(results) and len(results) > 0,
                    f"{ok_count}/{len(results)} accounts healthy",
                )
            except Exception as exc:
                add_check("healthcheck", "Account healthcheck", False, str(exc))
        elif not accounts_load_ok:
            add_check("healthcheck", "Account healthcheck", False, "Skipped due to accounts file parse error")
        else:
            add_check("healthcheck", "Account healthcheck", False, "No accounts configured")

        hard_fail = [c for c in checks if not c["ok"] and c["severity"] == "fail"]
        warn_only = [c for c in checks if not c["ok"] and c["severity"] == "warn"]
        status = "green" if not hard_fail else "red"

        return {
            "status": status,
            "ready_to_trade": len(hard_fail) == 0,
            "checked_at_utc": _now_iso(),
            "summary": {
                "pass": sum(1 for c in checks if c["ok"]),
                "fail": len(hard_fail),
                "warn": len(warn_only),
                "total": len(checks),
            },
            "checks": checks,
        }

    def discover_mt5_installations(self) -> dict[str, Any]:
        """Best-effort MT5 terminal discovery for UI defaults."""
        if os.name != "nt":
            return {
                "platform": os.name,
                "count": 0,
                "items": [],
                "default_source_dir": "",
                "install_required": True,
                "message": "Auto-detection is available on Windows only.",
            }

        hits: list[str] = []
        seen: set[str] = set()

        def add_if_terminal(path: Path) -> None:
            exe = path if path.name.lower().startswith("terminal") else (path / "terminal64.exe")
            if exe.exists():
                p = str(exe.resolve())
                key = p.lower()
                if key not in seen:
                    seen.add(key)
                    hits.append(p)

        # Fast common-location checks first.
        common_dirs = [
            Path(r"C:\Program Files\MetaTrader 5"),
            Path(r"C:\Program Files (x86)\MetaTrader 5"),
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "MetaTrader 5",
        ]
        for d in common_dirs:
            if d.exists():
                add_if_terminal(d)

        # Broader best-effort search if nothing found.
        if not hits:
            roots = [
                Path(r"C:\Program Files"),
                Path(r"C:\Program Files (x86)"),
                Path(os.getenv("LOCALAPPDATA", "")),
                Path(os.getenv("APPDATA", "")),
            ]
            max_hits = 8
            max_dirs = 4000
            walked = 0
            for root in roots:
                if not root.exists():
                    continue
                for dirpath, _, filenames in os.walk(root):
                    walked += 1
                    if walked > max_dirs or len(hits) >= max_hits:
                        break
                    lowered = {f.lower() for f in filenames}
                    if "terminal64.exe" in lowered:
                        add_if_terminal(Path(dirpath))
                    elif "terminal.exe" in lowered:
                        exe = Path(dirpath) / "terminal.exe"
                        if exe.exists():
                            p = str(exe.resolve())
                            key = p.lower()
                            if key not in seen:
                                seen.add(key)
                                hits.append(p)
                if walked > max_dirs or len(hits) >= max_hits:
                    break

        default_source = ""
        if hits:
            default_source = str(Path(hits[0]).parent)
        else:
            default_source = r"C:\Program Files\MetaTrader 5"

        portable_root = Path("mt5-portable")
        existing_portable: list[str] = []
        if portable_root.exists():
            for p in sorted(portable_root.iterdir()):
                if p.is_dir() and (p / "terminal64.exe").exists():
                    existing_portable.append(p.name)

        return {
            "platform": "windows",
            "count": len(hits),
            "items": hits,
            "default_source_dir": default_source,
            "install_required": len(hits) == 0,
            "portable_root": str(portable_root.resolve()),
            "portable_count": len(existing_portable),
            "portable_items": existing_portable,
            "message": (
                "MT5 detected." if hits else
                "MT5 terminal not found. Install MetaTrader 5 first, then retry."
            ),
        }

    def close_positions(
        self,
        account_name: str,
        symbol: str,
        side: str,
        volume: float | None,
        ticket: int | None = None,
    ) -> dict[str, Any]:
        account = next((a for a in self._load_accounts() if a.name == account_name), None)
        if account is None:
            raise ValueError(f"Unknown account: {account_name}")
        bot = TradingBot(self._make_account_config(self.cfg, account))
        closed: list[dict[str, Any]] = []
        try:
            bot.start()
            positions = bot.client.positions(symbol=symbol)
            if ticket is not None:
                positions = [p for p in positions if int(getattr(p, "ticket", 0)) == int(ticket)]
            if side == "buy":
                positions = [p for p in positions if int(p.type) == int(mt5.POSITION_TYPE_BUY)]
            elif side == "sell":
                positions = [p for p in positions if int(p.type) == int(mt5.POSITION_TYPE_SELL)]
            for p in positions:
                req_volume = volume if volume is not None else None
                if req_volume is not None and req_volume > float(p.volume):
                    req_volume = float(p.volume)
                position_id = int(getattr(p, "ticket", 0) or 0)
                result = bot.client.close_position(
                    p,
                    volume=req_volume,
                    comment="ui-close",
                )
                retcode = int(result.get("retcode", 0) or 0)
                deal_ticket = int(result.get("deal", 0) or 0)
                detail: dict[str, Any] = {
                    "ticket": int(p.ticket),
                    "retcode": retcode,
                    "deal": deal_ticket,
                    "order": result.get("order"),
                }
                # Enrich response from deal history if available.
                if deal_ticket > 0:
                    try:
                        deal_rows = bot.client.history_deals(ticket=deal_ticket)
                        if deal_rows:
                            d = deal_rows[0]
                            d_time = int(getattr(d, "time", 0) or 0)
                            detail["profit"] = float(getattr(d, "profit", 0.0) or 0.0)
                            detail["price"] = float(getattr(d, "price", 0.0) or 0.0)
                            detail["volume"] = float(getattr(d, "volume", 0.0) or 0.0)
                            detail["executed_at_utc"] = (
                                datetime.fromtimestamp(d_time, tz=timezone.utc).isoformat() if d_time > 0 else _now_iso()
                            )
                            detail["position_id"] = int(getattr(d, "position_id", position_id) or position_id)
                    except Exception:
                        pass

                if "executed_at_utc" not in detail:
                    detail["executed_at_utc"] = _now_iso()
                if "position_id" not in detail:
                    detail["position_id"] = position_id
                if "volume" not in detail:
                    detail["volume"] = float(req_volume if req_volume is not None else float(p.volume))
                if "price" not in detail:
                    detail["price"] = float(getattr(p, "price_current", 0.0) or getattr(p, "price_open", 0.0) or 0.0)
                if "profit" not in detail:
                    detail["profit"] = 0.0

                # Persist instant closed-deal journal for UI visibility even when broker history lags.
                self._append_closed_journal(
                    {
                        "account": account_name,
                        "login": account.mt5_login,
                        "deal_ticket": int(detail.get("deal", 0) or 0),
                        "order_ticket": int(detail.get("order", 0) or 0),
                        "position_id": int(detail.get("position_id", position_id) or position_id),
                        "symbol": symbol,
                        "side": side,
                        "volume": float(detail.get("volume", 0.0) or 0.0),
                        "price": float(detail.get("price", 0.0) or 0.0),
                        "profit": float(detail.get("profit", 0.0) or 0.0),
                        "swap": 0.0,
                        "commission": 0.0,
                        "comment": "ui-close",
                        "executed_at_utc": str(detail.get("executed_at_utc") or _now_iso()),
                        "source": "close_api",
                    }
                )

                closed.append(
                    detail
                )
        finally:
            try:
                bot.stop()
            except Exception:
                pass
        return {
            "account": account_name,
            "symbol": symbol,
            "closed_count": len(closed),
            "details": closed,
        }

    def _append_closed_journal(self, row: dict[str, Any]) -> None:
        try:
            line = json.dumps(row, separators=(",", ":"))
            with self._journal_lock:
                with self._closed_journal_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            # Non-blocking journal best effort.
            pass

    def _load_closed_journal(self, account_name: str | None, from_dt: datetime) -> list[dict[str, Any]]:
        if not self._closed_journal_path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            with self._journal_lock:
                lines = self._closed_journal_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return out
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if account_name and str(row.get("account", "")) != account_name:
                continue
            ts_raw = str(row.get("executed_at_utc", "") or "")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
            if ts < from_dt:
                continue
            out.append(row)
        return out

    def cancel_pending_order(self, account_name: str, ticket: int) -> dict[str, Any]:
        account = next((a for a in self._load_accounts() if a.name == account_name), None)
        if account is None:
            raise ValueError(f"Unknown account: {account_name}")

        bot = TradingBot(self._make_account_config(self.cfg, account))
        try:
            bot.start()
            orders = bot.client.active_orders(ticket=int(ticket))
            if not orders:
                raise ValueError(f"Pending order not found for ticket={ticket}")
            order = orders[0]
            result = bot.client.cancel_order(int(getattr(order, "ticket", ticket)))
            return {
                "account": account_name,
                "ticket": int(getattr(order, "ticket", ticket)),
                "symbol": str(getattr(order, "symbol", "") or ""),
                "retcode": result.get("retcode"),
                "order": result.get("order"),
                "comment": result.get("comment"),
            }
        finally:
            try:
                bot.stop()
            except Exception:
                pass

    def submit_plan(
        self,
        plan_rows: list[dict[str, Any]],
        timeout_seconds: int,
        poll_seconds: float,
        request_id: str | None,
    ) -> dict[str, Any]:
        request_key = request_id or uuid4().hex
        with self._req_lock:
            if request_key in self._request_cache:
                return self._request_cache[request_key]
        workflows = parse_advanced_order_rows(plan_rows)
        results = execute_advanced_order_plan(
            cfg=self.cfg,
            accounts=self._load_accounts(),
            workflows=workflows,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        payload = {
            "request_id": request_key,
            "submitted_at_utc": _now_iso(),
            "results": results,
        }
        with self._req_lock:
            self._request_cache[request_key] = payload
        return payload

    def quick_multi_order(
        self,
        accounts: list[str],
        symbol: str,
        side: str,
        volume: float,
        trigger_price: float | None,
        sl_price: float | None,
        tp_price: float | None,
        comment: str,
        timeout_seconds: int,
        poll_seconds: float,
        request_id: str | None,
    ) -> dict[str, Any]:
        if not accounts:
            raise ValueError("At least one account is required")
        rows: list[dict[str, Any]] = []
        for name in accounts:
            row: dict[str, Any] = {
                "account": name,
                "symbol": symbol,
                "side": side,
                "volume": volume,
                "comment": comment,
                "timeout_seconds": timeout_seconds,
            }
            if trigger_price is not None:
                row["trigger_price"] = trigger_price
            if sl_price is not None:
                row["sl_price"] = sl_price
            if tp_price is not None:
                row["tp_price"] = tp_price
            rows.append(row)
        payload = self.submit_plan(
            plan_rows=rows,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            request_id=request_id,
        )
        payload["rows_submitted"] = len(rows)
        return payload
