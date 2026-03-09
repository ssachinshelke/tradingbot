"""Test & diagnostic CLI – separated from the main trading commands.

Run from the project root:
    python tests/run_test.py <command> [options]

Commands:
    healthcheck             Single-account connectivity + symbol check
    multi-healthcheck       Check all configured accounts in parallel
    pending-test            Place far pending orders to verify broker visibility
    pending-test-all        Pending visibility test on all accounts
    dry-run                 Quick dry-run order test (no real execution)
    discover-terminal       Find MT5 terminal executables on this machine
    create-portable         Clone MT5 terminal into portable copies
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MT5 Bot – Test & Diagnostic Tools",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # healthcheck
    hc = sub.add_parser("healthcheck", help="Single-account connectivity check")
    hc.add_argument("--symbol", default=None)

    # multi-healthcheck
    mhc = sub.add_parser(
        "multi-healthcheck", help="Check all configured accounts",
    )
    mhc.add_argument("--accounts-file", default=None)
    mhc.add_argument("--symbol", default=None)
    mhc.add_argument("--timeout-seconds", type=int, default=30)

    # pending-test
    pt = sub.add_parser(
        "pending-test",
        help="Place far pending orders on primary account",
    )
    pt.add_argument("--symbol", default=None)
    pt.add_argument("--volume", type=float, default=0.01)
    pt.add_argument("--buy-factor", type=float, default=0.5)
    pt.add_argument("--sell-factor", type=float, default=2.0)

    # pending-test-all
    pta = sub.add_parser(
        "pending-test-all",
        help="Place far pending orders on all configured accounts",
    )
    pta.add_argument("--accounts-file", default=None)
    pta.add_argument("--symbol", default=None)
    pta.add_argument("--volume", type=float, default=0.01)
    pta.add_argument("--buy-factor", type=float, default=0.5)
    pta.add_argument("--sell-factor", type=float, default=2.0)
    pta.add_argument("--timeout-seconds", type=int, default=30)

    # dry-run
    dr = sub.add_parser(
        "dry-run", help="Quick dry-run order test (no real execution)",
    )
    dr.add_argument("--side", required=True, choices=["buy", "sell"])
    dr.add_argument("--symbol", default=None)
    dr.add_argument("--volume", type=float, default=0.01)
    dr.add_argument(
        "--broker-check", action="store_true",
        help="Use broker-side order_check (slower)",
    )
    dr.add_argument("--accounts-file", default=None, help="Multi-account dry-run")
    dr.add_argument("--timeout-seconds", type=int, default=30)

    # discover-terminal
    sub.add_parser("discover-terminal", help="Find MT5 terminal executables")

    # create-portable
    cp = sub.add_parser("create-portable", help="Create portable MT5 copies")
    cp.add_argument("--source-dir", required=True)
    default_root = str(PROJECT_ROOT / "mt5-portable")
    cp.add_argument("--target-root", default=default_root)
    cp.add_argument("--names", required=True, help="Comma-separated copy names")
    cp.add_argument("--accounts-file", default="accounts.json")
    cp.add_argument("--append-accounts", action="store_true")

    return parser


# ── Utility functions (no MT5 dependency) ─────────────────────────────────

def discover_mt5_terminals() -> list[str]:
    candidates: list[str] = []
    roots = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path(os.getenv("LOCALAPPDATA", "")),
        Path(os.getenv("APPDATA", "")),
    ]
    names = {"terminal64.exe", "terminal.exe"}
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower() in names:
                    full = str(Path(dirpath) / fn)
                    low = full.lower()
                    if low in seen:
                        continue
                    seen.add(low)
                    if "metatrader" in low or "terminal64.exe" in low:
                        candidates.append(full)
    return sorted(candidates)


def create_portable_copies(
    source_dir: str, target_root: str, names_csv: str,
    accounts_file: str, append_accounts: bool,
) -> list[dict]:
    source = Path(source_dir)
    if not (source / "terminal64.exe").exists():
        raise FileNotFoundError(
            f"terminal64.exe not found in source dir: {source_dir}"
        )
    root = Path(target_root)
    root.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in names_csv.split(",") if n.strip()]
    if not names:
        raise ValueError("No copy names provided")

    created: list[dict] = []
    for name in names:
        dst = root / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(source, dst)
        bat = dst / "start-portable.bat"
        bat.write_text(
            '@echo off\r\nstart "" "%~dp0terminal64.exe" /portable\r\n',
            encoding="utf-8",
        )
        created.append({"name": name, "mt5_path": str(dst / "terminal64.exe")})

    if append_accounts:
        acc_path = Path(accounts_file)
        existing: list[dict] = []
        if acc_path.exists():
            raw = json.loads(acc_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        for item in created:
            existing.append({
                "name": item["name"],
                "mt5_login": 0,
                "mt5_password": "fill_me",
                "mt5_server": "MetaQuotes-Demo",
                "mt5_path": item["mt5_path"],
                "mt5_portable": True,
            })
        acc_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    return created


# ── Command handlers ──────────────────────────────────────────────────────

def _require_mt5():
    if sys.version_info >= (3, 12):
        raise RuntimeError(
            "Python 3.12+ detected. MetaTrader5 requires Python 3.10 or 3.11."
        )


def cmd_healthcheck(args) -> None:
    _require_mt5()
    from mt5_bot.client import TradingBot
    from mt5_bot.config import load_config

    cfg = load_config()
    bot = TradingBot(cfg)
    bot.start()
    try:
        snap = bot.client.account_snapshot()
        symbol = args.symbol or cfg.default_symbol
        bot.client.ensure_symbol(symbol)
        spread = bot.spread_in_pips(symbol)
        print("MT5 healthcheck: OK")
        print(
            f"Login={snap.login} Server={snap.server} "
            f"Balance={snap.balance:.2f} Equity={snap.equity:.2f} "
            f"FreeMargin={snap.margin_free:.2f} {snap.currency}"
        )
        print(f"Symbol={symbol} SpreadPips={spread:.2f}")
    finally:
        bot.stop()


def cmd_multi_healthcheck(args) -> None:
    _require_mt5()
    from mt5_bot.config import load_accounts, load_config
    from mt5_bot.multi import healthcheck_all_accounts

    cfg = load_config()
    accounts = load_accounts(args.accounts_file or cfg.accounts_file)
    symbol = args.symbol or cfg.default_symbol
    results = healthcheck_all_accounts(
        cfg, accounts, symbol=symbol, timeout_seconds=args.timeout_seconds,
    )
    ok = 0
    for item in results:
        if item.get("ok"):
            ok += 1
            print(
                f"[OK] {item['name']} login={item['login']} "
                f"server={item['server']} balance={item['balance']:.2f} "
                f"equity={item['equity']:.2f} "
                f"{item['symbol']} spread={item['spread_pips']:.2f} pips"
            )
        else:
            print(
                f"[FAIL] {item.get('name', '?')} login={item.get('login')} "
                f"error={item.get('error')}"
            )
    print(f"Completed: {ok}/{len(results)} accounts healthy")


def cmd_pending_test(args) -> None:
    _require_mt5()
    from mt5_bot.client import TradingBot
    from mt5_bot.config import load_config

    cfg = load_config()
    bot = TradingBot(cfg)
    bot.start()
    try:
        snap = bot.client.account_snapshot()
        symbol = args.symbol or cfg.default_symbol
        ask = bot.client.current_price(symbol, "buy")
        bid = bot.client.current_price(symbol, "sell")
        buy_limit = bot.client.normalize_price(symbol, ask * args.buy_factor)
        sell_limit = bot.client.normalize_price(symbol, bid * args.sell_factor)
        buy_res = bot.client.send_limit_order(
            symbol=symbol, side="buy", volume=args.volume,
            price=buy_limit, comment="",
        )
        sell_res = bot.client.send_limit_order(
            symbol=symbol, side="sell", volume=args.volume,
            price=sell_limit, comment="",
        )
        bt = int(buy_res.get("order", 0) or 0)
        st = int(sell_res.get("order", 0) or 0)
        print(f"Pending test for login={snap.login} server={snap.server}")
        print(
            f"BUY_LIMIT  ticket={bt} price={buy_limit} "
            f"visible={bot.client.pending_order_exists(bt) if bt else False} "
            f"retcode={buy_res.get('retcode')}"
        )
        print(
            f"SELL_LIMIT ticket={st} price={sell_limit} "
            f"visible={bot.client.pending_order_exists(st) if st else False} "
            f"retcode={sell_res.get('retcode')}"
        )
    finally:
        bot.stop()


def cmd_pending_test_all(args) -> None:
    _require_mt5()
    from mt5_bot.config import load_accounts, load_config
    from mt5_bot.multi import pending_visibility_all_accounts

    cfg = load_config()
    accounts = load_accounts(args.accounts_file or cfg.accounts_file)
    symbol = args.symbol or cfg.default_symbol
    results = pending_visibility_all_accounts(
        cfg, accounts, symbol=symbol, volume=args.volume,
        buy_factor=args.buy_factor, sell_factor=args.sell_factor,
        timeout_seconds=args.timeout_seconds,
    )
    ok = 0
    for item in results:
        if item.get("ok"):
            ok += 1
            print(
                f"[OK] {item['name']} login={item['login']} "
                f"server={item['server']} | "
                f"BUY_LIMIT ticket={item['buy_ticket']} "
                f"visible={item['buy_visible']} "
                f"retcode={item['buy_retcode']} | "
                f"SELL_LIMIT ticket={item['sell_ticket']} "
                f"visible={item['sell_visible']} "
                f"retcode={item['sell_retcode']}"
            )
        else:
            print(
                f"[FAIL] {item.get('name', '?')} login={item.get('login')} "
                f"error={item.get('error')}"
            )
    print(f"Completed: {ok}/{len(results)} accounts tested")


def cmd_dry_run(args) -> None:
    _require_mt5()
    from mt5_bot.client import OrderPlan, TradingBot
    from mt5_bot.config import load_accounts, load_config
    from mt5_bot.multi import execute_order_plan

    cfg = load_config()
    symbol = args.symbol or cfg.default_symbol

    if args.accounts_file:
        accounts = load_accounts(args.accounts_file)
        plan_rows = [
            {
                "account": acc.name, "symbol": symbol, "side": args.side,
                "sl_pips": cfg.sl_pips, "tp_pips": cfg.tp_pips,
                "volume": args.volume, "comment": "dry-run-test",
                "force_fail": False,
            }
            for acc in accounts
        ]
        results = execute_order_plan(
            cfg, accounts, plan_rows,
            timeout_seconds=args.timeout_seconds,
            dry_run=True, broker_check=args.broker_check,
        )
        ok = sum(1 for r in results if r.get("ok"))
        for item in results:
            tag = "[OK]" if item.get("ok") else "[FAIL]"
            print(f"{tag} {item.get('name')} login={item.get('login')} "
                  f"error={item.get('error', '-')}")
        print(f"Dry-run: {ok}/{len(results)} passed")
    else:
        bot = TradingBot(cfg)
        bot.start()
        try:
            plan = OrderPlan(
                symbol=symbol, side=args.side,
                sl_pips=cfg.sl_pips, tp_pips=cfg.tp_pips,
                comment="dry-run-test",
            )
            result = bot.place_market_order(
                plan, volume_override=args.volume,
                dry_run=True, broker_check=args.broker_check,
            )
            print(f"Dry-run OK: {result['order_result']}")
        finally:
            bot.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()

    cmd = args.command
    if cmd == "discover-terminal":
        paths = discover_mt5_terminals()
        if not paths:
            print("No MT5 terminal found. Search for terminal64.exe manually.")
        else:
            print("Discovered terminals:")
            for p in paths:
                print(f"  {p}")
        return

    if cmd == "create-portable":
        created = create_portable_copies(
            args.source_dir, args.target_root, args.names,
            args.accounts_file, args.append_accounts,
        )
        print("Portable copies created:")
        for item in created:
            print(f"  {item['name']}: {item['mt5_path']}")
        if args.append_accounts:
            print(f"Account stubs appended to {args.accounts_file}")
        return

    dispatch = {
        "healthcheck": cmd_healthcheck,
        "multi-healthcheck": cmd_multi_healthcheck,
        "pending-test": cmd_pending_test,
        "pending-test-all": cmd_pending_test_all,
        "dry-run": cmd_dry_run,
    }
    handler = dispatch.get(cmd)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
