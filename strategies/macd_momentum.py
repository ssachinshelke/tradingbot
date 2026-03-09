"""MACD histogram crossover strategy.

BUY  when the MACD histogram crosses above zero (bullish momentum shift).
SELL when the MACD histogram crosses below zero (bearish momentum shift).

Defaults:  fast EMA=12, slow EMA=26, signal EMA=9 (standard MACD settings).

Usage in .env:
    STRATEGY_CLASS_PATH=strategies.macd_momentum:MACDMomentumStrategy
"""

from __future__ import annotations

from datetime import datetime, timezone

from mt5_bot import mt5
from mt5_bot.strategy import StrategySignal, timeframe_from_string


class MACDMomentumStrategy:
    FAST = 12
    SLOW = 26
    SIGNAL = 9

    def __init__(self, config) -> None:
        self.timeframe = timeframe_from_string(config.timeframe)

    @staticmethod
    def _ema(values: list[float], period: int) -> list[float]:
        """Exponential moving average over the full series."""
        if not values:
            return []
        result = [values[0]]
        k = 2.0 / (period + 1)
        for v in values[1:]:
            result.append(v * k + result[-1] * (1.0 - k))
        return result

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        bars_needed = self.SLOW + self.SIGNAL + 5
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, bars_needed)
        if rates is None or len(rates) < bars_needed:
            return None

        closes = [float(r["close"]) for r in rates]
        fast_ema = self._ema(closes, self.FAST)
        slow_ema = self._ema(closes, self.SLOW)
        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        signal_line = self._ema(macd_line, self.SIGNAL)

        if len(macd_line) < 2 or len(signal_line) < 2:
            return None

        hist_curr = macd_line[-1] - signal_line[-1]
        hist_prev = macd_line[-2] - signal_line[-2]
        candle_time = datetime.fromtimestamp(
            int(rates[-1]["time"]), tz=timezone.utc,
        )

        if hist_prev <= 0 and hist_curr > 0:
            return StrategySignal(
                side="buy",
                reason="MACD histogram bullish crossover",
                candle_time_utc=candle_time,
            )
        if hist_prev >= 0 and hist_curr < 0:
            return StrategySignal(
                side="sell",
                reason="MACD histogram bearish crossover",
                candle_time_utc=candle_time,
            )
        return None
