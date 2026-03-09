"""Price breakout strategy.

BUY  when the last candle close breaks above the previous candle's high.
SELL when the last candle close breaks below the previous candle's low.

Usage in .env:
    STRATEGY_CLASS_PATH=strategies.breakout:BreakoutStrategy
"""

from __future__ import annotations

from datetime import datetime, timezone

from mt5_bot import mt5
from mt5_bot.strategy import StrategySignal, timeframe_from_string


class BreakoutStrategy:
    def __init__(self, config) -> None:
        self.timeframe = timeframe_from_string(config.timeframe)

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, 3)
        if rates is None or len(rates) < 3:
            return None

        prev = rates[-2]
        last = rates[-1]
        candle_time = datetime.fromtimestamp(int(last["time"]), tz=timezone.utc)

        if float(last["close"]) > float(prev["high"]):
            return StrategySignal(
                side="buy",
                reason="Breakout above previous high",
                candle_time_utc=candle_time,
            )
        if float(last["close"]) < float(prev["low"]):
            return StrategySignal(
                side="sell",
                reason="Breakout below previous low",
                candle_time_utc=candle_time,
            )
        return None
