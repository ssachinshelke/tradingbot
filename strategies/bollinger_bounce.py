"""Bollinger Bands mean-reversion strategy.

BUY  when price closes below the lower band (oversold squeeze).
SELL when price closes above the upper band (overbought squeeze).

Defaults:  period=20, std-dev multiplier=2.0 (standard Bollinger settings).

Usage in .env:
    STRATEGY_CLASS_PATH=strategies.bollinger_bounce:BollingerBounceStrategy
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from mt5_bot import mt5
from mt5_bot.strategy import StrategySignal, timeframe_from_string


class BollingerBounceStrategy:
    PERIOD = 20
    STD_MULT = 2.0

    def __init__(self, config) -> None:
        self.timeframe = timeframe_from_string(config.timeframe)

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        bars_needed = self.PERIOD + 3
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, bars_needed)
        if rates is None or len(rates) < bars_needed:
            return None

        closes = [float(r["close"]) for r in rates]
        window = closes[-self.PERIOD:]
        sma = sum(window) / self.PERIOD
        variance = sum((c - sma) ** 2 for c in window) / self.PERIOD
        std_dev = math.sqrt(variance)

        upper = sma + self.STD_MULT * std_dev
        lower = sma - self.STD_MULT * std_dev
        last_close = closes[-1]

        candle_time = datetime.fromtimestamp(
            int(rates[-1]["time"]), tz=timezone.utc,
        )

        if last_close < lower:
            return StrategySignal(
                side="buy",
                reason=f"BB lower bounce ({last_close:.5f} < {lower:.5f})",
                candle_time_utc=candle_time,
            )
        if last_close > upper:
            return StrategySignal(
                side="sell",
                reason=f"BB upper bounce ({last_close:.5f} > {upper:.5f})",
                candle_time_utc=candle_time,
            )
        return None
