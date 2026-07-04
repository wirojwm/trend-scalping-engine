"""Basic performance metrics computed from trade journal rows, plus a safety report.

Operates on plain dicts (as returned by journal.read_journal_rows), so it has no
dependency on any specific broker or the live loop -- metrics can be recomputed any time
from the CSV alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.models import CloseReason


@dataclass(frozen=True)
class Metrics:
    total_trades: int
    wins: int
    losses: int
    breakeven_count: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    average_win: float
    average_loss: float
    profit_factor: float
    max_consecutive_losses: int
    average_trade_pnl: float
    trades_per_day: float


_EMPTY_METRICS = Metrics(
    total_trades=0,
    wins=0,
    losses=0,
    breakeven_count=0,
    win_rate=0.0,
    gross_profit=0.0,
    gross_loss=0.0,
    net_pnl=0.0,
    average_win=0.0,
    average_loss=0.0,
    profit_factor=0.0,
    max_consecutive_losses=0,
    average_trade_pnl=0.0,
    trades_per_day=0.0,
)


def _max_consecutive_losses(pnls: list[float]) -> int:
    max_streak = streak = 0
    for pnl in pnls:
        streak = streak + 1 if pnl < 0 else 0
        max_streak = max(max_streak, streak)
    return max_streak


def _trades_per_day(rows: list[dict]) -> float:
    trading_days = {datetime.fromisoformat(row["timestamp"]).date() for row in rows}
    return len(rows) / len(trading_days) if trading_days else 0.0


def calculate_metrics(rows: list[dict]) -> Metrics:
    """Compute summary performance metrics from trade journal rows.

    A trade is classified as a "breakeven" close if reason_close is BREAKEVEN_SL,
    regardless of its exact P&L; otherwise it's a win (P&L > 0) or a loss (P&L <= 0).
    win_rate is wins / total_trades (breakeven and loss trades both count against it).
    profit_factor is gross_profit / gross_loss, defined as +inf if there's profit and no
    losses, or 0.0 if there's neither -- never a ZeroDivisionError.
    """
    if not rows:
        return _EMPTY_METRICS

    pnls = [float(row["realized_pnl"]) for row in rows]
    reasons = [row["reason_close"] for row in rows]
    total_trades = len(rows)

    breakeven_count = sum(1 for reason in reasons if reason == CloseReason.BREAKEVEN_SL.value)
    wins = sum(
        1 for pnl, reason in zip(pnls, reasons) if reason != CloseReason.BREAKEVEN_SL.value and pnl > 0
    )
    losses = total_trades - wins - breakeven_count

    win_pnls = [pnl for pnl in pnls if pnl > 0]
    loss_pnls = [pnl for pnl in pnls if pnl < 0]

    gross_profit = sum(win_pnls)
    gross_loss = -sum(loss_pnls)  # positive magnitude
    net_pnl = sum(pnls)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    return Metrics(
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        breakeven_count=breakeven_count,
        win_rate=wins / total_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        average_win=(gross_profit / len(win_pnls)) if win_pnls else 0.0,
        average_loss=(sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0.0,
        profit_factor=profit_factor,
        max_consecutive_losses=_max_consecutive_losses(pnls),
        average_trade_pnl=net_pnl / total_trades,
        trades_per_day=_trades_per_day(rows),
    )


def build_safety_report(cfg: StrategyConfig, backend: str) -> str:
    """A human-readable summary of the anti-pattern guards and risk limits in effect."""
    lines = [
        "=== Safety Report ===",
        f"one_position_only:      {cfg.one_position_only}",
        f"counter_trend_disabled:  {not cfg.allow_counter_trend}",
        f"grid_disabled:           {not cfg.allow_grid}",
        f"martingale_disabled:     {not cfg.allow_martingale}",
        f"averaging_down_disabled: {not cfg.allow_averaging_down}",
        f"dry_run:                 {cfg.dry_run}",
        f"daily_max_loss:          {cfg.daily_max_loss}",
        f"max_consecutive_losses:  {cfg.max_consecutive_losses}",
        f"max_trades_per_day:      {cfg.max_trades_per_day}",
        f"broker_backend:          {backend}",
        "======================",
    ]
    return "\n".join(lines)


def print_safety_report(cfg: StrategyConfig, backend: str) -> None:
    print(build_safety_report(cfg, backend))
