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
        self._journal_lock = Lock()
        self._request_cache: dict[str, dict[str, Any]] = {}
        self._closed_journal_path = Path("logs") / "closed_deals_journal.jsonl"
        self._closed_journal_path.parent.mkdir(parents=True, exist_ok=True)

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
                    known_order_ids = {
                        int(r.get("order_ticket", 0) or 0)
                        for r in rows
                        if str(r.get("account", "")) == account.name
                    }
                    for o in orders:
                        order_ticket = int(getattr(o, "ticket", 0) or 0)
                        if order_ticket in known_order_ids:
                            continue
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
                f"{row.get('account','')}|{row.get('deal_ticket',0)}|"
                f"{row.get('order_ticket',0)}|{row.get('position_id',0)}|"
                f"{row.get('executed_at_utc','')}"
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
