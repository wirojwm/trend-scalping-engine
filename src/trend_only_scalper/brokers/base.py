"""Abstract broker interface.

Strategy and orchestration code (bot loop, position manager, risk manager) depends only
on this interface -- never on MT5, ccxt/python-binance, or any other broker SDK directly.
Each concrete adapter (MockBroker, MT5Broker, BinanceBroker) implements this contract
independently, so swapping backends never touches strategy code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from trend_only_scalper.models import CloseReason, Position, Side


class Broker(ABC):
    def connect(self) -> None:
        """Establish any connection required before use. Default no-op -- adapters that
        don't need one (MockBroker) can skip overriding this.
        """
        return None

    def disconnect(self) -> None:
        """Release any connection/resources. Default no-op."""
        return None

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Return the most recent `limit` OHLCV bars with columns:
        time, open, high, low, close, volume.
        """

    @abstractmethod
    def get_open_position(self, symbol: str, strategy_id: str) -> Position | None:
        """Return this strategy's open position for `symbol`, or None if there isn't one."""

    @abstractmethod
    def open_market_order(
        self, symbol: str, side: Side, quantity: float, stop_loss: float
    ) -> Position:
        """Open a market order with a stop-loss attached immediately. Must raise if a
        position is already open -- brokers never allow a second concurrent position.
        """

    @abstractmethod
    def close_position(self, position_id: str, reason: CloseReason) -> None:
        """Close the given position at market and record the trade."""

    @abstractmethod
    def modify_stop_loss(self, position_id: str, new_stop_loss: float) -> None:
        """Move the stop-loss for an open position."""

    @abstractmethod
    def get_unrealized_pnl(self, position: Position) -> float:
        """Cash unrealized P&L for `position` at the current market price."""

    @abstractmethod
    def get_trading_cost(self, symbol: str) -> float:
        """Estimated round-trip spread/commission cost for `symbol`, in cash or price units
        consistent with how the strategy's cost checks are configured.
        """

    @abstractmethod
    def get_account_equity(self) -> float:
        """Current account equity (balance + unrealized P&L on any open position)."""

    @abstractmethod
    def get_today_realized_pnl(self) -> float:
        """Sum of realized P&L for trades closed so far in the current trading day."""

    @abstractmethod
    def get_position_count(self, symbol: str, strategy_id: str) -> int:
        """Number of open positions this strategy currently holds for `symbol` (0 or 1)."""
