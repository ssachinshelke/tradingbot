# Tradingm5 - Multi-Account Trading Platform

Local multi-account MT5 trading platform with:
- realtime dashboard (health, orders, positions, floating P/L)
- advanced JSON order workflows
- one-click close by account/symbol
- offline 7-day trial + signed license activation

---

## 1) Quick Start (New User)

### Prerequisites
- Windows 10/11
- Python 3.11 (MT5 requirement)
- MetaTrader 5 terminal(s) installed

### Install
```powershell
py -3.11 -m venv .venv311
.venv311\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

If PowerShell blocks activation:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv311\Scripts\Activate.ps1
```

### Start Local UI
```powershell
python run_ui.py
```

Open:
- [http://127.0.0.1:8787](http://127.0.0.1:8787)

---

## 1.1) Release Onboarding (Windows + macOS)

This section is for binary users (no source code access needed).

### What is supported in the release
- Single-click launch:
  - Windows: `Tradingm5UI.exe` (double-click)
  - macOS: `start_ui.command` (double-click) or `./Tradingm5UI`
- Add/edit/delete accounts from UI (`Accounts` tab)
- Place different orders on multiple accounts in parallel (`Trading` tab)
- One-click Preflight (green/red readiness report before trading)
- Live P/L and live book updates via WebSocket snapshots
- History view with `All Executions` / `Closed Only` modes
- Auto-create MT5 portable folders from UI (`Accounts` tab, Windows only)
- Account limit guard: max `4` accounts per UI build (to reduce execution delay)

### Windows user guide (end-to-end)
1. Download release zip and extract.
2. Double-click `Tradingm5UI.exe`.
3. In `Accounts` tab:
   - Add Account 1 and Account 2.
   - Run `Healthcheck All` and confirm both are OK.
4. Optional: auto-create portable MT5 folders:
   - Open `Auto-Create Portable MT5 Folders (Windows)`.
   - Set source folder (must contain `terminal64.exe`), target root, and names.
   - Click `Create Portable Folders`.
5. Go to `Trading` tab:
   - Click `Run Preflight` and confirm `Ready: YES`.
   - Add one order row per account.
   - Use `Find` to search valid symbols per account.
   - Click `Submit All Orders`.
6. Verify realtime:
   - `Live Book` shows open positions and floating P/L.
   - `History` shows executions and close profit.

### macOS user guide
1. Download macOS release zip and extract.
2. Run `start_ui.command` (first launch may require Security approval).
3. Open `http://127.0.0.1:8787`.
4. Use `Accounts`, `Trading`, `Live Book`, and `History` tabs the same way as Windows.

Important for macOS:
- MT5 Python integration is Windows-first.
- Full live trading/order execution depends on MT5 runtime availability on that macOS setup.
- For production trading reliability, Windows remains the primary supported runtime.

---

## 2) Configuration Files

### `.env`
Main runtime configuration. Minimum required:
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`

For licensing:
- `LICENSE_PUBLIC_KEY_B64` (vendor public key for license validation)

### `accounts.json`
Multi-account credentials and terminal paths.

Example:
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

### `order_plan.advanced.json`
Single standard format for multi-account order execution.
Supports simple and advanced workflows.

---

## 3) Healthcheck Commands

Use these before live trading.

### Single-account healthcheck
```powershell
python tests/run_test.py healthcheck --symbol EURUSD
```

### Multi-account healthcheck
```powershell
python tests/run_test.py multi-healthcheck --accounts-file accounts.json --symbol EURUSD
```

### Discover MT5 terminal paths
```powershell
python tests/run_test.py discover-terminal
```

### Pending visibility test
```powershell
python tests/run_test.py pending-test --symbol EURUSD --volume 0.01
python tests/run_test.py pending-test-all --accounts-file accounts.json --symbol EURUSD
```

---

## 4) Order Placement Commands

## Standard multi-account command (recommended)
```powershell
python main.py multi-advanced-plan --plan-file order_plan.advanced.json --accounts-file accounts.json
```

### Simple market order row
```json
[
  {
    "account": "account-1",
    "symbol": "EURUSD",
    "side": "buy",
    "volume": 0.1,
    "comment": "market-buy"
  }
]
```

### Simple triggered order row
```json
[
  {
    "account": "account-1",
    "symbol": "EURUSD",
    "side": "buy",
    "volume": 0.1,
    "trigger_price": 1.1000,
    "sl_price": 1.0900,
    "tp_price": 1.1200,
    "comment": "buy-at-1.1000"
  }
]
```

### Advanced chained workflow (entry + on_fill)
```json
[
  {
    "account": "account-1",
    "symbol": "EURUSD",
    "entry": {
      "side": "buy",
      "volume": 0.1,
      "trigger_price": 1.1000,
      "comment": "entry"
    },
    "on_fill": {
      "side": "sell",
      "volume": 0.1,
      "trigger_price": 1.2000,
      "comment": "exit"
    },
    "timeout_seconds": 43200
  }
]
```

### Close via JSON (no ticket required)
```json
[
  {
    "account": "account-1",
    "symbol": "EURUSD",
    "action": "close",
    "side": "all",
    "comment": "close-eurusd"
  }
]
```

### Other useful trading commands
```powershell
python main.py status
python main.py positions --symbol EURUSD
python main.py orders --symbol EURUSD
python main.py history --days 7
python main.py close --ticket 12345678
```

---

## 5) Add Multiple MT5 Portable Terminals

Create isolated terminal copies for stable multi-account execution:

```powershell
python tests/run_test.py create-portable --source-dir "C:\Program Files\MetaTrader 5" --names acc1,acc2,acc3 --append-accounts
```

What this does:
- clones MT5 into `mt5-portable/<name>`
- creates `start-portable.bat` in each copy
- appends account stubs to `accounts.json` (if `--append-accounts` is used)

After that:
1. open `accounts.json`
2. fill each account login/password/server
3. run multi-healthcheck

---

## 6) Multi-Account Workflow (Recommended)

1. Add all accounts in `accounts.json` (or via UI).
2. Run healthcheck on all accounts.
3. Prepare `order_plan.advanced.json`.
4. Execute:
   ```powershell
   python main.py multi-advanced-plan --plan-file order_plan.advanced.json --accounts-file accounts.json
   ```
5. Monitor live in UI or with `positions`/`orders`.

Execution model:
- parallel across accounts
- sequential within each account workflow chain

---

## 7) Local UI Features

UI provides one dashboard for:
- account CRUD + per-account/all-account healthcheck
- quick multi-order form (minimal input: symbol, side, volume, optional trigger/sl/tp)
- one-click preflight readiness checks before order submission
- plan submission
- live active positions + pending orders
- realtime floating P/L
- one-click close by row and close selected account+symbol groups
- license status + activation

Run:
```powershell
python run_ui.py
```

---

## 8) Realtime Behavior

- backend WebSocket pushes snapshots every 1 second
- UI updates profit/positions/pending orders in near realtime
- trading submissions support idempotent `request_id`

---

## 9) Licensing (7-day trial + offline activation)

Runtime states:
- `trial_active`
- `trial_expired`
- `license_valid`
- `license_invalid`

### Get machine hash (customer machine)
```powershell
python tools/license_machine_id.py
```

### Vendor issues signed license file
```powershell
python tools/license_issuer.py --private-key-b64 "<PRIVATE_KEY_B64>" --customer-id "cust-001" --machine-hash "<MACHINE_HASH>" --days 365 --output "license.json"
```

### Activate in app
- UI: use License section and provide file path
- API: `POST /api/license/activate`

Notes:
- private key must stay with vendor only
- public key goes to `LICENSE_PUBLIC_KEY_B64` on client side
- this is an offline hardened licensing model (strong deterrence, not mathematically unbreakable)
- optional production server-side validation is supported with:
  - `LICENSE_VALIDATION_URL`
  - `LICENSE_VALIDATION_TOKEN`
  - `LICENSE_REQUIRE_ONLINE_VALIDATION=true` (fail closed if validation service is unavailable)

---

## 10) Packaging for Distribution (Code Not Exposed)

`.pyc` alone is not enough for protection. Use executable packaging.

Important:
- Build **Windows release on Windows**
- Build **macOS release on macOS**
- PyInstaller does not reliably cross-compile between OS families.

### Windows release
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_bundle.ps1
```

For a zipped release bundle:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release_zip.ps1 -Version v1
```

Outputs:
- `dist/Tradingm5UI.exe`
- `release/Tradingm5UI_<version>_<timestamp>.zip`

Launch helper (dev machine):
```powershell
.\scripts\start_ui.bat
```

### macOS release
```bash
chmod +x ./scripts/build_macos_bundle.sh
./scripts/build_macos_bundle.sh v1
```

Outputs:
- `dist/Tradingm5UI` (native macOS binary)
- `release/Tradingm5UI_<version>_macOS_<timestamp>.zip`

Launch helper:
```bash
chmod +x ./scripts/start_ui.command
./scripts/start_ui.command
```

Recommended hardening for production distribution:
- code sign binaries (Windows Authenticode, Apple Developer ID)
- notarize macOS binary/app before sharing externally

### Hardened production build (Nuitka)
Use Nuitka builds for stronger reverse-engineering resistance of Python logic.

Windows:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_nuitka.ps1 -Version v1
```

macOS:
```bash
chmod +x ./scripts/build_macos_nuitka.sh
./scripts/build_macos_nuitka.sh v1
```

### Binary signing
Windows signing:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sign_windows.ps1 -FilePath ".\dist\Tradingm5UI.exe" -CertFile "C:\path\codesign.pfx" -CertPassword "<password>"
```

macOS signing:
```bash
chmod +x ./scripts/sign_macos.sh
./scripts/sign_macos.sh "./dist/Tradingm5UI" "Developer ID Application: YOUR_ORG"
```

### Automatic release via GitHub (Windows + macOS)
This repo includes `.github/workflows/release.yml`.

How it works:
- push a tag like `v1.0.0`
- GitHub Actions builds Windows and macOS bundles in parallel
- both zip files are attached to a GitHub Release automatically

Example:
```bash
git tag v1.0.0
git push origin v1.0.0
```

Note for macOS:
- CI can build/package the macOS binary bundle.
- MetaTrader5 Python integration is Windows-first, so actual live MT5 trading features depend on MT5 runtime availability.

---

## 11) Requirements

`requirements.txt` includes:
- `MetaTrader5` (Python < 3.12)
- `python-dotenv`
- `numpy`
- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `cryptography`

---

## 12) Troubleshooting

### `Invalid "comment" argument`
- broker rejects comment field for some order types
- platform now retries with empty comment automatically

### `Unsupported filling mode`
- broker/symbol does not support fixed mode
- platform now tries multiple filling modes automatically

### PowerShell venv activation blocked
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv311\Scripts\Activate.ps1
```

### `Market closed`
- broker session is closed for symbol/account

### Command keeps running in advanced plan
- by design, chained workflows wait for conditions
- standalone entry-only rows now place and exit immediately

---

## 13) Safety Notes

- Always test on demo accounts first.
- Keep `.env` and `accounts.json` private.
- Validate symbol names per broker (`EURUSD`, `EURUSDm`, etc.).
- Use SL/TP unless you intentionally want manual exits.
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
