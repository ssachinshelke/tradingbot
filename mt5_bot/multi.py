"""Multi-account parallel order execution and health checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from multiprocessing import Process, Queue
import time
from typing import Any

from .client import OrderPlan, TradingBot
from .config import AccountConfig, BotConfig
from .journal import DispatchJournal


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_results(
    processes: list[Process],
    expected_count: int,
    queue: Queue,
    timeout_seconds: int,
    expected_names: list[str] | None = None,
) -> list[dict]:
    results: list[dict] = []
    pending_names = list(expected_names or [])
    deadline = time.monotonic() + max(1, timeout_seconds)
    for _ in range(expected_count):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            name = pending_names.pop(0) if pending_names else "unknown"
            results.append({
                "ok": False, "name": name,
                "error": "Timed out waiting for account worker",
            })
            continue
        try:
            item = queue.get(timeout=remaining)
            results.append(item)
            item_name = item.get("name")
            if item_name in pending_names:
                pending_names.remove(item_name)
        except Exception:
            name = pending_names.pop(0) if pending_names else "unknown"
            results.append({
                "ok": False, "name": name,
                "error": "Timed out waiting for account worker",
            })
    for proc in processes:
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
    return results


def _make_config(base: BotConfig, account: AccountConfig) -> BotConfig:
    return replace(
        base,
        mt5_login=account.mt5_login,
        mt5_password=account.mt5_password,
        mt5_server=account.mt5_server,
        mt5_path=account.mt5_path or base.mt5_path,
        mt5_portable=account.mt5_portable,
    )


# ── Workers ───────────────────────────────────────────────────────────────

def _order_worker(
    base_config: BotConfig, account: AccountConfig,
    plan: OrderPlan, volume_override: float | None,
    dispatch_id: str, target_send_epoch: float | None,
    dry_run: bool, broker_check: bool, force_fail: bool,
    queue: Queue,
) -> None:
    bot = TradingBot(_make_config(base_config, account))
    placed_at = ack_at = ""
    latency_ms = 0.0
    try:
        bot.start()
        working_plan = plan
        worker_volume = volume_override
        if force_fail:
            working_plan = OrderPlan(
                symbol="INVALID_SYMBOL_TEST", side=plan.side,
                sl_pips=plan.sl_pips, tp_pips=plan.tp_pips,
                comment=f"{plan.comment}:force-fail",
            )
            worker_volume = 0.01
        if target_send_epoch is not None:
            wait = target_send_epoch - time.time()
            if wait > 0:
                time.sleep(wait)
        t0 = time.perf_counter()
        placed_at = _now_utc_iso()
        result = bot.place_market_order(
            working_plan, volume_override=worker_volume,
            dry_run=dry_run, broker_check=broker_check,
        )
        t1 = time.perf_counter()
        ack_at = _now_utc_iso()
        latency_ms = (t1 - t0) * 1000.0
        queue.put({
            "dispatch_id": dispatch_id, "name": account.name,
            "login": account.mt5_login, "symbol": plan.symbol,
            "side": plan.side, "mode": "dry_run" if dry_run else "live",
            "ok": True, "result": result["order_result"],
            "volume": result["volume"],
            "placed_at_utc": placed_at, "ack_at_utc": ack_at,
            "latency_ms": latency_ms,
        })
    except Exception as err:
        queue.put({
            "dispatch_id": dispatch_id, "name": account.name,
            "login": account.mt5_login, "symbol": plan.symbol,
            "side": plan.side, "mode": "dry_run" if dry_run else "live",
            "ok": False, "error": str(err),
            "placed_at_utc": placed_at or _now_utc_iso(),
            "ack_at_utc": ack_at or _now_utc_iso(),
            "latency_ms": latency_ms,
        })
    finally:
        try:
            bot.stop()
        except Exception:
            pass


def _healthcheck_worker(
    base_config: BotConfig, account: AccountConfig,
    symbol: str, queue: Queue,
) -> None:
    bot = TradingBot(_make_config(base_config, account))
    try:
        bot.start()
        snap = bot.client.account_snapshot()
        bot.client.ensure_symbol(symbol)
        spread = bot.spread_in_pips(symbol)
        queue.put({
            "name": account.name, "login": account.mt5_login,
            "ok": True, "server": snap.server,
            "balance": snap.balance, "equity": snap.equity,
            "symbol": symbol, "spread_pips": spread,
        })
    except Exception as err:
        queue.put({
            "name": account.name, "login": account.mt5_login,
            "ok": False, "error": str(err),
        })
    finally:
        try:
            bot.stop()
        except Exception:
            pass


def _pending_visibility_worker(
    base_config: BotConfig, account: AccountConfig,
    symbol: str, volume: float,
    buy_factor: float, sell_factor: float,
    queue: Queue,
) -> None:
    bot = TradingBot(_make_config(base_config, account))
    try:
        bot.start()
        snap = bot.client.account_snapshot()
        ask = bot.client.current_price(symbol, "buy")
        bid = bot.client.current_price(symbol, "sell")
        buy_limit = bot.client.normalize_price(symbol, ask * buy_factor)
        sell_limit = bot.client.normalize_price(symbol, bid * sell_factor)
        buy_res = bot.client.send_limit_order(
            symbol=symbol, side="buy", volume=volume,
            price=buy_limit, comment="",
        )
        sell_res = bot.client.send_limit_order(
            symbol=symbol, side="sell", volume=volume,
            price=sell_limit, comment="",
        )
        buy_ticket = int(buy_res.get("order", 0) or 0)
        sell_ticket = int(sell_res.get("order", 0) or 0)
        queue.put({
            "ok": True, "name": account.name,
            "login": snap.login, "server": snap.server,
            "buy_ticket": buy_ticket, "buy_price": buy_limit,
            "buy_visible": bot.client.pending_order_exists(buy_ticket) if buy_ticket else False,
            "buy_retcode": buy_res.get("retcode"),
            "sell_ticket": sell_ticket, "sell_price": sell_limit,
            "sell_visible": bot.client.pending_order_exists(sell_ticket) if sell_ticket else False,
            "sell_retcode": sell_res.get("retcode"),
        })
    except Exception as err:
        queue.put({
            "ok": False, "name": account.name,
            "login": account.mt5_login, "error": str(err),
        })
    finally:
        try:
            bot.stop()
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────

def healthcheck_all_accounts(
    base_config: BotConfig, accounts: list[AccountConfig],
    symbol: str, timeout_seconds: int = 60,
) -> list[dict]:
    queue: Queue = Queue()
    procs: list[Process] = []
    names: list[str] = []
    for acc in accounts:
        p = Process(target=_healthcheck_worker, args=(base_config, acc, symbol, queue))
        p.daemon = True
        p.start()
        procs.append(p)
        names.append(acc.name)
    return _collect_results(procs, len(accounts), queue, timeout_seconds, names)


def load_order_plan(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.loads(f.read())
    if not isinstance(raw, list):
        raise ValueError("Order plan must be a JSON array")
    normalised: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Order plan row #{idx} must be an object")
        if "account" not in item or "side" not in item or "symbol" not in item:
            raise ValueError(f"Order plan row #{idx} needs account, side, symbol")
        side = str(item["side"]).lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"Order plan row #{idx} side must be buy/sell")
        normalised.append({
            "account": str(item["account"]).strip(),
            "symbol": str(item["symbol"]).strip(),
            "side": side,
            "sl_pips": float(item.get("sl_pips", 25)),
            "tp_pips": float(item.get("tp_pips", 50)),
            "volume": (
                float(item["volume"])
                if "volume" in item and item["volume"] is not None
                else None
            ),
            "comment": str(item.get("comment", "mt5-bot:plan")),
            "force_fail": bool(item.get("force_fail", False)),
        })
    if not normalised:
        raise ValueError("Order plan is empty")
    return normalised


def execute_order_plan(
    base_config: BotConfig, accounts: list[AccountConfig],
    plan_rows: list[dict[str, Any]],
    timeout_seconds: int = 60,
    sync_send_delay_ms: int | None = None,
    dry_run: bool = False, broker_check: bool = False,
) -> list[dict]:
    account_lookup = {a.name: a for a in accounts}
    queue: Queue = Queue()
    procs: list[Process] = []
    names: list[str] = []
    dispatch_id = f"dispatch-{int(time.time())}"
    target_epoch = (
        time.time() + (sync_send_delay_ms / 1000.0)
        if sync_send_delay_ms is not None and sync_send_delay_ms >= 0
        else None
    )
    for row in plan_rows:
        acct_name = row["account"]
        if acct_name not in account_lookup:
            raise ValueError(f"Order plan account not found: {acct_name}")
        order_plan = OrderPlan(
            symbol=row["symbol"], side=row["side"],
            sl_pips=row["sl_pips"], tp_pips=row["tp_pips"],
            comment=row["comment"],
        )
        p = Process(
            target=_order_worker,
            args=(
                base_config, account_lookup[acct_name], order_plan,
                row["volume"], dispatch_id, target_epoch,
                dry_run, broker_check, bool(row.get("force_fail", False)),
                queue,
            ),
        )
        p.daemon = True
        p.start()
        procs.append(p)
        names.append(acct_name)

    results = _collect_results(procs, len(plan_rows), queue, timeout_seconds, names)
    journal = DispatchJournal(base_config.dispatch_journal_path)
    for item in results:
        order_data = item.get("result", {}) if item.get("ok") else {}
        journal.append({
            "dispatch_id": item.get("dispatch_id", dispatch_id),
            "account_name": item.get("name"),
            "account_login": item.get("login"),
            "symbol": item.get("symbol"),
            "side": item.get("side"),
            "volume": item.get("volume"),
            "placed_at_utc": item.get("placed_at_utc"),
            "ack_at_utc": item.get("ack_at_utc"),
            "latency_ms": f"{float(item.get('latency_ms', 0.0)):.2f}",
            "order_id": order_data.get("order"),
            "deal_id": order_data.get("deal"),
            "retcode": order_data.get("retcode"),
            "mode": item.get("mode", "live"),
            "status": (
                ("DRY_OK" if item.get("ok") else "DRY_FAIL")
                if dry_run
                else ("OK" if item.get("ok") else "FAIL")
            ),
            "error": item.get("error", ""),
        })
    return results


def pending_visibility_all_accounts(
    base_config: BotConfig, accounts: list[AccountConfig],
    symbol: str, volume: float,
    buy_factor: float, sell_factor: float,
    timeout_seconds: int = 30,
) -> list[dict]:
    queue: Queue = Queue()
    procs: list[Process] = []
    names: list[str] = []
    for acc in accounts:
        p = Process(
            target=_pending_visibility_worker,
            args=(base_config, acc, symbol, volume, buy_factor, sell_factor, queue),
        )
        p.daemon = True
        p.start()
        procs.append(p)
        names.append(acc.name)
    return _collect_results(procs, len(accounts), queue, timeout_seconds, names)
