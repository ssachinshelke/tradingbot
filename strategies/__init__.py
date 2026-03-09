"""Custom trading strategy implementations.

Each file exports a class with a ``generate_signal(symbol) -> StrategySignal | None``
method.  Point your ``.env`` at any of them:

    STRATEGY_CLASS_PATH=strategies.rsi_reversal:RSIReversalStrategy
"""
