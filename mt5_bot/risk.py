from __future__ import annotations

from datetime import datetime, timezone

from . import mt5


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float,
        max_daily_loss_pct: float,
        max_open_trades: int,
    ) -> None:
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_open_trades = max_open_trades

    def is_open_trades_limit_reached(self) -> bool:
        positions = mt5.positions_get()
        return len(positions or []) >= self.max_open_trades

    def daily_loss_limit_hit(self, start_balance: float) -> bool:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"Unable to get account info: {mt5.last_error()}")
        if start_balance <= 0:
            return False
        loss = max(0.0, start_balance - info.equity)
        return (loss / start_balance) >= self.max_daily_loss_pct

    def calc_lot_size(
        self,
        symbol: str,
        account_balance: float,
        sl_pips: float,
        pip_size: float,
    ) -> float:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise RuntimeError(
                f"Unable to get symbol info for {symbol}: {mt5.last_error()}"
            )
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"Unable to get tick for {symbol}: {mt5.last_error()}"
            )
        risk_amount = account_balance * self.risk_per_trade
        if symbol_info.trade_tick_size == 0:
            raise RuntimeError(f"Invalid tick size (0) for symbol {symbol}")
        pip_value_per_lot = (
            symbol_info.trade_tick_value * (pip_size / symbol_info.trade_tick_size)
        )
        if pip_value_per_lot <= 0:
            raise RuntimeError("Invalid pip value from symbol specification")
        raw_lot = risk_amount / (sl_pips * pip_value_per_lot)
        step = symbol_info.volume_step
        min_vol = symbol_info.volume_min
        max_vol = symbol_info.volume_max
        stepped_lot = max(min_vol, min(max_vol, round(raw_lot / step) * step))
        return round(stepped_lot, 2)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
