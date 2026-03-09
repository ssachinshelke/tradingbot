"""RSI (Relative Strength Index) mean-reversion strategy.

BUY  when RSI drops below the oversold level (default 30).
SELL when RSI rises above the overbought level (default 70).

RSI period defaults to 14 — the industry standard.

Usage in .env:
    STRATEGY_CLASS_PATH=strategies.rsi_reversal:RSIReversalStrategy
"""

from __future__ import annotations

from datetime import datetime, timezone

from mt5_bot import mt5
from mt5_bot.strategy import StrategySignal, timeframe_from_string


class RSIReversalStrategy:
    PERIOD = 14
    OVERBOUGHT = 70.0
    OVERSOLD = 30.0

    def __init__(self, config) -> None:
        self.timeframe = timeframe_from_string(config.timeframe)

    @staticmethod
    def _rsi(closes: list[float], period: int) -> float:
        """Compute RSI from a list of close prices."""
        if len(closes) < period + 1:
            return 50.0
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        bars_needed = self.PERIOD + 5
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, bars_needed)
        if rates is None or len(rates) < bars_needed:
            return None

        closes = [float(r["close"]) for r in rates]
        rsi = self._rsi(closes, self.PERIOD)
        candle_time = datetime.fromtimestamp(
            int(rates[-1]["time"]), tz=timezone.utc,
        )

        if rsi < self.OVERSOLD:
            return StrategySignal(
                side="buy",
                reason=f"RSI oversold ({rsi:.1f})",
                candle_time_utc=candle_time,
            )
        if rsi > self.OVERBOUGHT:
            return StrategySignal(
                side="sell",
                reason=f"RSI overbought ({rsi:.1f})",
                candle_time_utc=candle_time,
            )
        return None
