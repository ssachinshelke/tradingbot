# MT5 Trading Bot

Automated multi-account trading bot for MetaTrader 5 via the official Python API.
Supports market / limit / stop / stop-limit orders, risk management, pluggable
strategies, parallel multi-account execution, and a full trade journal.

---

## Quick Start

```bash
# 1. Create a Python 3.11 virtual environment (MT5 requires ≤ 3.11)
py -3.11 -m venv .venv311
.venv311\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
copy .env.example .env          # then fill MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
```

Verify everything works:

```bash
python tests/run_test.py healthcheck --symbol EURUSD
```

---

## Installation (detailed)

| Step | Command |
|------|---------|
| Install Python 3.11 | `winget install -e --id Python.Python.3.11` |
| Create venv | `py -3.11 -m venv .venv311` |
| Activate | `.venv311\Scripts\activate` |
| Install deps | `pip install -r requirements.txt` |
| Copy config | `copy .env.example .env` |
| Find terminal | `python tests/run_test.py discover-terminal` |
| Set `MT5_PATH` | Edit `.env` → full path to `terminal64.exe` |

> **Note**: The MetaTrader 5 Python package communicates with a locally running
> MT5 terminal via IPC.  The terminal must be installed and launched at least once.

---

## Configuration

### `.env` — primary config

| Variable | Default | Description |
|----------|---------|-------------|
| `MT5_LOGIN` | *required* | Account login number |
| `MT5_PASSWORD` | *required* | Account password |
| `MT5_SERVER` | *required* | Broker server name |
| `MT5_PATH` | | Full path to `terminal64.exe` |
| `MT5_PORTABLE` | `false` | Set `true` for portable terminal mode |
| `DEFAULT_SYMBOL` | `EURUSD` | Default trading symbol |
| `RISK_PER_TRADE` | `0.01` | Risk 1 % of balance per trade |
| `MAX_DAILY_LOSS_PCT` | `0.03` | Stop trading after 3 % daily drawdown |
| `MAX_OPEN_TRADES` | `3` | Global max simultaneous positions |
| `SL_PIPS` | `25` | Default stop-loss in pips |
| `TP_PIPS` | `50` | Default take-profit in pips |
| `DEVIATION` | `20` | Max price slippage (points) |
| `MAGIC_NUMBER` | `20260302` | Order magic number for identification |
| `TIMEFRAME` | `M5` | Candle timeframe (M1–MN1) |
| `STRATEGY_NAME` | `ma_cross` | Built-in strategy name |
| `STRATEGY_CLASS_PATH` | | Custom strategy `module:ClassName` |
| `POLL_INTERVAL_SECONDS` | `15` | Loop sleep between cycles |
| `COOLDOWN_SECONDS` | `60` | Min gap between consecutive trades |
| `MAX_SPREAD_PIPS` | `2.5` | Skip entry if spread exceeds this |
| `ENABLE_SESSION_FILTER` | `false` | Restrict to UTC time window |
| `SESSION_START_UTC` | `06:00` | Session window start |
| `SESSION_END_UTC` | `20:00` | Session window end |
| `ENABLE_BREAK_EVEN` | `true` | Auto break-even SL |
| `ENABLE_TRAILING_STOP` | `true` | Auto trailing SL |
| `ENABLE_PARTIAL_TP` | `true` | Auto partial close at TP trigger |

### `accounts.json` — multi-account config

```json
[
  {
    "name": "account-1",
    "mt5_login": 12345678,
    "mt5_password": "secret",
    "mt5_server": "MetaQuotes-Demo",
    "mt5_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
    "mt5_portable": false
  }
]
```

### `order_plan.advanced.json` — standard multi-account plan (single command)

Use this when you need chained behavior such as:
- place an entry at a trigger price
- place a second order when the first order is filled (`on_fill`)
- place a recovery/re-entry order when the first position closes at SL (`on_sl`)

It also supports simple flat rows (`side`, `volume`, `trigger_price`) so you can
use one format for both basic and advanced workflows.

```json
[
  {
    "account": "account-1",
    "symbol": "EURUSD",
    "entry": {
      "side": "buy",
      "volume": 0.1,
      "trigger_price": 1.1000,
      "sl_price": 1.0800,
      "tp_price": 1.1800,
      "comment": "entry-at-1.1000"
    },
    "on_fill": {
      "side": "buy",
      "volume": 0.1,
      "trigger_price": 1.2000,
      "sl_price": 1.1800,
      "tp_price": 1.2600,
      "comment": "second-order-at-1.2000"
    },
    "on_sl": {
      "side": "buy",
      "volume": 0.1,
      "trigger_price": 1.0500,
      "sl_price": 1.0300,
      "tp_price": 1.1000,
      "comment": "re-entry-after-sl"
    },
    "timeout_seconds": 43200
  }
]
```

