"""Unit tests for position_manager: cash TP, cash breakeven lock, one-position-only gate."""

from datetime import datetime

import pytest

from trend_only_scalper.models import CloseReason, Position, PositionAction, Side
from trend_only_scalper.risk.cooldown import start_cooldown
from trend_only_scalper.strategy.position_manager import manage_position, should_scan_for_entry


def make_position(side: Side, entry_price: float, stop_loss: float, quantity: float = 1.0) -> Position:
    return Position(
        position_id="pos-1",
        symbol="EURUSD",
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        opened_at=datetime(2026, 1, 1),
    )


# --- Cash take-profit ------------------------------------------------------


def test_manage_position_closes_when_pnl_at_or_above_tp():
    position = make_position(Side.BUY, entry_price=1.1000, stop_loss=1.0950)
    result = manage_position(
        position,
        unrealized_pnl_cash=1.50,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,
    )
    assert result.action is PositionAction.CLOSE
    assert result.close_reason is CloseReason.TP_CASH


# --- Cash breakeven trigger ------------------------------------------------


def test_manage_position_modifies_sl_when_pnl_at_or_above_be_trigger_buy():
    position = make_position(Side.BUY, entry_price=1.1000, stop_loss=1.0950, quantity=1.0)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.70,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,
    )
    assert result.action is PositionAction.MODIFY_SL
    # BUY breakeven stop = entry + lock_distance + cost_buffer; lock_distance = 0.05 / 1.0
    assert result.new_stop_loss == pytest.approx(1.1000 + 0.05)
    assert result.new_stop_loss > position.entry_price


def test_manage_position_does_nothing_below_be_trigger():
    position = make_position(Side.BUY, entry_price=1.1000, stop_loss=1.0950)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.30,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,
    )
    assert result.action is PositionAction.NONE
    assert result.new_stop_loss is None


# --- Breakeven direction correctness ---------------------------------------


def test_buy_breakeven_sl_is_above_entry():
    position = make_position(Side.BUY, entry_price=100.0, stop_loss=99.0, quantity=2.0)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.70,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.10,
    )
    assert result.action is PositionAction.MODIFY_SL
    assert result.new_stop_loss > position.entry_price


def test_sell_breakeven_sl_is_below_entry():
    position = make_position(Side.SELL, entry_price=100.0, stop_loss=101.0, quantity=2.0)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.70,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.10,
    )
    assert result.action is PositionAction.MODIFY_SL
    assert result.new_stop_loss < position.entry_price


# --- Stop-loss never moves backward -----------------------------------


def test_stop_loss_never_moves_backward_for_buy():
    # current stop_loss is already better (higher) than what the BE calc would produce
    position = make_position(Side.BUY, entry_price=100.0, stop_loss=100.5, quantity=1.0)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.70,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,  # would produce candidate 100.05, worse than current 100.5
    )
    assert result.action is PositionAction.NONE
    assert result.new_stop_loss is None


def test_stop_loss_never_moves_backward_for_sell():
    position = make_position(Side.SELL, entry_price=100.0, stop_loss=99.5, quantity=1.0)
    result = manage_position(
        position,
        unrealized_pnl_cash=0.70,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,  # would produce candidate 99.95, worse (higher) than 99.5
    )
    assert result.action is PositionAction.NONE
    assert result.new_stop_loss is None


# --- One-position-only gate --------------------------------------------


def test_should_scan_for_entry_true_when_no_position():
    assert should_scan_for_entry(None) is True


def test_should_scan_for_entry_false_when_position_open():
    position = make_position(Side.BUY, entry_price=100.0, stop_loss=99.0)
    assert should_scan_for_entry(position) is False


# --- Cooldown never blocks position management -----------------------------


def test_active_cooldown_does_not_affect_manage_position_outcome():
    position = make_position(Side.BUY, entry_price=1.1000, stop_loss=1.0950)
    cooldown = start_cooldown(5)
    assert cooldown.active is True  # an active cooldown exists...

    # ...but manage_position has no cooldown parameter at all, so it behaves identically
    # whether or not a cooldown is in effect -- position management is never gated.
    result = manage_position(
        position,
        unrealized_pnl_cash=1.50,
        tp_cash=1.50,
        breakeven_trigger_cash=0.70,
        breakeven_lock_cash=0.05,
    )
    assert result.action is PositionAction.CLOSE
