"""Backend orchestration service for local UI."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from mt5_bot import mt5
from mt5_bot.advanced_plan import execute_advanced_order_plan, parse_advanced_order_rows
from mt5_bot.client import TradingBot
from mt5_bot.config import AccountConfig, BotConfig, load_accounts, load_config
from mt5_bot.multi import healthcheck_all_accounts


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
        self.cfg: BotConfig = load_config()
        self._accounts_file = Path(self.cfg.accounts_file)
        self._req_lock = Lock()
        self._request_cache: dict[str, dict[str, Any]] = {}

    def _load_accounts(self) -> list[AccountConfig]:
        return load_accounts(str(self._accounts_file))

    def _save_accounts(self, accounts: list[AccountConfig]) -> None:
        serializable = [asdict(a) for a in accounts]
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
        updated = AccountConfig(
            name=str(payload["name"]).strip(),
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
        return healthcheck_all_accounts(
            self.cfg,
            accounts,
            symbol=symbol or self.cfg.default_symbol,
            timeout_seconds=45,
        )

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

    def get_closed_deals(
        self,
        account_name: str | None,
        days: int = 7,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        days_safe = max(1, min(int(days), 365))
        limit_safe = max(1, min(int(limit), 2000))
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=days_safe)

        accounts = self._load_accounts()
        if account_name:
            accounts = [a for a in accounts if a.name == account_name]
        if not accounts:
            return []

        deal_entry_out = int(getattr(mt5, "DEAL_ENTRY_OUT", 1))
        rows: list[dict[str, Any]] = []

        for account in accounts:
            bot = TradingBot(self._make_account_config(self.cfg, account))
            try:
                bot.start()
                deals = bot.client.history_deals(date_from=from_dt, date_to=to_dt)
                for d in deals:
                    if int(getattr(d, "entry", -1)) != deal_entry_out:
                        continue
                    t = int(getattr(d, "time", 0) or 0)
                    ts = datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t > 0 else None
                    side = "buy" if int(getattr(d, "type", -1)) == int(getattr(mt5, "DEAL_TYPE_BUY", 0)) else "sell"
                    rows.append(
                        {
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
                            "executed_at_utc": ts,
                        }
                    )
            finally:
                try:
                    bot.stop()
                except Exception:
                    pass

        rows.sort(key=lambda r: r.get("executed_at_utc") or "", reverse=True)
        return rows[:limit_safe]

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
                result = bot.client.close_position(
                    p,
                    volume=req_volume,
                    comment="ui-close",
                )
                closed.append(
                    {
                        "ticket": int(p.ticket),
                        "retcode": result.get("retcode"),
                        "deal": result.get("deal"),
                        "order": result.get("order"),
                    }
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
