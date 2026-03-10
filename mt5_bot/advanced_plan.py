"""Advanced conditional order-plan execution.

Supports per-account workflows with:
- entry order
- optional on_fill follow-up order
- optional on_sl follow-up order
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from multiprocessing import Process, Queue
import queue as _queue_mod
import time
from typing import Any

from . import mt5
from .client import MT5Client, TradingBot
from .config import AccountConfig, BotConfig


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_picklable(value: Any) -> Any:
    """Convert nested values to queue-safe primitives for multiprocessing."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[str(k)] = _to_picklable(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_to_picklable(v) for v in value]
    if isinstance(value, set):
        return [_to_picklable(v) for v in sorted(value, key=lambda x: str(x))]
    # MT5 namedtuples/classes or any custom objects end here.
    return str(value)


@dataclass(frozen=True)
class StepPlan:
    action: str
    side: str | None
    volume: float | None
    trigger_price: float | None
    sl_price: float | None
    tp_price: float | None
    comment: str


@dataclass(frozen=True)
class WorkflowPlan:
    account: str
    symbol: str
    entry: StepPlan
    on_fill: StepPlan | None
    on_sl: StepPlan | None
    timeout_seconds: int


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"Invalid number for '{field}': {value}") from exc


def _parse_step(raw: dict[str, Any], field_name: str) -> StepPlan:
    if not isinstance(raw, dict):
        raise ValueError(f"'{field_name}' must be an object")
    action = str(raw.get("action", "open")).strip().lower()
    if action not in ("open", "close"):
        raise ValueError(f"'{field_name}.action' must be open/close")
    side_raw = raw.get("side")
    side = str(side_raw).strip().lower() if side_raw is not None else None
    if side is not None and side not in ("buy", "sell"):
        raise ValueError(f"'{field_name}.side' must be buy/sell")
    volume = (
        _as_float(raw["volume"], f"{field_name}.volume")
        if "volume" in raw and raw.get("volume") is not None
        else None
    )
    if action == "open":
        if side is None:
            raise ValueError(f"'{field_name}.side' is required")
        if volume is None:
            raise ValueError(f"'{field_name}.volume' is required")
    trigger_price = (
        _as_float(raw["trigger_price"], f"{field_name}.trigger_price")
        if raw.get("trigger_price") is not None
        else None
    )
    sl_price = (
        _as_float(raw["sl_price"], f"{field_name}.sl_price")
        if raw.get("sl_price") is not None
        else None
    )
    tp_price = (
        _as_float(raw["tp_price"], f"{field_name}.tp_price")
        if raw.get("tp_price") is not None
        else None
    )
    comment = str(raw.get("comment", field_name)).strip() or field_name
    return StepPlan(
        action=action,
        side=side,
        volume=volume,
        trigger_price=trigger_price,
        sl_price=sl_price,
        tp_price=tp_price,
        comment=comment,
    )


def parse_advanced_order_rows(raw: Any) -> list[WorkflowPlan]:
    if not isinstance(raw, list):
        raise ValueError("Advanced order plan must be a JSON array")
    plans: list[WorkflowPlan] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Workflow row #{idx} must be an object")
        account = str(item.get("account", "")).strip()
        symbol = str(item.get("symbol", "")).strip()
        if not account:
            raise ValueError(f"Workflow row #{idx} missing account")
        if not symbol:
            raise ValueError(f"Workflow row #{idx} missing symbol")
        # Supports two JSON styles:
        # 1) nested: { entry: {...}, on_fill: {...}, on_sl: {...} }
        # 2) flat:   { side, volume, trigger_price?, sl_price?, tp_price?, comment? }
        if "entry" in item:
            entry = _parse_step(item["entry"], f"row#{idx}.entry")
        else:
            entry = _parse_step(item, f"row#{idx}")
        on_fill = (
            _parse_step(item["on_fill"], f"row#{idx}.on_fill")
            if item.get("on_fill") is not None
            else None
        )
        on_sl = (
            _parse_step(item["on_sl"], f"row#{idx}.on_sl")
            if item.get("on_sl") is not None
            else None
        )
        if entry.action == "close" and (on_fill is not None or on_sl is not None):
            raise ValueError(
                f"Workflow row #{idx}: close action cannot use on_fill/on_sl"
            )
        timeout_seconds = int(item.get("timeout_seconds", 3600))
        if timeout_seconds <= 0:
            raise ValueError(f"Workflow row #{idx} timeout_seconds must be > 0")
        plans.append(
            WorkflowPlan(
                account=account,
                symbol=symbol,
                entry=entry,
                on_fill=on_fill,
                on_sl=on_sl,
                timeout_seconds=timeout_seconds,
            )
        )
    if not plans:
        raise ValueError("Advanced order plan is empty")
    return plans