---

## Trading Commands (`main.py`)

```bash
# Account status
python main.py status

# Place market order
python main.py order --side buy --symbol EURUSD
python main.py order --side sell --symbol EURUSD --volume 0.1 --sl-pips 20 --tp-pips 40

# Close position (full or partial)
python main.py close --ticket 12345678
python main.py close --ticket 12345678 --volume 0.05

# Cancel pending order
python main.py cancel --ticket 12345678

# List open positions
python main.py positions
python main.py positions --symbol EURUSD

# List active pending orders
python main.py orders

# Trade history (last N days)
python main.py history --days 7

# Standard multi-account command (simple + advanced JSON supported)
python main.py multi-advanced-plan --plan-file order_plan.advanced.json --accounts-file accounts.json

# Run automated strategy loop
python main.py run --symbol EURUSD
python main.py run --symbol EURUSD --cycles 50
```

---

## Test & Diagnostic Commands (`tests/run_test.py`)

```bash
# Single-account connectivity check
python tests/run_test.py healthcheck --symbol EURUSD

# Multi-account health check
python tests/run_test.py multi-healthcheck --accounts-file accounts.json --symbol EURUSD

# Place far pending orders to verify broker UI visibility
python tests/run_test.py pending-test --symbol EURUSD --volume 0.01
python tests/run_test.py pending-test-all --accounts-file accounts.json --symbol EURUSD

# Quick dry-run (no real orders)
python tests/run_test.py dry-run --side buy --symbol EURUSD
python tests/run_test.py dry-run --side buy --accounts-file accounts.json

# Find MT5 terminal executables on this machine
python tests/run_test.py discover-terminal

# Create portable MT5 copies for multi-account
python tests/run_test.py create-portable --source-dir "C:\Program Files\MetaTrader 5" --names acc1,acc2,acc3 --append-accounts
```

---

## Writing a Custom Strategy

Every strategy is a Python class with **one method**:

```python
def generate_signal(self, symbol: str) -> StrategySignal | None:
```

### Step 1 — Create a file in `strategies/`

```python
# strategies/my_strategy.py
from __future__ import annotations
from datetime import datetime, timezone
from mt5_bot import mt5
from mt5_bot.strategy import StrategySignal, timeframe_from_string


class MyStrategy:
    def __init__(self, config) -> None:
        self.timeframe = timeframe_from_string(config.timeframe)

    def generate_signal(self, symbol: str) -> StrategySignal | None:
        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, 50)
        if rates is None or len(rates) < 50:
            return None

        closes = [float(r["close"]) for r in rates]
        candle_time = datetime.fromtimestamp(
            int(rates[-1]["time"]), tz=timezone.utc,
        )

        # YOUR LOGIC HERE
        # Return StrategySignal(side="buy"|"sell", reason="...", candle_time_utc=...)
        # or None to skip this candle.

        return None
```

### Step 2 — Point `.env` at your class

```
STRATEGY_CLASS_PATH=strategies.my_strategy:MyStrategy
```

### Step 3 — Run

```bash
python main.py run --symbol EURUSD
```

### Available MT5 data inside your strategy

| Function | Description |
|----------|-------------|
| `mt5.copy_rates_from_pos(symbol, tf, 0, N)` | Last N OHLCV bars |
| `mt5.copy_rates_range(symbol, tf, dt_from, dt_to)` | Bars in date range |
| `mt5.symbol_info_tick(symbol)` | Latest bid/ask/last tick |
| `mt5.copy_ticks_from(symbol, dt, N, flags)` | Raw tick data |
| `mt5.symbol_info(symbol)` | Symbol specification (digits, lot sizes) |

### Built-in strategies (ready to use)

| Name | `.env` value | Logic |
|------|-------------|-------|
| MA Cross | `STRATEGY_NAME=ma_cross` | SMA fast/slow crossover |
| RSI Reversal | `strategies.rsi_reversal:RSIReversalStrategy` | RSI < 30 buy, > 70 sell |
| Bollinger Bounce | `strategies.bollinger_bounce:BollingerBounceStrategy` | Price vs Bollinger bands |
| MACD Momentum | `strategies.macd_momentum:MACDMomentumStrategy` | MACD histogram crossover |
| Breakout | `strategies.breakout:BreakoutStrategy` | Close breaks prev high/low |

