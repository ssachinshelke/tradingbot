"""CSV journals for trade execution and multi-account dispatch logging."""

from __future__ import annotations

import csv
from pathlib import Path


class TradeJournal:
    HEADER = [
        "timestamp_utc", "symbol", "side", "volume",
        "sl", "tp", "order", "deal", "retcode", "comment", "reason",
    ]

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(self.HEADER)

    def append(self, row: dict) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.HEADER).writerow(row)


class DispatchJournal:
    HEADER = [
        "dispatch_id", "account_name", "account_login", "symbol", "side",
        "volume", "placed_at_utc", "ack_at_utc", "latency_ms",
        "order_id", "deal_id", "retcode", "mode", "status", "error",
    ]

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(self.HEADER)

    def append(self, row: dict) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.HEADER).writerow(row)
