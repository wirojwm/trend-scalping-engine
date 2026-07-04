"""CSV trade journal: one row per closed trade, appended, never overwritten.

Broker-agnostic: callers pass in plain values (broker name as a string, not a Broker
instance), so this module has no dependency on any specific adapter.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class JournalRow:
    """One completed trade, with the decision context that led to it."""

    timestamp: datetime
    strategy_id: str
    broker: str
    symbol: str
    timeframe_entry: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    stop_loss_initial: float
    stop_loss_final: float
    realized_pnl: float
    fees_or_cost: float
    reason_open: str
    reason_close: str
    m15_trend: str
    m5_confirmation: str
    m1_signal: str
    tp_cash: float
    breakeven_trigger_cash: float
    daily_pnl_after_trade: float
    consecutive_losses_after_trade: int
    trades_today: int
    dry_run: bool


JOURNAL_FIELDNAMES = [f.name for f in fields(JournalRow)]


def _row_to_csv_dict(row: JournalRow) -> dict:
    data = asdict(row)
    data["timestamp"] = row.timestamp.isoformat()
    return data


def write_journal_row(path: str | Path, row: JournalRow) -> None:
    """Append one row to the CSV journal at `path`, writing a header only if the file
    doesn't already exist (or is empty) -- never overwrites existing rows.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(_row_to_csv_dict(row))


def read_journal_rows(path: str | Path) -> list[dict]:
    """Read all journal rows back as plain dicts of strings (values are not type-converted;
    callers such as metrics.py convert the fields they need).
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