---

## API Reference (library usage)

Use `MT5Client` directly for full programmatic control:

```python
from mt5_bot.client import MT5Client
from mt5_bot.config import load_config

cfg = load_config()
client = MT5Client(cfg)
client.connect()

# Account
snap = client.account_snapshot()

# Market data
rates = client.get_rates_pos("EURUSD", mt5.TIMEFRAME_M5, 0, 100)
tick  = client.symbol_tick("EURUSD")

# Orders — all types
client.send_market_order("EURUSD", "buy", 0.1, sl=1.0800, tp=1.1000)
client.send_limit_order("EURUSD", "buy", 0.1, price=1.0800)
client.send_stop_order("EURUSD", "buy", 0.1, price=1.1200)
client.send_stop_limit_order("EURUSD", "buy", 0.1, stop_price=1.12, limit_price=1.11)

# Modify / cancel
client.modify_position(ticket=123, symbol="EURUSD", sl=1.09, tp=1.11)
client.modify_pending_order(ticket=456, symbol="EURUSD", price=1.085)
client.cancel_order(ticket=456)

# Close
pos = client.positions(symbol="EURUSD")[0]
client.close_position(pos)                        # full close
client.close_position(pos, volume=0.05)            # partial close

# History
deals = client.history_deals(date_from=..., date_to=...)
orders = client.history_orders(date_from=..., date_to=...)

# Risk calculators
margin = client.calc_margin(mt5.ORDER_TYPE_BUY, "EURUSD", 0.1, 1.09)
profit = client.calc_profit(mt5.ORDER_TYPE_BUY, "EURUSD", 0.1, 1.09, 1.10)

# Market depth
client.depth_subscribe("EURUSD")
book = client.depth_get("EURUSD")
client.depth_unsubscribe("EURUSD")

client.shutdown()
```

---

## Project Structure

```
Tradingm5/
├── main.py                    # Trading CLI
├── requirements.txt
├── .env.example
├── accounts.example.json
├── order_plan.example.json
├── order_plan.advanced.example.json
│
├── mt5_bot/                   # Core package
│   ├── __init__.py            # MT5 import adapter
│   ├── config.py              # BotConfig, AccountConfig, loaders
│   ├── client.py              # MT5Client (all APIs) + TradingBot
│   ├── risk.py                # Risk management (lot sizing, loss limits)
│   ├── journal.py             # Trade + dispatch CSV journals
│   ├── strategy.py            # Strategy protocol + MA cross + factory
│   ├── engine.py              # Automated trading loop
│   ├── multi.py               # Multi-account parallel execution
│   └── advanced_plan.py       # Conditional workflow execution
│
├── strategies/                # Pluggable strategy examples
│   ├── breakout.py            # Price breakout
│   ├── rsi_reversal.py        # RSI mean-reversion
│   ├── bollinger_bounce.py    # Bollinger Band bounce
│   └── macd_momentum.py       # MACD histogram crossover
│
└── tests/                     # Diagnostic & test tools
    └── run_test.py            # Test CLI (healthcheck, dry-run, etc.)
```

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: MetaTrader5` | Python ≥ 3.12 or package not installed | Use Python 3.11: `py -3.11 -m venv .venv311` |
| `IPC initialize failed` | MT5 terminal not running or wrong path | Set `MT5_PATH` in `.env` to `terminal64.exe` |
| `retcode=10027 AutoTrading disabled` | Algo trading off in terminal | Enable AutoTrading button in MT5 toolbar |
| `retcode=10017 Trade disabled` | Broker restriction on account | Check account type & contact broker |
| `Invalid "comment" argument` | Broker rejects special chars in comment | Bot auto-sanitises; leave comment empty for pending orders |
| `Timed out waiting for worker` | MT5 init slow or broker check blocking | Use `--timeout-seconds 60` or skip `--broker-check` |
| `PermissionError` on multiprocessing | Sandbox restrictions | Run with full permissions outside sandbox |

---

## Notes

- **Always test on a demo account first.**
- Keep `.env` and `accounts.json` out of version control (`.gitignore` covers both).
- For multi-account: use separate MT5 terminal instances (portable mode) per account.
- Journal files (`trade_journal.csv`, `dispatch_journal.csv`) are generated at runtime.