def load_advanced_order_plan(path: str) -> list[WorkflowPlan]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.loads(f.read())
    return parse_advanced_order_rows(raw)


def _position_comment(position: Any) -> str:
    return str(getattr(position, "comment", "") or "")


def _find_position_by_comment(bot: TradingBot, symbol: str, safe_comment: str) -> Any | None:
    positions = bot.client.positions(symbol=symbol)
    for pos in positions:
        if _position_comment(pos) == safe_comment:
            return pos
    return None


def _infer_pending_order_type(bot: TradingBot, symbol: str, side: str, trigger_price: float) -> str:
    ask = bot.client.current_price(symbol, "buy")
    bid = bot.client.current_price(symbol, "sell")
    if side == "buy":
        return "buy_stop" if trigger_price >= ask else "buy_limit"
    return "sell_stop" if trigger_price <= bid else "sell_limit"


def _place_step(bot: TradingBot, symbol: str, step: StepPlan, comment_prefix: str) -> dict[str, Any]:
    safe_comment = MT5Client._safe_comment(f"{comment_prefix}:{step.comment}:{int(time.time())}")
    if step.action == "close":
        positions = bot.client.positions(symbol=symbol)
        if step.side == "buy":
            positions = [p for p in positions if int(getattr(p, "type", -1)) == int(mt5.POSITION_TYPE_BUY)]
        elif step.side == "sell":
            positions = [p for p in positions if int(getattr(p, "type", -1)) == int(mt5.POSITION_TYPE_SELL)]
        if not positions:
            raise RuntimeError(
                f"No matching open positions to close for {symbol} "
                f"(side={step.side or 'all'})"
            )
        closed: list[dict[str, Any]] = []
        for pos in positions:
            req_volume = float(step.volume) if step.volume is not None else None
            if req_volume is not None and req_volume > float(getattr(pos, "volume", 0.0)):
                req_volume = float(getattr(pos, "volume", 0.0))
            res = bot.client.close_position(pos, volume=req_volume, comment=safe_comment)
            closed.append(
                {
                    "ticket": int(getattr(pos, "ticket")),
                    "symbol": str(getattr(pos, "symbol", symbol)),
                    "retcode": res.get("retcode"),
                    "deal": res.get("deal"),
                    "order": res.get("order"),
                }
            )
        return {
            "kind": "close",
            "comment": safe_comment,
            "closed": closed,
            "closed_count": len(closed),
        }
    sl = float(step.sl_price) if step.sl_price is not None else 0.0
    tp = float(step.tp_price) if step.tp_price is not None else 0.0
    baseline_snapshot = {
        int(getattr(p, "ticket")): float(getattr(p, "volume", 0.0) or 0.0)
        for p in bot.client.positions(symbol=symbol)
    }
    if step.trigger_price is None:
        result = bot.client.send_market_order(
            symbol=symbol,
            side=step.side,
            volume=step.volume,
            sl=sl,
            tp=tp,
            comment=safe_comment,
        )
        return {
            "kind": "market",
            "result": result,
            "comment": safe_comment,
            "order_ticket": int(result.get("order", 0) or 0),
            "baseline_snapshot": baseline_snapshot,
            "sl": step.sl_price,
            "tp": step.tp_price,
        }

    order_type = _infer_pending_order_type(bot, symbol, step.side, step.trigger_price)
    used_comment = safe_comment
    try:
        if order_type == "buy_limit":
            result = bot.client.send_limit_order(
                symbol=symbol,
                side="buy",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment=safe_comment,
            )
        elif order_type == "sell_limit":
            result = bot.client.send_limit_order(
                symbol=symbol,
                side="sell",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment=safe_comment,
            )
        elif order_type == "buy_stop":
            result = bot.client.send_stop_order(
                symbol=symbol,
                side="buy",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment=safe_comment,
            )
        else:
            result = bot.client.send_stop_order(
                symbol=symbol,
                side="sell",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment=safe_comment,
            )
    except RuntimeError as err:
        msg = str(err).lower()
        if "comment" not in msg:
            raise
        used_comment = ""
        if order_type == "buy_limit":
            result = bot.client.send_limit_order(
                symbol=symbol,
                side="buy",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment="",
            )
        elif order_type == "sell_limit":
            result = bot.client.send_limit_order(
                symbol=symbol,
                side="sell",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment="",
            )
        elif order_type == "buy_stop":
            result = bot.client.send_stop_order(
                symbol=symbol,
                side="buy",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment="",
            )
        else:
            result = bot.client.send_stop_order(
                symbol=symbol,
                side="sell",
                volume=step.volume,
                price=step.trigger_price,
                sl=sl,
                tp=tp,
                comment="",
            )
    return {
        "kind": "pending",
        "order_type": order_type,
        "result": result,
        "comment": used_comment,
        "order_ticket": int(result.get("order", 0) or 0),
        "baseline_snapshot": baseline_snapshot,
        "sl": step.sl_price,
        "tp": step.tp_price,
    }


