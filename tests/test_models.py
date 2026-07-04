"""Phase 1 smoke tests for core dataclasses/enums."""

from datetime import datetime

from trend_only_scalper.models import Bar, Position, Side


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
