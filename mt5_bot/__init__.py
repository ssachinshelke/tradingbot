"""MetaTrader5 bot package.

The MT5 Python module is imported here so every submodule can access it
with ``from . import mt5`` without circular-dependency issues.
"""

import sys


def _mt5_version_hint() -> str:
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        "MetaTrader5 Python package is not available in this interpreter. "
        f"Current Python: {py}. Use Python 3.10 or 3.11 on Windows, "
        "then reinstall dependencies."
    )


try:
    import MetaTrader5 as mt5  # type: ignore  # noqa: F401
except ModuleNotFoundError as exc:
    class _MT5Unavailable:
        """Import-time fallback so packaging can proceed on non-Windows CI."""

        _hint = _mt5_version_hint()

        def __getattr__(self, name: str):  # noqa: ANN001
            # Allow constant lookups at import time (TIMEFRAME_*, ORDER_*, etc.)
            if name.isupper():
                return 0
            raise RuntimeError(self._hint)

    mt5 = _MT5Unavailable()  # type: ignore[assignment]