def _find_new_or_changed_position(
    bot: TradingBot, symbol: str, baseline_snapshot: dict[int, float],
) -> Any | None:
    positions = bot.client.positions(symbol=symbol)
    for pos in positions:
        ticket = int(getattr(pos, "ticket"))
        volume = float(getattr(pos, "volume", 0.0) or 0.0)
        if ticket not in baseline_snapshot:
            return pos
        if abs(volume - baseline_snapshot[ticket]) > 1e-9:
            return pos
    return None


def _wait_for_position_open(
    bot: TradingBot,
    symbol: str,
    safe_comment: str,
    timeout_seconds: int,
    poll_seconds: float,
    baseline_snapshot: dict[int, float] | None = None,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if safe_comment:
            pos = _find_position_by_comment(bot, symbol, safe_comment)
            if pos is not None:
                return pos
        if baseline_snapshot is not None:
            pos = _find_new_or_changed_position(bot, symbol, baseline_snapshot)
            if pos is not None:
                return pos
        time.sleep(max(0.2, poll_seconds))
    raise RuntimeError(f"Timed out waiting for position open (comment={safe_comment})")


def _wait_for_pending_fill(
    bot: TradingBot,
    symbol: str,
    order_ticket: int,
    safe_comment: str,
    timeout_seconds: int,
    poll_seconds: float,
    baseline_snapshot: dict[int, float] | None = None,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if order_ticket > 0 and bot.client.pending_order_exists(order_ticket):
            time.sleep(max(0.2, poll_seconds))
            continue
        if safe_comment:
            pos = _find_position_by_comment(bot, symbol, safe_comment)
            if pos is not None:
                return pos
        if baseline_snapshot is not None:
            pos = _find_new_or_changed_position(bot, symbol, baseline_snapshot)
            if pos is not None:
                return pos
        time.sleep(max(0.2, poll_seconds))
    raise RuntimeError(f"Timed out waiting for pending fill (order={order_ticket})")


def _detect_close_reason(bot: TradingBot, symbol: str, position_ticket: int, sl: float | None, tp: float | None) -> str:
    deals = bot.client.history_deals(position=position_ticket)
    if not deals:
        return "unknown"
    out_entry = int(getattr(mt5, "DEAL_ENTRY_OUT", 1))
    close_deals = [d for d in deals if int(getattr(d, "entry", -1)) == out_entry]
    if not close_deals:
        return "unknown"
    close_deal = close_deals[-1]
    close_price = float(getattr(close_deal, "price", 0.0) or 0.0)
    if sl is None and tp is None:
        return "closed"
    tol = max(bot.client.pip_size(symbol) * 3.0, 1e-7)
    if sl is not None and abs(close_price - float(sl)) <= tol:
        return "sl"
    if tp is not None and abs(close_price - float(tp)) <= tol:
        return "tp"
    return "closed"


def _wait_for_position_close(
    bot: TradingBot,
    symbol: str,
    position_ticket: int,
    sl: float | None,
    tp: float | None,
    timeout_seconds: int,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if bot.client.positions(ticket=position_ticket):
            time.sleep(max(0.2, poll_seconds))
            continue
        return _detect_close_reason(bot, symbol, position_ticket, sl, tp)
    raise RuntimeError(f"Timed out waiting for position close (ticket={position_ticket})")


def _execute_workflow(
    cfg: BotConfig,
    account: AccountConfig,
    wf: WorkflowPlan,
    poll_seconds: float,
    default_timeout_seconds: int,
) -> dict[str, Any]:
    bot = TradingBot(
        BotConfig(
            mt5_login=account.mt5_login,
            mt5_password=account.mt5_password,
            mt5_server=account.mt5_server,
            mt5_path=account.mt5_path or cfg.mt5_path,
            mt5_portable=account.mt5_portable,
            default_symbol=cfg.default_symbol,
            risk_per_trade=cfg.risk_per_trade,
            max_daily_loss_pct=cfg.max_daily_loss_pct,
            max_open_trades=cfg.max_open_trades,
            sl_pips=cfg.sl_pips,
            tp_pips=cfg.tp_pips,
            deviation=cfg.deviation,
            magic_number=cfg.magic_number,
            timeframe=cfg.timeframe,
            fast_ma=cfg.fast_ma,
            slow_ma=cfg.slow_ma,
            poll_interval_seconds=cfg.poll_interval_seconds,
            cooldown_seconds=cfg.cooldown_seconds,
            max_spread_pips=cfg.max_spread_pips,
            enable_session_filter=cfg.enable_session_filter,
            session_start_utc=cfg.session_start_utc,
            session_end_utc=cfg.session_end_utc,
            journal_path=cfg.journal_path,
            max_connect_retries=cfg.max_connect_retries,
            max_symbol_open_trades=cfg.max_symbol_open_trades,
            max_symbol_volume=cfg.max_symbol_volume,
            enable_break_even=cfg.enable_break_even,
            break_even_trigger_pips=cfg.break_even_trigger_pips,
            break_even_offset_pips=cfg.break_even_offset_pips,
            enable_trailing_stop=cfg.enable_trailing_stop,
            trailing_start_pips=cfg.trailing_start_pips,
            trailing_distance_pips=cfg.trailing_distance_pips,
            enable_partial_tp=cfg.enable_partial_tp,
            partial_tp_trigger_pips=cfg.partial_tp_trigger_pips,
            partial_tp_close_pct=cfg.partial_tp_close_pct,
            accounts_file=cfg.accounts_file,
            dispatch_journal_path=cfg.dispatch_journal_path,
            sync_send_delay_ms=cfg.sync_send_delay_ms,
            strategy_name=cfg.strategy_name,
            strategy_class_path=cfg.strategy_class_path,
        )
    )
    started_at = _now_utc_iso()
    timeout_seconds = wf.timeout_seconds if wf.timeout_seconds > 0 else default_timeout_seconds
    steps: list[dict[str, Any]] = []
    try:
        bot.start()
        bot.client.ensure_symbol(wf.symbol)

        entry_data = _place_step(bot, wf.symbol, wf.entry, f"{wf.account}:entry")
        steps.append({"event": "entry_placed", "data": entry_data})

        # For a standalone entry workflow, place-and-exit immediately.
        # This avoids long waits when user only wants order submission.
        if wf.on_fill is None and wf.on_sl is None:
            return {
                "ok": True,
                "name": account.name,
                "login": account.mt5_login,
                "symbol": wf.symbol,
                "started_at_utc": started_at,
                "finished_at_utc": _now_utc_iso(),
                "steps": steps,
            }

        if entry_data["kind"] == "pending":
            entry_pos = _wait_for_pending_fill(
                bot=bot,
                symbol=wf.symbol,
                order_ticket=entry_data["order_ticket"],
                safe_comment=entry_data["comment"],
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                baseline_snapshot=(
                    {
                        int(k): float(v)
                        for k, v in dict(entry_data["baseline_snapshot"]).items()
                    }
                    if "baseline_snapshot" in entry_data
                    else None
                ),
            )
        else:
            entry_pos = _wait_for_position_open(
                bot=bot,
                symbol=wf.symbol,
                safe_comment=entry_data["comment"],
                timeout_seconds=min(timeout_seconds, 30),
                poll_seconds=poll_seconds,
                baseline_snapshot=(
                    {
                        int(k): float(v)
                        for k, v in dict(entry_data["baseline_snapshot"]).items()
                    }
                    if "baseline_snapshot" in entry_data
                    else None
                ),
            )
        position_ticket = int(getattr(entry_pos, "ticket"))
        steps.append({"event": "entry_filled", "position_ticket": position_ticket})

        if wf.on_fill is not None:
            fill_data = _place_step(bot, wf.symbol, wf.on_fill, f"{wf.account}:on_fill")
            steps.append({"event": "on_fill_placed", "data": fill_data})

        if wf.on_sl is not None:
            close_reason = _wait_for_position_close(
                bot=bot,
                symbol=wf.symbol,
                position_ticket=position_ticket,
                sl=entry_data.get("sl"),
                tp=entry_data.get("tp"),
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            steps.append({"event": "entry_closed", "reason": close_reason})
            if close_reason == "sl":
                sl_data = _place_step(bot, wf.symbol, wf.on_sl, f"{wf.account}:on_sl")
                steps.append({"event": "on_sl_placed", "data": sl_data})

        return {
            "ok": True,
            "name": account.name,
            "login": account.mt5_login,
            "symbol": wf.symbol,
            "started_at_utc": started_at,
            "finished_at_utc": _now_utc_iso(),
            "steps": steps,
        }
    except Exception as err:
        return {
            "ok": False,
            "name": account.name,
            "login": account.mt5_login,
            "symbol": wf.symbol,
            "started_at_utc": started_at,
            "finished_at_utc": _now_utc_iso(),
            "steps": steps,
            "error": str(err),
        }
    finally:
        try:
            bot.stop()
        except Exception:
            pass


def _account_workflows_worker(
    cfg: BotConfig,
    account: AccountConfig,
    indexed_workflows: list[tuple[int, WorkflowPlan]],
    timeout_seconds: int,
    poll_seconds: float,
    queue: Queue,
) -> None:
    for row_index, wf in indexed_workflows:
        result = _execute_workflow(
            cfg=cfg,
            account=account,
            wf=wf,
            poll_seconds=poll_seconds,
            default_timeout_seconds=timeout_seconds,
        )
        result["row_index"] = row_index
        queue.put(_to_picklable(result))


def execute_advanced_order_plan(
    cfg: BotConfig,
    accounts: list[AccountConfig],
    workflows: list[WorkflowPlan],
    timeout_seconds: int = 3600,
    poll_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    account_lookup = {a.name: a for a in accounts}
    indexed_workflows = list(enumerate(workflows))
    grouped: dict[str, list[tuple[int, WorkflowPlan]]] = {}
    results: list[dict[str, Any]] = []

    for row_index, wf in indexed_workflows:
        if wf.account not in account_lookup:
            results.append(
                {
                    "ok": False,
                    "name": wf.account,
                    "login": None,
                    "symbol": wf.symbol,
                    "started_at_utc": _now_utc_iso(),
                    "finished_at_utc": _now_utc_iso(),
                    "steps": [],
                    "row_index": row_index,
                    "error": f"Account not found in accounts file: {wf.account}",
                }
            )
            continue
        grouped.setdefault(wf.account, []).append((row_index, wf))

    result_queue: Queue = Queue()
    processes: list[Process] = []
    expected = 0
    for account_name, account_workflows in grouped.items():
        account = account_lookup[account_name]
        p = Process(
            target=_account_workflows_worker,
            args=(
                cfg,
                account,
                account_workflows,
                timeout_seconds,
                poll_seconds,
                result_queue,
            ),
        )
        p.daemon = True
        p.start()
        processes.append(p)
        expected += len(account_workflows)

    per_item_timeout = max(timeout_seconds + 60, 120)
    collected = 0
    while collected < expected:
        try:
            item = result_queue.get(timeout=per_item_timeout)
            results.append(item)
            collected += 1
        except _queue_mod.Empty:
            break

    for proc in processes:
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)

    if collected < expected:
        for row_index, wf in indexed_workflows:
            found = any(r.get("row_index") == row_index for r in results)
            if not found:
                results.append(
                    {
                        "ok": False,
                        "name": wf.account,
                        "login": account_lookup.get(wf.account, None)
                        and account_lookup[wf.account].mt5_login,
                        "symbol": wf.symbol,
                        "started_at_utc": _now_utc_iso(),
                        "finished_at_utc": _now_utc_iso(),
                        "steps": [],
                        "row_index": row_index,
                        "error": "Worker process timed out or crashed",
                    }
                )

    results.sort(key=lambda x: int(x.get("row_index", 0)))
    for item in results:
        item.pop("row_index", None)
    return results
