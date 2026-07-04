"""Daily risk guard: decides whether new trades may be opened today.

Pure function over the day's running stats -- never touches a broker, never manages an
already-open position. Position management proceeds regardless of guard state; only new
entries are gated.
"""

from __future__ import annotations

from trend_only_scalper.models import DailyStats, GuardState


def evaluate_daily_guard(
    stats: DailyStats,
    daily_profit_target: float,
    daily_max_loss: float,
    max_consecutive_losses: int,
    max_trades_per_day: int,
) -> GuardState:
    """Return a GuardState listing every threshold currently breached, if any."""
    reasons: list[str] = []

    if stats.realized_pnl_cash >= daily_profit_target:
        reasons.append("daily_profit_target_reached")
    if stats.realized_pnl_cash <= daily_max_loss:
        reasons.append("daily_max_loss_reached")
    if stats.consecutive_losses >= max_consecutive_losses:
        reasons.append("max_consecutive_losses_reached")
    if stats.trade_count >= max_trades_per_day:
        reasons.append("max_trades_per_day_reached")

    return GuardState(trading_allowed=not reasons, blocked_reasons=reasons)
