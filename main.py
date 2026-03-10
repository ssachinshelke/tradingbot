"""MT5 Trading Bot – main CLI.

Trading commands only.  For diagnostics/tests see: python tests/run_test.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MT5 Trading Bot")
    sub = p.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show account status")

    # order
    o = sub.add_parser("order", help="Place a market order")
    o.add_argument("--side", required=True, choices=["buy", "sell"])
    o.add_argument("--symbol", default=None)
    o.add_argument("--volume", type=float, default=None, help="Fixed lot size")
    o.add_argument("--sl-pips", type=float, default=None)
    o.add_argument("--tp-pips", type=float, default=None)
    o.add_argument("--comment", default="mt5-bot")

    # close
    c = sub.add_parser("close", help="Close a position by ticket")
    c.add_argument("--ticket", required=True, type=int)
    c.add_argument("--volume", type=float, default=None, help="Partial close volume")

    # cancel
    ca = sub.add_parser("cancel", help="Cancel a pending order by ticket")
    ca.add_argument("--ticket", required=True, type=int)

    # positions
    pos = sub.add_parser("positions", help="List open positions")
    pos.add_argument("--symbol", default=None)

    # orders (active pending)
    od = sub.add_parser("orders", help="List active pending orders")
    od.add_argument("--symbol", default=None)

    # history
    hi = sub.add_parser("history", help="Show recent trade history")
    hi.add_argument("--days", type=int, default=7, help="Lookback days (default 7)")

    # multi-advanced-plan
    ap = sub.add_parser(
        "multi-advanced-plan",
        help="Execute all multi-account orders from a JSON workflow plan",
    )
    ap.add_argument("--plan-file", required=True)
    ap.add_argument("--accounts-file", default=None)
    ap.add_argument("--timeout-seconds", type=int, default=3600)
    ap.add_argument("--poll-seconds", type=float, default=2.0)

    # run (automation loop)
    r = sub.add_parser("run", help="Run automated strategy loop")
    r.add_argument("--symbol", default=None)
    r.add_argument("--cycles", type=int, default=None)

    return p


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()

    if sys.version_info >= (3, 12):
        raise RuntimeError(
            "Python 3.12+ detected. MetaTrader5 requires Python 3.10 or 3.11."
        )

    from mt5_bot.client import MT5Client, OrderPlan, TradingBot
    from mt5_bot.config import load_accounts, load_config
    from mt5_bot.advanced_plan import (
        execute_advanced_order_plan,
        load_advanced_order_plan,
    )
    from mt5_bot.engine import TradingEngine
    from ui_backend.license_manager import LicenseManager

    cfg = load_config()
    cmd = args.command
    license_status = LicenseManager().status()
    if license_status.status in ("trial_expired", "license_invalid"):
        raise RuntimeError(
            license_status.error
            or "License is invalid or expired. Activate license to continue."
        )

    # ── Single-account commands that need a bot instance ──────────────────

    if cmd in ("status", "order", "close", "cancel", "positions", "orders", "history"):
        bot = TradingBot(cfg)
        bot.start()
        try:
            if cmd == "status":
                snap = bot.client.account_snapshot()
                print(
                    f"Login={snap.login}  Server={snap.server}\n"
                    f"Balance={snap.balance:.2f}  Equity={snap.equity:.2f}  "
                    f"FreeMargin={snap.margin_free:.2f}  {snap.currency}"
                )

            elif cmd == "order":
                plan = OrderPlan(
                    symbol=args.symbol or cfg.default_symbol,
                    side=args.side,
                    sl_pips=args.sl_pips or cfg.sl_pips,
                    tp_pips=args.tp_pips or cfg.tp_pips,
                    comment=args.comment,
                )
                result = bot.place_market_order(plan, volume_override=args.volume)
                r = result["order_result"]
                print(
                    f"Order placed: {result['side']} {result['symbol']} "
                    f"volume={result['volume']:.2f} "
                    f"order={r.get('order')} retcode={r.get('retcode')}"
                )

            elif cmd == "close":
                positions = bot.client.positions(ticket=args.ticket)
                if not positions:
                    print(f"No open position with ticket {args.ticket}")
                    return
                r = bot.client.close_position(
                    positions[0], volume=args.volume, comment="manual-close",
                )
                print(
                    f"Position {args.ticket} closed. "
                    f"retcode={r.get('retcode')} deal={r.get('deal')}"
                )

            elif cmd == "cancel":
                r = bot.client.cancel_order(args.ticket)
                print(
                    f"Order {args.ticket} cancelled. retcode={r.get('retcode')}"
                )

            elif cmd == "positions":
                pos_list = bot.client.positions(symbol=args.symbol)
                if not pos_list:
                    print("No open positions.")
                    return
                for p in pos_list:
                    side = "BUY" if p.type == 0 else "SELL"
                    print(
                        f"  ticket={p.ticket}  {side}  {p.symbol}  "
                        f"vol={p.volume}  open={p.price_open:.5f}  "
                        f"profit={p.profit:.2f}  sl={p.sl}  tp={p.tp}"
                    )
                print(f"Total: {len(pos_list)} position(s)")

            elif cmd == "orders":
                ord_list = bot.client.active_orders(symbol=args.symbol)
                if not ord_list:
                    print("No active pending orders.")
                    return
                type_names = {
                    2: "BUY_LIMIT", 3: "SELL_LIMIT",
                    4: "BUY_STOP", 5: "SELL_STOP",
                    6: "BUY_STOP_LIMIT", 7: "SELL_STOP_LIMIT",
                }
                for o in ord_list:
                    tname = type_names.get(o.type, str(o.type))
                    print(
                        f"  ticket={o.ticket}  {tname}  {o.symbol}  "
                        f"vol={o.volume_current}  price={o.price_open:.5f}"
                    )
                print(f"Total: {len(ord_list)} order(s)")

            elif cmd == "history":
                from datetime import timedelta
                date_to = datetime.now(timezone.utc)
                date_from = date_to - timedelta(days=args.days)
                deals = bot.client.history_deals(
                    date_from=date_from, date_to=date_to,
                )
                if not deals:
                    print(f"No deals in the last {args.days} day(s).")
                    return
                for d in deals:
                    entry = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}.get(
                        d.entry, str(d.entry),
                    )
                    print(
                        f"  {d.symbol:10s}  {entry:6s}  vol={d.volume}  "
                        f"price={d.price:.5f}  profit={d.profit:.2f}  "
                        f"deal={d.ticket}"
                    )
                print(f"Total: {len(deals)} deal(s) in {args.days} day(s)")

        finally:
            bot.stop()
        return

    # ── Multi-account commands ────────────────────────────────────────────

    if cmd == "multi-advanced-plan":
        accounts = load_accounts(args.accounts_file or cfg.accounts_file)
        workflows = load_advanced_order_plan(args.plan_file)
        results = execute_advanced_order_plan(
            cfg=cfg,
            accounts=accounts,
            workflows=workflows,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        ok = 0
        for item in results:
            if item.get("ok"):
                ok += 1
                print(
                    f"[OK] {item.get('name')} login={item.get('login')} "
                    f"symbol={item.get('symbol')} "
                    f"steps={len(item.get('steps', []))}"
                )
            else:
                print(
                    f"[FAIL] {item.get('name', '?')} "
                    f"login={item.get('login')} "
                    f"error={item.get('error')}"
                )
        print(f"Completed: {ok}/{len(results)} successful workflows")
        return

    # ── Automation loop ───────────────────────────────────────────────────

    if cmd == "run":
        engine = TradingEngine(cfg)
        engine.run(
            symbol=args.symbol or cfg.default_symbol,
            max_cycles=args.cycles,
        )
        return


if __name__ == "__main__":
    main()
