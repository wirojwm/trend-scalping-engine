"""SimulatedBroker: a Broker implementation driving the backtest replay.

Executes new entries at the NEXT M1 bar's open (execution_mode: next_open), applies a
configurable spread + slippage to every fill, and autonomously simulates a hard
stop-loss hit by checking the current bar's low/high against the open position's stop --
exactly like a real broker's server-side stop order would, without our strategy logic
ever deciding it. Only one position at a time, matching every other adapter.

Known simplifications (documented, not hidden):
- TP/breakeven checks use the current bar's CLOSE as "the price" (bar-resolution, not
  tick-level) -- this is what "approximately" means for cash TP in the phase spec.
- A hard stop-loss fill is assumed to happen exactly at the stop price. SL is checked
  before TP/BE each bar, so on a single bar that could have hit either, SL wins -- a
  conservative simplification, not a realistic intrabar order-of-events simulation.
- get_bars() ignores the `symbol` argument entirely; the loaded CSV already represents
  one specific instrument, so there is nothing to filter by.
"""

from __future__ import annotations

import pandas as pd

from trend_only_scalper.brokers.base import Broker
from trend_only_scalper.models import ClosedTrade, CloseReason, Position, Side

_TIMEFRAME_DELTA = {
    "M1": pd.Timedelta(minutes=1),
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
}


