"""Strategy protocol, built-in MA cross, and pluggable strategy factory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
from typing import Literal, Protocol

from .config import BotConfig
from . import mt5


SignalSide = Literal["buy", "sell"]


@dataclass
class StrategySignal:
    side: SignalSide
    reason: str
    candle_time_utc: datetime


class Strategy(Protocol):
    def generate_signal(self, symbol: str) -> StrategySignal | None: ...


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3, "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5, "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2,
    "H4": mt5.TIMEFRAME_H4, "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8, "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def timeframe_from_string(name: str) -> int:
    if name not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported timeframe '{name}'. "
            f"Valid: {', '.join(TIMEFRAME_MAP)}"
        )
    return TIMEFRAME_MAP[name]


class MovingAverageCrossStrategy:
    """Built-in MA crossover strategy (default)."""

    def __init__(self, fast_period: int, slow_period: int, timeframe: int) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be lower than slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.timeframe = timeframe

    @staticmethod
    def _sma(values: list[float], period: int) -> float:
        return sum(values[-period:]) / period

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        bars_needed = self.slow_period + 3
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, bars_needed)
        if rates is None or len(rates) < bars_needed:
            return None

        closes = [float(row["close"]) for row in rates]
        fast_prev = self._sma(closes[:-1], self.fast_period)
        fast_curr = self._sma(closes, self.fast_period)
        slow_prev = self._sma(closes[:-1], self.slow_period)
        slow_curr = self._sma(closes, self.slow_period)

        candle_time = datetime.fromtimestamp(
            int(rates[-1]["time"]), tz=timezone.utc,
        )

        if fast_prev <= slow_prev and fast_curr > slow_curr:
            return StrategySignal(
                side="buy", reason="MA bullish crossover",
                candle_time_utc=candle_time,
            )
        if fast_prev >= slow_prev and fast_curr < slow_curr:
            return StrategySignal(
                side="sell", reason="MA bearish crossover",
                candle_time_utc=candle_time,
            )
        return None


def _load_custom_strategy(class_path: str, config: BotConfig) -> Strategy:
    if ":" not in class_path:
        raise ValueError("STRATEGY_CLASS_PATH must be 'module.path:ClassName'")
    module_name, class_name = class_path.split(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(f"Strategy class not found: {class_name} in {module_name}")
    try:
        instance = cls(config)
    except TypeError:
        instance = cls()
    if not hasattr(instance, "generate_signal"):
        raise ValueError(
            f"Custom strategy {class_path} must implement generate_signal(symbol)"
        )
    return instance


def create_strategy(config: BotConfig) -> Strategy:
    if config.strategy_class_path:
        return _load_custom_strategy(config.strategy_class_path, config)
    if config.strategy_name.lower() == "ma_cross":
        return MovingAverageCrossStrategy(
            fast_period=config.fast_ma,
            slow_period=config.slow_ma,
            timeframe=timeframe_from_string(config.timeframe),
        )
    raise ValueError(
        "Unsupported STRATEGY_NAME. Use 'ma_cross' or set STRATEGY_CLASS_PATH."
    )
