"""Unified MT5 client – low-level API wrapper + high-level TradingBot.

Merges the former mt5_adapter, mt5_client, and bot modules into a single
file so the package stays small while exposing **every** MT5 API a quant
trader needs: connection, account info, market data, full order management
(market / limit / stop / stop-limit / modify / cancel / close), history,
margin & profit calculators, and market depth.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .config import BotConfig
from . import mt5

Side = Literal["buy", "sell"]


@dataclass
class AccountSnapshot:
    login: int
    server: str
    balance: float
    equity: float
    margin_free: float
    currency: str
    time: datetime


@dataclass
class OrderPlan:
    symbol: str
    side: Side
    sl_pips: float
    tp_pips: float
    comment: str = "mt5-bot"


# ---------------------------------------------------------------------------
# Low-level client – thin Pythonic wrapper over every MetaTrader5 function
# ---------------------------------------------------------------------------

class MT5Client:
    """Wraps the MetaTrader5 Python API with error handling and Pythonic
    return types.  Use this directly when you need full control."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    # ── Connection ────────────────────────────────────────────────────────

    def connect(self) -> None:
        last_error: tuple | None = None
        for attempt in range(1, self.config.max_connect_retries + 1):
            if self.config.mt5_path:
                ok = mt5.initialize(
                    path=self.config.mt5_path,
                    portable=self.config.mt5_portable,
                )
            else:
                ok = mt5.initialize()
            if not ok:
                last_error = mt5.last_error()
                time.sleep(min(attempt, 3))
                continue
            if mt5.login(
                login=self.config.mt5_login,
                password=self.config.mt5_password,
                server=self.config.mt5_server,
            ):
                return
            last_error = mt5.last_error()
            mt5.shutdown()
            time.sleep(min(attempt, 3))
        raise RuntimeError(f"Unable to connect/login after retries: {last_error}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def version(self) -> tuple:
        """Return (build, release_date, name) of the connected terminal."""
        return mt5.version()

    def terminal_info(self) -> dict:
        info = mt5.terminal_info()
        if info is None:
            raise RuntimeError(f"terminal_info failed: {mt5.last_error()}")
        return info._asdict()

    def last_error(self) -> tuple:
        return mt5.last_error()

    # ── Account ───────────────────────────────────────────────────────────

    def account_snapshot(self) -> AccountSnapshot:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"Unable to read account info: {mt5.last_error()}")
        raw_time = getattr(info, "time", None)
        snap_time = (
            datetime.fromtimestamp(raw_time)
            if isinstance(raw_time, (int, float))
            else datetime.now()
        )
        return AccountSnapshot(
            login=info.login,
            server=info.server,
            balance=info.balance,
            equity=info.equity,
            margin_free=info.margin_free,
            currency=info.currency,
            time=snap_time,
        )

    # ── Symbols ───────────────────────────────────────────────────────────

    def ensure_symbol(self, symbol: str) -> None:
        si = mt5.symbol_info(symbol)
        if si is None:
            raise ValueError(f"Symbol not found: {symbol}")
        if not si.visible and not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Failed to select symbol: {symbol}")

    def symbol_info(self, symbol: str) -> dict:
        si = mt5.symbol_info(symbol)
        if si is None:
            raise RuntimeError(
                f"Unable to get symbol info for {symbol}: {mt5.last_error()}"
            )
        return si._asdict()

    def symbol_tick(self, symbol: str) -> dict:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"Unable to get tick for {symbol}: {mt5.last_error()}"
            )
        return tick._asdict()

    def symbols_total(self) -> int:
        return mt5.symbols_total()

    def symbols_get(self, group: str | None = None) -> list[dict]:
        data = mt5.symbols_get(group=group) if group else mt5.symbols_get()
        return [s._asdict() for s in (data or [])]

    def current_price(self, symbol: str, side: Side) -> float:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"Unable to get tick for {symbol}: {mt5.last_error()}"
            )
        return tick.ask if side == "buy" else tick.bid

    def pip_size(self, symbol: str) -> float:
        si = mt5.symbol_info(symbol)
        if si is None:
            raise RuntimeError(
                f"Unable to get symbol info for {symbol}: {mt5.last_error()}"
            )
        return si.point * 10 if si.digits in (3, 5) else si.point

    def normalize_price(self, symbol: str, price: float) -> float:
        si = mt5.symbol_info(symbol)
        if si is None:
            raise RuntimeError(
                f"Unable to get symbol info for {symbol}: {mt5.last_error()}"
            )
        return round(price, int(si.digits))

    def spread_pips(self, symbol: str) -> float:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"Unable to get tick for {symbol}: {mt5.last_error()}"
            )
        pip = self.pip_size(symbol)
        return (tick.ask - tick.bid) / pip

    # ── Market Data ───────────────────────────────────────────────────────

    def get_rates(
        self, symbol: str, timeframe: int, date_from: datetime, count: int,
    ) -> Any:
        """OHLCV bars from *date_from* forward.  Returns numpy structured array."""
        data = mt5.copy_rates_from(symbol, timeframe, date_from, count)
        if data is None:
            raise RuntimeError(
                f"copy_rates_from failed for {symbol}: {mt5.last_error()}"
            )
        return data

    def get_rates_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int,
    ) -> Any:
        """OHLCV bars from bar index (0 = current).  Returns numpy structured array."""
        data = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        if data is None:
            raise RuntimeError(
                f"copy_rates_from_pos failed for {symbol}: {mt5.last_error()}"
            )
        return data

    def get_rates_range(
        self, symbol: str, timeframe: int,
        date_from: datetime, date_to: datetime,
    ) -> Any:
        """OHLCV bars in [date_from, date_to].  Returns numpy structured array."""
        data = mt5.copy_rates_range(symbol, timeframe, date_from, date_to)
        if data is None:
            raise RuntimeError(
                f"copy_rates_range failed for {symbol}: {mt5.last_error()}"
            )
        return data

    def get_ticks(
        self, symbol: str, date_from: datetime, count: int,
        flags: int | None = None,
    ) -> Any:
        """Ticks from *date_from* forward.  Returns numpy structured array."""
        f = flags if flags is not None else mt5.COPY_TICKS_ALL
        data = mt5.copy_ticks_from(symbol, date_from, count, f)
        if data is None:
            raise RuntimeError(
                f"copy_ticks_from failed for {symbol}: {mt5.last_error()}"
            )
        return data

    def get_ticks_range(
        self, symbol: str, date_from: datetime, date_to: datetime,
        flags: int | None = None,
    ) -> Any:
        """Ticks in [date_from, date_to].  Returns numpy structured array."""
        f = flags if flags is not None else mt5.COPY_TICKS_ALL
        data = mt5.copy_ticks_range(symbol, date_from, date_to, f)
        if data is None:
            raise RuntimeError(
                f"copy_ticks_range failed for {symbol}: {mt5.last_error()}"
            )
        return data

    # ── Market Depth ──────────────────────────────────────────────────────

    def depth_subscribe(self, symbol: str) -> bool:
        return mt5.market_book_add(symbol)

    def depth_get(self, symbol: str) -> list:
        return list(mt5.market_book_get(symbol) or [])

    def depth_unsubscribe(self, symbol: str) -> bool:
        return mt5.market_book_release(symbol)

    # ── Positions ─────────────────────────────────────────────────────────

    def positions(
        self, symbol: str | None = None, ticket: int | None = None,
    ) -> list:
        if ticket is not None:
            data = mt5.positions_get(ticket=ticket)
        elif symbol:
            data = mt5.positions_get(symbol=symbol)
        else:
            data = mt5.positions_get()
        return list(data or [])

    def positions_count(self, symbol: str | None = None) -> int:
        return len(self.positions(symbol=symbol))

    def symbol_total_volume(self, symbol: str) -> float:
        return sum(float(p.volume) for p in self.positions(symbol=symbol))

    # ── Active Pending Orders ─────────────────────────────────────────────

    def active_orders(
        self, symbol: str | None = None, ticket: int | None = None,
    ) -> list:
        if ticket is not None:
            data = mt5.orders_get(ticket=ticket)
        elif symbol:
            data = mt5.orders_get(symbol=symbol)
        else:
            data = mt5.orders_get()
        return list(data or [])

    def active_orders_count(self) -> int:
        return mt5.orders_total()

    def pending_order_exists(self, ticket: int) -> bool:
        return bool(mt5.orders_get(ticket=ticket))

    # ── Margin & Profit Calculators ───────────────────────────────────────

    def calc_margin(
        self, action: int, symbol: str, volume: float, price: float,
    ) -> float | None:
        """Required margin for a trade.  *action*: mt5.ORDER_TYPE_BUY / SELL."""
        return mt5.order_calc_margin(action, symbol, volume, price)

    def calc_profit(
        self, action: int, symbol: str, volume: float,
        price_open: float, price_close: float,
    ) -> float | None:
        """Hypothetical profit.  *action*: mt5.ORDER_TYPE_BUY / SELL."""
        return mt5.order_calc_profit(action, symbol, volume, price_open, price_close)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _safe_comment(comment: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", comment or "")
        return safe[:31] if safe else "mt5-bot"

    @staticmethod
    def _filling_mode_name(mode: int) -> str:
        names = {
            getattr(mt5, "ORDER_FILLING_FOK", -1): "FOK",
            getattr(mt5, "ORDER_FILLING_IOC", -1): "IOC",
            getattr(mt5, "ORDER_FILLING_RETURN", -1): "RETURN",
        }
        return names.get(mode, str(mode))

    def _market_filling_modes(self, symbol: str) -> list[int]:
        """Resolve preferred filling modes for this symbol/account."""
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(
                f"Unable to get symbol info for {symbol}: {mt5.last_error()}"
            )
        allowed_mask = int(getattr(info, "filling_mode", 0) or 0)
        ordered: list[int] = []
        flag_map = (
            ("SYMBOL_FILLING_FOK", "ORDER_FILLING_FOK"),
            ("SYMBOL_FILLING_IOC", "ORDER_FILLING_IOC"),
        )
        for sym_flag_name, order_fill_name in flag_map:
            sym_flag = getattr(mt5, sym_flag_name, None)
            order_fill = getattr(mt5, order_fill_name, None)
            if sym_flag is None or order_fill is None:
                continue
            if allowed_mask & int(sym_flag):
                ordered.append(int(order_fill))

        # Fallback order for brokers that expose incomplete mask metadata.
        for fallback_name in (
            "ORDER_FILLING_FOK",
            "ORDER_FILLING_IOC",
            "ORDER_FILLING_RETURN",
        ):
            fallback = getattr(mt5, fallback_name, None)
            if fallback is None:
                continue
            value = int(fallback)
            if value not in ordered:
                ordered.append(value)
        return ordered

    def _execute_with_filling_fallback(
        self,
        request: dict[str, Any],
        success_retcodes: set[int],
        check_only: bool = False,
        context: str = "order_send",
    ) -> dict:
        invalid_fill_retcode = int(getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030))
        symbol = str(request["symbol"])
        attempted: list[str] = []
        last_none_error: tuple | None = None
        request_variants: list[tuple[str, dict[str, Any]]] = [("with_comment", dict(request))]
        if "comment" in request:
            no_comment = dict(request)
            no_comment.pop("comment", None)
            request_variants.append(("no_comment", no_comment))

        for variant_name, base_request in request_variants:
            for mode in self._market_filling_modes(symbol):
                attempted.append(f"{self._filling_mode_name(mode)}:{variant_name}")
                payload = dict(base_request)
                payload["type_filling"] = mode
                result = (
                    mt5.order_check(payload)
                    if check_only
                    else mt5.order_send(payload)
                )
                if result is None:
                    last_none_error = mt5.last_error()
                    if (
                        variant_name == "with_comment"
                        and "comment" in str(last_none_error).lower()
                    ):
                        break
                    continue
                data = result._asdict()
                retcode = int(data.get("retcode", -1) or -1)
                if retcode in success_retcodes:
                    return data
                if retcode != invalid_fill_retcode:
                    comment_text = str(data.get("comment", "")).lower()
                    if variant_name == "with_comment" and "comment" in comment_text:
                        break
                    raise RuntimeError(
                        f"{context} failed retcode={retcode}, "
                        f"comment={data.get('comment')}"
                    )

        if last_none_error is not None:
            raise RuntimeError(
                f"{context} returned None for all filling modes: {last_none_error}"
            )
        tried = ", ".join(attempted) if attempted else "none"
        raise RuntimeError(
            f"{context} failed retcode={invalid_fill_retcode}, "
            f"comment=Unsupported filling mode (tried: {tried})"
        )

    # ── Market Order ──────────────────────────────────────────────────────

    def send_market_order(
        self, symbol: str, side: Side, volume: float,
        sl: float = 0.0, tp: float = 0.0, comment: str = "mt5-bot",
    ) -> dict:
        self.ensure_symbol(symbol)
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        price = self.current_price(symbol, side)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "comment": self._safe_comment(comment),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        return self._execute_with_filling_fallback(
            request=request,
            success_retcodes={int(mt5.TRADE_RETCODE_DONE)},
            check_only=False,
            context="order_send",
        )

    def check_market_order(
        self, symbol: str, side: Side, volume: float,
        sl: float = 0.0, tp: float = 0.0, comment: str = "mt5-bot-check",
    ) -> dict:
        """Broker-side order_check (no real execution)."""
        self.ensure_symbol(symbol)
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        price = self.current_price(symbol, side)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "comment": self._safe_comment(comment),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        return self._execute_with_filling_fallback(
            request=request,
            success_retcodes={int(mt5.TRADE_RETCODE_DONE)},
            check_only=True,
            context="order_check",
        )

    # ── Pending Limit Order ───────────────────────────────────────────────

    def send_limit_order(
        self, symbol: str, side: Side, volume: float, price: float,
        sl: float = 0.0, tp: float = 0.0, comment: str = "",
    ) -> dict:
        self.ensure_symbol(symbol)
        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if side == "buy"
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": self.normalize_price(symbol, price),
            "sl": sl,
            "tp": tp,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if comment:
            request["comment"] = self._safe_comment(comment)
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(
                f"limit order_send returned None: {mt5.last_error()}"
            )
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise RuntimeError(
                f"limit order failed retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Pending Stop Order ────────────────────────────────────────────────

    def send_stop_order(
        self, symbol: str, side: Side, volume: float, price: float,
        sl: float = 0.0, tp: float = 0.0, comment: str = "",
    ) -> dict:
        self.ensure_symbol(symbol)
        order_type = (
            mt5.ORDER_TYPE_BUY_STOP if side == "buy"
            else mt5.ORDER_TYPE_SELL_STOP
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": self.normalize_price(symbol, price),
            "sl": sl,
            "tp": tp,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if comment:
            request["comment"] = self._safe_comment(comment)
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(
                f"stop order_send returned None: {mt5.last_error()}"
            )
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise RuntimeError(
                f"stop order failed retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Pending Stop-Limit Order ──────────────────────────────────────────

    def send_stop_limit_order(
        self, symbol: str, side: Side, volume: float,
        stop_price: float, limit_price: float,
        sl: float = 0.0, tp: float = 0.0, comment: str = "",
    ) -> dict:
        """When *stop_price* is hit, a limit order at *limit_price* is placed."""
        self.ensure_symbol(symbol)
        order_type = (
            mt5.ORDER_TYPE_BUY_STOP_LIMIT if side == "buy"
            else mt5.ORDER_TYPE_SELL_STOP_LIMIT
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": self.normalize_price(symbol, stop_price),
            "stoplimit": self.normalize_price(symbol, limit_price),
            "sl": sl,
            "tp": tp,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if comment:
            request["comment"] = self._safe_comment(comment)
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(
                f"stop-limit order_send returned None: {mt5.last_error()}"
            )
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise RuntimeError(
                f"stop-limit order failed retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Modify Position SL / TP ───────────────────────────────────────────

    def modify_position(
        self, ticket: int, symbol: str, sl: float, tp: float,
    ) -> dict:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "magic": self.config.magic_number,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"modify position failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"modify position retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Modify Pending Order ──────────────────────────────────────────────

    def modify_pending_order(
        self, ticket: int, symbol: str, price: float,
        sl: float = 0.0, tp: float = 0.0,
    ) -> dict:
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "symbol": symbol,
            "price": self.normalize_price(symbol, price),
            "sl": sl,
            "tp": tp,
            "magic": self.config.magic_number,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"modify order failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"modify order retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Cancel Pending Order ──────────────────────────────────────────────

    def cancel_order(self, ticket: int) -> dict:
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"cancel order failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"cancel order retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── Close Position ────────────────────────────────────────────────────

    def close_position(
        self, position: Any, volume: float | None = None,
        comment: str = "close",
    ) -> dict:
        """Close *position* fully (volume=None) or partially."""
        close_vol = volume if volume is not None else float(position.volume)
        side: Side = "sell" if position.type == mt5.POSITION_TYPE_BUY else "buy"
        price = self.current_price(position.symbol, side)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "position": position.ticket,
            "volume": close_vol,
            "type": (
                mt5.ORDER_TYPE_SELL
                if position.type == mt5.POSITION_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            ),
            "price": price,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "comment": self._safe_comment(comment),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        return self._execute_with_filling_fallback(
            request=request,
            success_retcodes={
                int(mt5.TRADE_RETCODE_DONE),
                int(mt5.TRADE_RETCODE_DONE_PARTIAL),
            },
            check_only=False,
            context="close position",
        )

    def close_by_opposite(self, ticket: int, opposite_ticket: int) -> dict:
        """Close a position by an opposite one (hedging accounts)."""
        request = {
            "action": mt5.TRADE_ACTION_CLOSE_BY,
            "position": ticket,
            "position_by": opposite_ticket,
            "magic": self.config.magic_number,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"close_by failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"close_by retcode={result.retcode}, "
                f"comment={result.comment}"
            )
        return result._asdict()

    # ── History ───────────────────────────────────────────────────────────

    def history_orders(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> list:
        if ticket is not None:
            data = mt5.history_orders_get(ticket=ticket)
        elif position is not None:
            data = mt5.history_orders_get(position=position)
        elif date_from and date_to:
            data = mt5.history_orders_get(date_from, date_to)
        else:
            now = datetime.now(timezone.utc)
            data = mt5.history_orders_get(
                datetime(2020, 1, 1, tzinfo=timezone.utc), now,
            )
        return list(data or [])

    def history_deals(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> list:
        if ticket is not None:
            data = mt5.history_deals_get(ticket=ticket)
        elif position is not None:
            data = mt5.history_deals_get(position=position)
        elif date_from and date_to:
            data = mt5.history_deals_get(date_from, date_to)
        else:
            now = datetime.now(timezone.utc)
            data = mt5.history_deals_get(
                datetime(2020, 1, 1, tzinfo=timezone.utc), now,
            )
        return list(data or [])

    def history_orders_count(
        self, date_from: datetime, date_to: datetime,
    ) -> int:
        return mt5.history_orders_total(date_from, date_to)

    def history_deals_count(
        self, date_from: datetime, date_to: datetime,
    ) -> int:
        return mt5.history_deals_total(date_from, date_to)


# ---------------------------------------------------------------------------
# High-level TradingBot – MT5Client + RiskManager + order helpers
# ---------------------------------------------------------------------------

class TradingBot:
    """Opinionated high-level wrapper used by the automated engine and
    multi-account dispatcher.  If you want raw API control, use MT5Client
    directly instead."""

    def __init__(self, config: BotConfig) -> None:
        from .risk import RiskManager

        self.config = config
        self.client = MT5Client(config)
        self.risk = RiskManager(
            risk_per_trade=config.risk_per_trade,
            max_daily_loss_pct=config.max_daily_loss_pct,
            max_open_trades=config.max_open_trades,
        )
        self.start_balance: float | None = None

    def start(self) -> None:
        self.client.connect()
        self.start_balance = self.client.account_snapshot().balance

    def stop(self) -> None:
        self.client.shutdown()

    def spread_in_pips(self, symbol: str) -> float:
        return self.client.spread_pips(symbol)

    def estimate_order_volume(self, symbol: str, sl_pips: float) -> float:
        snap = self.client.account_snapshot()
        pip_size = self.client.pip_size(symbol)
        return self.risk.calc_lot_size(
            symbol=symbol,
            account_balance=snap.balance,
            sl_pips=sl_pips,
            pip_size=pip_size,
        )

    def place_market_order(
        self,
        plan: OrderPlan,
        volume_override: float | None = None,
        dry_run: bool = False,
        broker_check: bool = False,
    ) -> dict:
        if self.start_balance is None:
            raise RuntimeError("Bot not started. Call start() first.")
        if self.risk.daily_loss_limit_hit(self.start_balance):
            raise RuntimeError("Daily loss limit reached. Trading halted.")
        if self.risk.is_open_trades_limit_reached():
            raise RuntimeError("Max open trades limit reached.")

        pip_size = self.client.pip_size(plan.symbol)
        lot = (
            volume_override
            if volume_override is not None
            else self.estimate_order_volume(plan.symbol, plan.sl_pips)
        )
        price = self.client.current_price(plan.symbol, plan.side)
        sl_dist = plan.sl_pips * pip_size
        tp_dist = plan.tp_pips * pip_size

        if plan.side == "buy":
            sl, tp = price - sl_dist, price + tp_dist
        else:
            sl, tp = price + sl_dist, price - tp_dist

        if dry_run and broker_check:
            result = self.client.check_market_order(
                symbol=plan.symbol, side=plan.side, volume=lot,
                sl=sl, tp=tp, comment=plan.comment,
            )
        elif dry_run:
            self.client.ensure_symbol(plan.symbol)
            self.client.current_price(plan.symbol, plan.side)
            result = {"retcode": 0, "comment": "LOCAL_DRY_RUN_OK"}
        else:
            result = self.client.send_market_order(
                symbol=plan.symbol, side=plan.side, volume=lot,
                sl=sl, tp=tp, comment=plan.comment,
            )

        return {
            "order_result": result,
            "volume": lot,
            "sl": sl,
            "tp": tp,
            "side": plan.side,
            "symbol": plan.symbol,
            "comment": plan.comment,
            "dry_run": dry_run,
        }
