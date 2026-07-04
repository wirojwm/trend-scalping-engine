"""In-memory broker for tests and dry-run: no credentials, no network, no MT5/Binance import.

Bars are injected via `set_bars()`; the broker simulates a single position, cash P&L from
the latest injected M1 close, a configurable flat spread/cost, and keeps an order log plus
closed-trade history for assertions.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trend_only_scalper.brokers.base import Broker
from trend_only_scalper.models import ClosedTrade, CloseReason, Position, Side

_EMPTY_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class MockBroker(Broker):
    def __init__(
        self,
        symbol: str,
        strategy_id: str = "trend_only_scalper",
        starting_equity: float = 10_000.0,
        trading_cost: float = 0.02,
    ) -> None:
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.starting_equity = starting_equity
        self.trading_cost = trading_cost

        self._bars: dict[str, pd.DataFrame] = {}
        self._position: Position | None = None
        self._position_counter = 0
        self._orders: list[dict] = []
        self._closed_trades: list[ClosedTrade] = []

    # --- test/dry-run helpers, not part of the Broker interface -----------

    def set_bars(self, timeframe: str, df: pd.DataFrame) -> None:
        """Inject the OHLCV DataFrame a test or dry-run run wants `get_bars` to return."""
        self._bars[timeframe] = df.reset_index(drop=True)

    def seed_realized_pnl(self, amount: float) -> None:
        """Test helper: inject a synthetic realized P&L as if a trade already closed today,
        so daily-guard threshold tests don't need to play out a full open/close cycle.
        """
        self._closed_trades.append(
            ClosedTrade(
                position_id="seed",
                symbol=self.symbol,
                side=Side.BUY,
                quantity=0.0,
                entry_price=0.0,
                exit_price=0.0,
                opened_at=datetime.min,
                closed_at=datetime.min,
                realized_pnl_cash=amount,
                reason=CloseReason.MANUAL,
            )
        )

    def get_order_log(self) -> list[dict]:
        return list(self._orders)

    def get_trade_history(self) -> list[ClosedTrade]:
        return list(self._closed_trades)

    def _latest_m1_row(self) -> pd.Series:
        df = self._bars.get("M1")
        if df is None or df.empty:
            raise RuntimeError("MockBroker: no M1 bars injected yet -- call set_bars('M1', df)")
        return df.iloc[-1]

    def _latest_price(self) -> float:
        return float(self._latest_m1_row()["close"])

    def _latest_time(self) -> datetime:
        value = self._latest_m1_row()["time"]
        return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value

    def _require_symbol(self, symbol: str) -> None:
        if symbol != self.symbol:
            raise ValueError(f"MockBroker is configured for symbol {self.symbol!r}, got {symbol!r}")

    # --- Broker interface --------------------------------------------------

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self._require_symbol(symbol)
        df = self._bars.get(timeframe)
        if df is None:
            return pd.DataFrame(columns=_EMPTY_OHLCV_COLUMNS)
        return df.tail(limit).reset_index(drop=True)

    def get_open_position(self, symbol: str, strategy_id: str) -> Position | None:
        self._require_symbol(symbol)
        if self._position is None:
            return None
        if self._position.strategy_id != strategy_id:
            return None
        return self._position

    def get_position_count(self, symbol: str, strategy_id: str) -> int:
        return 1 if self.get_open_position(symbol, strategy_id) is not None else 0

    def open_market_order(
        self, symbol: str, side: Side, quantity: float, stop_loss: float
    ) -> Position:
        self._require_symbol(symbol)
        if self._position is not None:
            raise RuntimeError(
                "MockBroker: cannot open a new position while one is already open "
                "(one-position-only rule)"
            )

        self._position_counter += 1
        position = Position(
            position_id=f"mock-{self._position_counter}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=self._latest_price(),
            stop_loss=stop_loss,
            opened_at=self._latest_time(),
            strategy_id=self.strategy_id,
        )
        self._position = position
        self._orders.append(
            {
                "type": "OPEN",
                "position_id": position.position_id,
                "side": side,
                "quantity": quantity,
                "entry_price": position.entry_price,
                "stop_loss": stop_loss,
            }
        )
        return position

    def close_position(self, position_id: str, reason: CloseReason) -> None:
        position = self._position
        if position is None or position.position_id != position_id:
            raise ValueError(f"MockBroker: no open position with id {position_id!r}")

        exit_price = self._latest_price()
        realized_pnl_cash = self.get_unrealized_pnl(position)
        trade = ClosedTrade(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            opened_at=position.opened_at,
            closed_at=self._latest_time(),
            realized_pnl_cash=realized_pnl_cash,
            reason=reason,
        )
        self._closed_trades.append(trade)
        self._orders.append(
            {
                "type": "CLOSE",
                "position_id": position_id,
                "reason": reason,
                "exit_price": exit_price,
                "realized_pnl_cash": realized_pnl_cash,
            }
        )
        self._position = None

    def modify_stop_loss(self, position_id: str, new_stop_loss: float) -> None:
        position = self._position
        if position is None or position.position_id != position_id:
            raise ValueError(f"MockBroker: no open position with id {position_id!r}")
        position.stop_loss = new_stop_loss
        self._orders.append(
            {"type": "MODIFY_SL", "position_id": position_id, "new_stop_loss": new_stop_loss}
        )

    def get_unrealized_pnl(self, position: Position) -> float:
        latest_price = self._latest_price()
        price_diff = (
            latest_price - position.entry_price
            if position.side is Side.BUY
            else position.entry_price - latest_price
        )
        return price_diff * position.quantity

    def get_trading_cost(self, symbol: str) -> float:
        self._require_symbol(symbol)
        return self.trading_cost

    def get_account_equity(self) -> float:
        return self.starting_equity + self.get_today_realized_pnl()

    def get_today_realized_pnl(self) -> float:
        # MockBroker represents a single dry-run/test session, so "today" is simply
        # every trade closed so far in this session.
        return sum(trade.realized_pnl_cash for trade in self._closed_trades)
