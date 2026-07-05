"""Phase 1 smoke tests for core dataclasses/enums."""

from datetime import datetime

from trend_only_scalper.models import Bar, DailyStats, Position, Side, load_daily_stats, save_daily_stats


def test_side_opposite():
    assert Side.BUY.opposite is Side.SELL
    assert Side.SELL.opposite is Side.BUY


def test_bar_bullish_bearish_and_range():
    bullish = Bar(timestamp=datetime(2026, 1, 1), open=1.0, high=1.5, low=0.9, close=1.4, volume=100)
    bearish = Bar(timestamp=datetime(2026, 1, 1), open=1.4, high=1.5, low=0.9, close=1.0, volume=100)

    assert bullish.is_bullish is True
    assert bullish.is_bearish is False
    assert bearish.is_bearish is True
    assert bullish.range == 0.6


def test_position_defaults():
    position = Position(
        position_id="1",
        symbol="EURUSD",
        side=Side.BUY,
        quantity=0.1,
        entry_price=1.1000,
        stop_loss=1.0950,
        opened_at=datetime(2026, 1, 1),
    )
    assert position.breakeven_applied is False
    assert position.magic_number == 0


# --- DailyStats persistence across restarts --------------------------------


def test_load_daily_stats_returns_fresh_when_no_file_exists(tmp_path):
    path = tmp_path / "daily_stats.json"
    stats = load_daily_stats(path, trading_day="2026-01-01")
    assert stats.trading_day == "2026-01-01"
    assert stats.trade_count == 0
    assert stats.consecutive_losses == 0


def test_save_then_load_daily_stats_round_trips_same_day(tmp_path):
    path = tmp_path / "daily_stats.json"
    original = DailyStats(
        trading_day="2026-01-01", realized_pnl_cash=-12.5, trade_count=4, consecutive_losses=2
    )
    save_daily_stats(path, original)

    restored = load_daily_stats(path, trading_day="2026-01-01")
    assert restored.realized_pnl_cash == -12.5
    assert restored.trade_count == 4
    assert restored.consecutive_losses == 2


def test_load_daily_stats_starts_fresh_on_a_new_trading_day(tmp_path):
    # A restart on a new calendar day must not carry over yesterday's counters --
    # the normal calendar-day reset always takes priority over persisted state.
    path = tmp_path / "daily_stats.json"
    save_daily_stats(
        path, DailyStats(trading_day="2026-01-01", trade_count=5, consecutive_losses=3)
    )

    restored = load_daily_stats(path, trading_day="2026-01-02")
    assert restored.trading_day == "2026-01-02"
    assert restored.trade_count == 0
    assert restored.consecutive_losses == 0


def test_load_daily_stats_handles_corrupt_file_gracefully(tmp_path):
    path = tmp_path / "daily_stats.json"
    path.write_text("not valid json")

    stats = load_daily_stats(path, trading_day="2026-01-01")
    assert stats.trading_day == "2026-01-01"
    assert stats.trade_count == 0
