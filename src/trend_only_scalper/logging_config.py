"""Console + rotating file logging setup, shared by all entry points."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Path | str = "logs",
    log_file: str = "trend_only_scalper.log",
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure the root logger with a console handler and a rotating file handler.

    Safe to call multiple times (e.g. in tests) -- clears existing handlers first.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_dir / log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return root


decision_logger = logging.getLogger("trend_only_scalper.decision")


@dataclass(frozen=True)
class DecisionLogEntry:
    """One structured record of what the bot loop decided on a single iteration."""

    symbol: str
    has_open_position: bool
    daily_guard_status: str
    cooldown_status: str
    trading_cost_status: str
    m15_trend: str
    m5_confirmation: str
    m1_signal: str
    action_taken: str
    no_trade_reason: str = ""


def log_decision(entry: DecisionLogEntry) -> None:
    """Emit exactly one structured log line per bot loop iteration (key=value, easy to grep)."""
    decision_logger.info(
        "symbol=%s has_open_position=%s daily_guard=%s cooldown=%s trading_cost=%s "
        "m15_trend=%s m5_confirmation=%s m1_signal=%s action=%s no_trade_reason=%s",
        entry.symbol,
        entry.has_open_position,
        entry.daily_guard_status,
        entry.cooldown_status,
        entry.trading_cost_status,
        entry.m15_trend,
        entry.m5_confirmation,
        entry.m1_signal,
        entry.action_taken,
        entry.no_trade_reason,
    )