class SimulatedBroker(Broker):
    def __init__(self, config, strategy_id: str = "trend_only_scalper") -> None:
        self.config = config
        self.strategy_id = strategy_id
        self.symbol = config.symbol
        self.starting_equity = config.initial_equity
        self.spread_price = config.spread_points_or_price
        self.fee_rate = config.fee_rate
        self.slippage_price = config.slippage_points_or_price

        self._m1: pd.DataFrame | None = None
        self._m5: pd.DataFrame | None = None
        self._m15: pd.DataFrame | None = None
        self._current_index = -1
        self._current_time: pd.Timestamp | None = None

        self._position: Position | None = None
        self._position_counter = 0
        self._orders: list[dict] = []
        self._closed_trades: list[ClosedTrade] = []

    # --- data feed / replay clock --------------------------------------

    def load_data(self, m1: pd.DataFrame, m5: pd.DataFrame, m15: pd.DataFrame) -> None:
        self._m1 = m1.reset_index(drop=True)
        self._m5 = m5.reset_index(drop=True)
        self._m15 = m15.reset_index(drop=True)

    def set_current_bar(self, index: int) -> None:
        """Advance the simulated 'now' to M1 row `index`. Call once per replay step,
        before check_and_apply_stop_loss() and run_iteration().
        """
        self._current_index = index
        self._current_time = self._m1.iloc[index]["time"]

    def _current_bar(self) -> pd.Series:
        return self._m1.iloc[self._current_index]

    def _next_open_price(self) -> float:
        next_index = self._current_index + 1
        if next_index < len(self._m1):
            return float(self._m1.iloc[next_index]["open"])
        return float(self._current_bar()["close"])  # no next bar left; fall back to current close

    def _fill_price(self, transaction_side: Side, base_price: float) -> float:
        half_spread = self.spread_price / 2.0
        if transaction_side is Side.BUY:
            return base_price + half_spread + self.slippage_price
        return base_price - half_spread - self.slippage_price

    def _pnl(self, position: Position, exit_price: float) -> float:
        move = exit_price - position.entry_price
        return (move if position.side is Side.BUY else -move) * position.quantity

    # --- test/inspection helpers ------------------------------------------

    def get_order_log(self) -> list[dict]:
        return list(self._orders)

    def get_trade_history(self) -> list[ClosedTrade]:
        return list(self._closed_trades)

    # --- Broker interface --------------------------------------------------

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        if timeframe == "M1":
            df = self._m1.iloc[: self._current_index + 1]
        else:
            source = self._m5 if timeframe == "M5" else self._m15
            delta = _TIMEFRAME_DELTA[timeframe]
            df = source[source["time"] + delta <= self._current_time]
        return df.tail(limit).reset_index(drop=True)

    def get_open_position(self, symbol: str, strategy_id: str) -> Position | None:
        return self._position if (self._position and self._position.symbol == symbol) else None

    def get_position_count(self, symbol: str, strategy_id: str) -> int:
        return 1 if self.get_open_position(symbol, strategy_id) is not None else 0

    def open_market_order(self, symbol: str, side: Side, quantity: float, stop_loss: float) -> Position:
        if self._position is not None:
            raise RuntimeError("SimulatedBroker: a position is already open; one-position-only rule violated")

        entry_price = self._fill_price(side, self._next_open_price())
        self._position_counter += 1
        position = Position(
            position_id=f"sim-{self._position_counter}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            opened_at=self._current_time.to_pydatetime(),
            strategy_id=self.strategy_id,
        )
        self._position = position
        self._orders.append(
            {
                "type": "OPEN", "position_id": position.position_id, "side": side,
                "quantity": quantity, "entry_price": entry_price, "stop_loss": stop_loss,
            }
        )
        return position

    def close_position(self, position_id: str, reason: CloseReason) -> None:
        position = self._position
        if position is None or position.position_id != position_id:
            raise ValueError(f"SimulatedBroker: no open position with id {position_id!r}")

        exit_price = self._fill_price(position.side.opposite, float(self._current_bar()["close"]))
        realized_pnl = self._pnl(position, exit_price)
        self._closed_trades.append(
            ClosedTrade(
                position_id=position.position_id, symbol=position.symbol, side=position.side,
                quantity=position.quantity, entry_price=position.entry_price, exit_price=exit_price,
                opened_at=position.opened_at, closed_at=self._current_time.to_pydatetime(),
                realized_pnl_cash=realized_pnl, reason=reason,
            )
        )
        self._orders.append(
            {"type": "CLOSE", "position_id": position_id, "reason": reason,
             "exit_price": exit_price, "realized_pnl_cash": realized_pnl}
        )
        self._position = None

    def modify_stop_loss(self, position_id: str, new_stop_loss: float) -> None:
        if self._position is None or self._position.position_id != position_id:
            raise ValueError(f"SimulatedBroker: no open position with id {position_id!r}")
        self._position.stop_loss = new_stop_loss
        self._orders.append({"type": "MODIFY_SL", "position_id": position_id, "new_stop_loss": new_stop_loss})

    def get_unrealized_pnl(self, position: Position) -> float:
        current_close = float(self._current_bar()["close"])
        return self._pnl(position, current_close)

    def get_trading_cost(self, symbol: str) -> float:
        current_close = float(self._current_bar()["close"])
        return self.spread_price + 2 * self.fee_rate * current_close

    def get_account_equity(self) -> float:
        unrealized = self.get_unrealized_pnl(self._position) if self._position else 0.0
        return self.starting_equity + self.get_today_realized_pnl() + unrealized

    def get_today_realized_pnl(self) -> float:
        current_date = self._current_time.date()
        return sum(trade.realized_pnl_cash for trade in self._closed_trades if trade.closed_at.date() == current_date)

    # --- backtest-only: autonomous stop-loss simulation ---------------------

    def check_and_apply_stop_loss(self) -> ClosedTrade | None:
        """Check whether the current bar's range breached the open position's stop-loss;
        if so, close it (as a real broker's stop order would) and return the ClosedTrade.

        Must be called once per bar, BEFORE run_iteration(), so that any resulting
        cooldown/daily-guard bookkeeping (done by the replay driver, since manage_position
        never decides hard-SL closes) takes effect before this same bar's entry-scan.
        """
        position = self._position
        if position is None:
            return None

        bar = self._current_bar()
        hit = (
            (position.side is Side.BUY and bar["low"] <= position.stop_loss)
            or (position.side is Side.SELL and bar["high"] >= position.stop_loss)
        )
        if not hit:
            return None

        exit_price = position.stop_loss
        realized_pnl = self._pnl(position, exit_price)
        trade = ClosedTrade(
            position_id=position.position_id, symbol=position.symbol, side=position.side,
            quantity=position.quantity, entry_price=position.entry_price, exit_price=exit_price,
            opened_at=position.opened_at, closed_at=self._current_time.to_pydatetime(),
            realized_pnl_cash=realized_pnl, reason=CloseReason.HARD_SL,
        )
        self._closed_trades.append(trade)
        self._orders.append(
            {"type": "CLOSE", "position_id": position.position_id, "reason": CloseReason.HARD_SL,
             "exit_price": exit_price, "realized_pnl_cash": realized_pnl}
        )
        self._position = None
        return trade
