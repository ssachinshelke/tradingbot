"""Automated trading engine – continuous strategy loop with position management."""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timezone

from .client import OrderPlan, TradingBot
from .config import BotConfig
from .journal import TradeJournal
from . import mt5
from .strategy import create_strategy


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.strip().split(":")
    return dtime(hour=int(hh), minute=int(mm))


def _within_utc_session(now_utc: datetime, start: dtime, end: dtime) -> bool:
    t = now_utc.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


class TradingEngine:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.bot = TradingBot(config)
        self.journal = TradeJournal(config.journal_path)
        self.strategy = create_strategy(config)
        self._last_trade_epoch: float = 0.0
        self._last_trade_bar_time_utc: datetime | None = None
        self._partial_closed_tickets: set[int] = set()
        self._session_start = _parse_hhmm(config.session_start_utc)
        self._session_end = _parse_hhmm(config.session_end_utc)

    def _can_trade_now(self) -> bool:
        if not self.config.enable_session_filter:
            return True
        return _within_utc_session(
            datetime.now(timezone.utc), self._session_start, self._session_end,
        )

    def _on_cooldown(self) -> bool:
        return (time.time() - self._last_trade_epoch) < self.config.cooldown_seconds

    def _round_volume(self, symbol: str, volume: float) -> float:
        info = mt5.symbol_info(symbol)
        if info is None or info.volume_step <= 0:
            return round(volume, 2)
        step = float(info.volume_step)
        fmt = f"{step:.8f}"
        decimals = max(
            0,
            len(fmt.rstrip("0").split(".")[1]) if "." in fmt else 0,
        )
        rounded = round(round(volume / step) * step, decimals)
        return max(0.0, rounded)

    # ── Position management (break-even / trailing / partial TP) ──────────

    def _manage_open_positions(self, symbol: str) -> None:
        for position in self.bot.client.positions(symbol=symbol):
            try:
                self._manage_single_position(position)
            except Exception as err:
                logging.exception(
                    "Position management failed for ticket=%s: %s",
                    position.ticket, err,
                )

    def _manage_single_position(self, position) -> None:  # noqa: C901
        symbol_info = mt5.symbol_info(position.symbol)
        if symbol_info is None:
            return

        pip = self.bot.client.pip_size(position.symbol)
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        exit_side = "sell" if is_buy else "buy"
        exit_price = self.bot.client.current_price(position.symbol, exit_side)
        progress_pips = (
            (exit_price - float(position.price_open)) / pip
            if is_buy
            else (float(position.price_open) - exit_price) / pip
        )

        current_sl = float(position.sl or 0.0)
        current_tp = float(position.tp or 0.0)
        point = float(symbol_info.point)
        target_sl: float | None = None

        if (
            self.config.enable_break_even
            and progress_pips >= self.config.break_even_trigger_pips
        ):
            be_sl = (
                float(position.price_open)
                + self.config.break_even_offset_pips * pip
                if is_buy
                else float(position.price_open)
                - self.config.break_even_offset_pips * pip
            )
            target_sl = be_sl

        if (
            self.config.enable_trailing_stop
            and progress_pips >= self.config.trailing_start_pips
        ):
            trail_sl = (
                exit_price - self.config.trailing_distance_pips * pip
                if is_buy
                else exit_price + self.config.trailing_distance_pips * pip
            )
            if target_sl is None:
                target_sl = trail_sl
            else:
                target_sl = (
                    max(target_sl, trail_sl)
                    if is_buy
                    else min(target_sl, trail_sl)
                )

        if target_sl is not None:
            if is_buy:
                target_sl = min(target_sl, exit_price - point)
                should_modify = current_sl == 0.0 or target_sl > current_sl + point
            else:
                target_sl = max(target_sl, exit_price + point)
                should_modify = current_sl == 0.0 or target_sl < current_sl - point

            if should_modify:
                self.bot.client.modify_position(
                    ticket=position.ticket,
                    symbol=position.symbol,
                    sl=target_sl,
                    tp=current_tp,
                )
                logging.info(
                    "Updated SL ticket=%s -> %.5f", position.ticket, target_sl,
                )

        if (
            self.config.enable_partial_tp
            and position.ticket not in self._partial_closed_tickets
            and progress_pips >= self.config.partial_tp_trigger_pips
        ):
            min_vol = float(symbol_info.volume_min)
            current_volume = float(position.volume)
            raw_close = current_volume * self.config.partial_tp_close_pct
            close_volume = self._round_volume(position.symbol, raw_close)
            if close_volume < min_vol:
                return

            remaining_after = current_volume - close_volume
            if 0 < remaining_after < min_vol:
                close_volume = self._round_volume(
                    position.symbol, current_volume - min_vol,
                )

            if close_volume < min_vol or close_volume >= current_volume:
                return

            self.bot.client.close_position(
                position, volume=close_volume, comment="mt5-bot:partial-tp",
            )
            self._partial_closed_tickets.add(position.ticket)
            logging.info(
                "Partial TP ticket=%s volume=%.2f",
                position.ticket, close_volume,
            )

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self, symbol: str, max_cycles: int | None = None) -> None:
        logging.info(
            "Starting engine symbol=%s strategy=%s timeframe=%s",
            symbol, self.config.strategy_name, self.config.timeframe,
        )
        self.bot.start()
        cycles = 0
        try:
            while True:
                if max_cycles is not None and cycles >= max_cycles:
                    logging.info("Reached max cycles=%s. Stopping.", max_cycles)
                    return
                cycles += 1

                try:
                    self._manage_open_positions(symbol)

                    if not self._can_trade_now():
                        logging.info("Outside allowed UTC session. Waiting.")
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    if self.bot.risk.daily_loss_limit_hit(
                        self.bot.start_balance or 0.0,
                    ):
                        logging.warning("Daily loss limit reached. Paused.")
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    if self.bot.risk.is_open_trades_limit_reached():
                        logging.info("Open trades limit reached. Waiting.")
                        time.sleep(self.config.poll_interval_seconds)
                        continue
                    if (
                        self.bot.client.positions_count(symbol=symbol)
                        >= self.config.max_symbol_open_trades
                    ):
                        logging.info("Symbol open trades cap for %s.", symbol)
                        time.sleep(self.config.poll_interval_seconds)
                        continue
                    symbol_volume = self.bot.client.symbol_total_volume(symbol)
                    if symbol_volume >= self.config.max_symbol_volume:
                        logging.info("Symbol volume cap for %s.", symbol)
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    spread = self.bot.spread_in_pips(symbol)
                    if spread > self.config.max_spread_pips:
                        logging.info(
                            "Spread %.2f > %.2f. Waiting.",
                            spread, self.config.max_spread_pips,
                        )
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    signal = self.strategy.generate_signal(symbol)
                    if signal is None:
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    if self._last_trade_bar_time_utc == signal.candle_time_utc:
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    if self._on_cooldown():
                        logging.info("Cooldown active. Waiting.")
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    planned_volume = self.bot.estimate_order_volume(
                        symbol=symbol, sl_pips=self.config.sl_pips,
                    )
                    if symbol_volume + planned_volume > self.config.max_symbol_volume:
                        logging.info(
                            "Volume %.2f + planned %.2f > cap %.2f",
                            symbol_volume, planned_volume,
                            self.config.max_symbol_volume,
                        )
                        time.sleep(self.config.poll_interval_seconds)
                        continue

                    plan = OrderPlan(
                        symbol=symbol,
                        side=signal.side,
                        sl_pips=self.config.sl_pips,
                        tp_pips=self.config.tp_pips,
                        comment=f"mt5-bot:{signal.reason}",
                    )
                    execution = self.bot.place_market_order(
                        plan, volume_override=planned_volume,
                    )
                    order_result = execution["order_result"]
                    self._last_trade_epoch = time.time()
                    self._last_trade_bar_time_utc = signal.candle_time_utc

                    self.journal.append({
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "side": execution["side"],
                        "volume": execution["volume"],
                        "sl": execution["sl"],
                        "tp": execution["tp"],
                        "order": order_result.get("order"),
                        "deal": order_result.get("deal"),
                        "retcode": order_result.get("retcode"),
                        "comment": execution["comment"],
                        "reason": signal.reason,
                    })
                    logging.info(
                        "Order placed: side=%s volume=%.2f order=%s retcode=%s",
                        execution["side"], execution["volume"],
                        order_result.get("order"), order_result.get("retcode"),
                    )
                except Exception as err:
                    logging.exception("Cycle failed: %s", err)

                time.sleep(self.config.poll_interval_seconds)
        finally:
            self.bot.stop()
