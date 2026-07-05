"""Manage the single open position: cash TP, cash breakeven lock, one-position-only gate.

While a position is open, the caller must call manage_position() and return immediately --
this module never scans for new entries and never closes a position for any reason other
than the cash TP condition (a hard SL hit is detected and applied by the broker/order layer,
not decided here).
"""

from __future__ import annotations

from trend_only_scalper.models import CloseReason, Position, PositionAction, Side, TradeResult


def should_scan_for_entry(position: Position | None) -> bool:
    """One-position-only gate: never look for a new entry while a position is open."""
    return position is None


def _cash_to_price_distance(cash_amount: float, quantity: float, contract_size: float = 1.0) -> float:
    """Convert a cash amount to a price distance: pnl = qty * contract_size * price_move.

    contract_size defaults to 1.0 for linear instruments (Binance base-asset units,
    Mock/Simulated abstract units); brokers where quantity isn't 1:1 with cash-per-price-unit
    (e.g. MT5 lots) must pass their actual Broker.contract_size(symbol) here.
    """
    if quantity == 0:
        raise ValueError("quantity must be non-zero to convert cash to a price distance")
    if contract_size == 0:
        raise ValueError("contract_size must be non-zero to convert cash to a price distance")
    return cash_amount / (quantity * contract_size)


def _improves_stop_loss(side: Side, current_stop_loss: float, candidate_stop_loss: float) -> bool:
    """A stop only "improves" if it moves in the direction that locks in more profit --
    never backward, regardless of side.
    """
    if side is Side.BUY:
        return candidate_stop_loss > current_stop_loss
    return candidate_stop_loss < current_stop_loss


def manage_position(
    position: Position,
    unrealized_pnl_cash: float,
    tp_cash: float,
    breakeven_trigger_cash: float,
    breakeven_lock_cash: float,
    cost_buffer_price: float = 0.0,
    contract_size: float = 1.0,
) -> TradeResult:
    """Decide this bar's action for the single open position: CLOSE, MODIFY_SL, or NONE.

    contract_size must be the broker's actual Broker.contract_size(position.symbol) --
    1.0 for linear instruments, e.g. an MT5 symbol's trade_contract_size for lot-based ones.
    """
    if unrealized_pnl_cash >= tp_cash:
        return TradeResult(action=PositionAction.CLOSE, close_reason=CloseReason.TP_CASH)

    if unrealized_pnl_cash >= breakeven_trigger_cash:
        lock_distance = _cash_to_price_distance(breakeven_lock_cash, position.quantity, contract_size)
        if position.side is Side.BUY:
            candidate_stop_loss = position.entry_price + lock_distance + cost_buffer_price
        else:
            candidate_stop_loss = position.entry_price - lock_distance - cost_buffer_price

        if _improves_stop_loss(position.side, position.stop_loss, candidate_stop_loss):
            return TradeResult(action=PositionAction.MODIFY_SL, new_stop_loss=candidate_stop_loss)
        return TradeResult(action=PositionAction.NONE)

    return TradeResult(action=PositionAction.NONE)
