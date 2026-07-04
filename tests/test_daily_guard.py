"""Unit tests for the daily risk guard and the bar-counter cooldown."""

from trend_only_scalper.models import CloseReason, DailyStats
from trend_only_scalper.risk.cooldown import cooldown_bars_for_reason, is_active, start_cooldown, tick
from trend_only_scalper.risk.daily_guard import evaluate_daily_guard

DEFAULT_LIMITS = dict(
    daily_profit_target=200.0,
    daily_max_loss=-30.0,
    max_consecutive_losses=3,
    max_trades_per_day=150,
)


def make_stats(realized_pnl_cash=0.0, trade_count=0, consecutive_losses=0) -> DailyStats:
    return DailyStats(
        trading_day="2026-01-01",
        realized_pnl_cash=realized_pnl_cash,
        trade_count=trade_count,
        consecutive_losses=consecutive_losses,
    )


# --- Daily guard ---------------------------------------------------------


def test_daily_guard_allows_trading_under_all_thresholds():
    stats = make_stats(realized_pnl_cash=10.0, trade_count=5, consecutive_losses=1)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is True
    assert guard.blocked_reasons == []


def test_daily_guard_blocks_when_profit_target_reached():
    stats = make_stats(realized_pnl_cash=200.0)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is False
    assert "daily_profit_target_reached" in guard.blocked_reasons


def test_daily_guard_blocks_when_max_loss_reached():
    stats = make_stats(realized_pnl_cash=-30.0)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is False
    assert "daily_max_loss_reached" in guard.blocked_reasons


def test_daily_guard_blocks_when_max_consecutive_losses_reached():
    stats = make_stats(consecutive_losses=3)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is False
    assert "max_consecutive_losses_reached" in guard.blocked_reasons


def test_daily_guard_blocks_when_max_trades_per_day_reached():
    stats = make_stats(trade_count=150)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is False
    assert "max_trades_per_day_reached" in guard.blocked_reasons


def test_daily_guard_reports_multiple_simultaneous_breaches():
    stats = make_stats(realized_pnl_cash=-30.0, trade_count=150, consecutive_losses=3)
    guard = evaluate_daily_guard(stats, **DEFAULT_LIMITS)
    assert guard.trading_allowed is False
    assert len(guard.blocked_reasons) == 3


# --- Cooldown --------------------------------------------------------------


def test_cooldown_bars_for_reason_mapping():
    assert cooldown_bars_for_reason(CloseReason.TP_CASH, 1, 2, 5) == 1
    assert cooldown_bars_for_reason(CloseReason.BREAKEVEN_SL, 1, 2, 5) == 2
    assert cooldown_bars_for_reason(CloseReason.HARD_SL, 1, 2, 5) == 5
    assert cooldown_bars_for_reason(CloseReason.MANUAL, 1, 2, 5) == 0


def test_cooldown_blocks_new_entries_until_it_elapses():
    state = start_cooldown(2)
    assert is_active(state) is True

    state = tick(state)
    assert is_active(state) is True  # 1 bar remaining

    state = tick(state)
    assert is_active(state) is False  # 0 bars remaining


def test_cooldown_does_not_go_negative():
    state = start_cooldown(1)
    state = tick(state)
    state = tick(state)  # tick again after already reaching 0
    assert state.bars_remaining == 0
    assert is_active(state) is False


def test_start_cooldown_with_zero_bars_is_inactive_immediately():
    state = start_cooldown(0)
    assert is_active(state) is False
