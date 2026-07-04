"""MT5 broker adapter using the MetaTrader5 Python package.

This is the ONLY module in the project that imports MetaTrader5 -- the bot loop and all
strategy/risk code depend solely on brokers.base.Broker, so nothing outside this file
needs to change to support MT5, and nothing here leaks into strategy logic.

Safety model: when config.allow_live_trading is False (the default), this adapter still
connects to MT5 for REAL market data (bars, tick prices, symbol/account info -- all
read-only and harmless) but SIMULATES order placement locally instead of calling
order_send(), the same way MockBroker does. Real orders only go out once
allow_live_trading is explicitly set to true in mt5.yaml, and even then only for this
strategy's own magic number -- positions with any other magic number are never touched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from trend_only_scalper.brokers.base import Broker
from trend_only_scalper.config import MT5Config
from trend_only_scalper.models import ClosedTrade, CloseReason, Position, Side

logger = logging.getLogger("trend_only_scalper.brokers.mt5")

_TIMEFRAME_ATTRS = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15"}
_FILLING_MODE_ATTRS = {
    "IOC": "ORDER_FILLING_IOC",
    "FOK": "ORDER_FILLING_FOK",
    "RETURN": "ORDER_FILLING_RETURN",
}
_FILLING_MODES = ("IOC", "FOK", "RETURN")


def _import_mt5_module():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover - only hit without the package installed
        raise RuntimeError(
            "MetaTrader5 package is not installed (Windows only). "
            "Install it with `pip install MetaTrader5` before using MT5Broker."
        ) from exc
    return mt5


def _decimal_places(step: float) -> int:
    """Number of decimal places implied by a step size, e.g. 0.01 -> 2, 1.0 -> 0."""
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[-1]) if "." in text else 0


class MT5Broker(Broker):
    def __init__(
        self,
        config: MT5Config,
        strategy_id: str = "trend_only_scalper",
        mt5_module: object | None = None,
    ) -> None:
        self._mt5 = mt5_module if mt5_module is not None else _import_mt5_module()
        self.config = config
        self.strategy_id = strategy_id
        self.symbol = config.symbol
        self.magic_number = config.magic
        self.allow_live_trading = config.allow_live_trading
        self._connected = False
        self._selected_symbols: set[str] = set()

        # Used only while allow_live_trading is False: mirrors MockBroker so the full loop
        # (management, cooldown, journal) still exercises correctly against real market
        # data without ever touching the live account.
        self._simulated_position: Position | None = None
        self._simulated_closed_trades: list[ClosedTrade] = []
        self._position_counter = 0

    # --- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        kwargs: dict = {}
        if self.config.path:
            kwargs["path"] = self.config.path
        if self.config.login:
            kwargs["login"] = self.config.login
        if self.config.password:
            kwargs["password"] = self.config.password
        if self.config.server:
            kwargs["server"] = self.config.server

        if not self._mt5.initialize(**kwargs):
            code, description = self._mt5.last_error()
            raise RuntimeError(f"MT5Broker: initialize() failed (code={code}, {description})")
        self._connected = True
        logger.info("MT5 terminal connected (symbol=%s, magic=%s)", self.symbol, self.magic_number)

        if not self.allow_live_trading:
            logger.warning(
                "allow_live_trading is False -- MT5Broker will use REAL market data but "
                "SIMULATE order placement locally. No real orders will be sent."
            )

    def disconnect(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False
            logger.info("MT5 terminal disconnected")

    def _select_symbol(self, symbol: str) -> None:
        if symbol in self._selected_symbols:
            return
        if not self._mt5.symbol_select(symbol, True):
            code, description = self._mt5.last_error()
            raise RuntimeError(f"MT5Broker: symbol_select({symbol}) failed (code={code}, {description})")
        self._selected_symbols.add(symbol)

    # --- normalization ---------------------------------------------------------

    def _symbol_info(self, symbol: str):
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"MT5Broker: symbol_info({symbol}) returned None -- is it selected?")
        return info

    def _normalize_price(self, symbol: str, price: float) -> float:
        return round(price, self._symbol_info(symbol).digits)

    def _normalize_volume(self, symbol: str, volume: float) -> float:
        info = self._symbol_info(symbol)
        step = info.volume_step or 0.01
        normalized = round(volume / step) * step
        normalized = min(max(normalized, info.volume_min), info.volume_max)
        return round(normalized, _decimal_places(step))

    # --- bars ------------------------------------------------------------------

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self._select_symbol(symbol)
        mt5_timeframe = getattr(self._mt5, _TIMEFRAME_ATTRS[timeframe])
        rates = self._mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, limit)
        columns = ["time", "open", "high", "low", "close", "volume"]
        if rates is None or len(rates) == 0:
            return pd.DataFrame(columns=columns)

        raw = pd.DataFrame(rates)
        return pd.DataFrame(
            {
                "time": pd.to_datetime(raw["time"], unit="s", utc=True),
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "volume": raw["tick_volume"],
            }
        )

    # --- positions ---------------------------------------------------------

    def _real_positions(self, symbol: str) -> list:
        positions = self._mt5.positions_get(symbol=symbol) or ()
        return [p for p in positions if p.magic == self.magic_number]

    def _to_position(self, mt5_pos) -> Position:
        side = Side.BUY if mt5_pos.type == self._mt5.ORDER_TYPE_BUY else Side.SELL
        return Position(
            position_id=str(mt5_pos.ticket),
            symbol=mt5_pos.symbol,
            side=side,
            quantity=mt5_pos.volume,
            entry_price=mt5_pos.price_open,
            stop_loss=mt5_pos.sl,
            opened_at=datetime.fromtimestamp(mt5_pos.time, tz=timezone.utc),
            strategy_id=self.strategy_id,
            magic_number=mt5_pos.magic,
        )

    def get_open_position(self, symbol: str, strategy_id: str) -> Position | None:
        if not self.allow_live_trading:
            position = self._simulated_position
            return position if position and position.symbol == symbol else None

        matches = self._real_positions(symbol)
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                "MT5 reports %d open positions with magic=%s on %s; one-position-only expects "
                "at most 1 -- using the first and leaving the rest untouched",
                len(matches), self.magic_number, symbol,
            )
        return self._to_position(matches[0])

    def get_position_count(self, symbol: str, strategy_id: str) -> int:
        if not self.allow_live_trading:
            position = self._simulated_position
            return 1 if (position and position.symbol == symbol) else 0
        return len(self._real_positions(symbol))

    # --- orders --------------------------------------------------------------

    def open_market_order(self, symbol: str, side: Side, quantity: float, stop_loss: float) -> Position:
        if self.get_position_count(symbol, self.strategy_id) > 0:
            raise RuntimeError("MT5Broker: a position is already open; one-position-only rule violated")

        self._select_symbol(symbol)
        tick = self._mt5.symbol_info_tick(symbol)
        price = self._normalize_price(symbol, tick.ask if side is Side.BUY else tick.bid)
        volume = self._normalize_volume(symbol, quantity)
        normalized_sl = self._normalize_price(symbol, stop_loss)

        if not self.allow_live_trading:
            logger.warning(
                "[SIMULATED, allow_live_trading=False] would open %s %s volume=%s price=%.5f "
                "sl=%.5f magic=%s",
                side.value, symbol, volume, price, normalized_sl, self.magic_number,
            )
            self._position_counter += 1
            position = Position(
                position_id=f"mt5-sim-{self._position_counter}",
                symbol=symbol,
                side=side,
                quantity=volume,
                entry_price=price,
                stop_loss=normalized_sl,
                opened_at=datetime.now(timezone.utc),
                strategy_id=self.strategy_id,
                magic_number=self.magic_number,
            )
            self._simulated_position = position
            return position

        order_type = self._mt5.ORDER_TYPE_BUY if side is Side.BUY else self._mt5.ORDER_TYPE_SELL
        result = self._send_order_with_filling_fallback(
            action=self._mt5.TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=volume,
            type=order_type,
            price=price,
            sl=normalized_sl,
            deviation=self.config.deviation,
            magic=self.magic_number,
            comment=self.config.order_comment,
            type_time=self._mt5.ORDER_TIME_GTC,
        )
        self._raise_if_order_failed(result, "open_market_order")

        position = self.get_open_position(symbol, self.strategy_id)
        if position is None:
            raise RuntimeError("MT5Broker: order_send succeeded but no matching position was found afterward")
        return position

    def close_position(self, position_id: str, reason: CloseReason) -> None:
        if not self.allow_live_trading:
            position = self._simulated_position
            if position is None or position.position_id != position_id:
                raise ValueError(f"MT5Broker: no simulated open position with id {position_id!r}")
            exit_price = self._simulated_latest_price(position.symbol)
            realized_pnl = self.get_unrealized_pnl(position)
            self._simulated_closed_trades.append(
                ClosedTrade(
                    position_id=position.position_id,
                    symbol=position.symbol,
                    side=position.side,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    opened_at=position.opened_at,
                    closed_at=datetime.now(timezone.utc),
                    realized_pnl_cash=realized_pnl,
                    reason=reason,
                )
            )
            logger.warning(
                "[SIMULATED, allow_live_trading=False] would close position=%s reason=%s",
                position_id, reason.value,
            )
            self._simulated_position = None
            return

        ticket = int(position_id)
        mt5_pos = self._get_real_position_or_raise(ticket, "close_position")

        tick = self._mt5.symbol_info_tick(mt5_pos.symbol)
        opposite_type = (
            self._mt5.ORDER_TYPE_SELL if mt5_pos.type == self._mt5.ORDER_TYPE_BUY else self._mt5.ORDER_TYPE_BUY
        )
        price = tick.bid if opposite_type == self._mt5.ORDER_TYPE_SELL else tick.ask

        result = self._send_order_with_filling_fallback(
            action=self._mt5.TRADE_ACTION_DEAL,
            symbol=mt5_pos.symbol,
            volume=mt5_pos.volume,
            type=opposite_type,
            position=ticket,
            price=self._normalize_price(mt5_pos.symbol, price),
            deviation=self.config.deviation,
            magic=self.magic_number,
            comment=f"{self.config.order_comment}:{reason.value}",
            type_time=self._mt5.ORDER_TIME_GTC,
        )
        self._raise_if_order_failed(result, "close_position")

    def modify_stop_loss(self, position_id: str, new_stop_loss: float) -> None:
        if not self.allow_live_trading:
            position = self._simulated_position
            if position is None or position.position_id != position_id:
                raise ValueError(f"MT5Broker: no simulated open position with id {position_id!r}")
            position.stop_loss = new_stop_loss
            logger.warning(
                "[SIMULATED, allow_live_trading=False] would modify SL position=%s new_sl=%.5f",
                position_id, new_stop_loss,
            )
            return

        ticket = int(position_id)
        mt5_pos = self._get_real_position_or_raise(ticket, "modify_stop_loss")

        result = self._mt5.order_send(
            {
                "action": self._mt5.TRADE_ACTION_SLTP,
                "symbol": mt5_pos.symbol,
                "position": ticket,
                "sl": self._normalize_price(mt5_pos.symbol, new_stop_loss),
                "tp": mt5_pos.tp,
                "magic": self.magic_number,
            }
        )
        self._raise_if_order_failed(result, "modify_stop_loss")

    def _get_real_position_or_raise(self, ticket: int, action: str):
        positions = self._mt5.positions_get(ticket=ticket) or ()
        if not positions:
            raise ValueError(f"MT5Broker: no open MT5 position with ticket {ticket}")
        mt5_pos = positions[0]
        if mt5_pos.magic != self.magic_number:
            raise ValueError(
                f"MT5Broker: refusing to {action} ticket {ticket} -- magic={mt5_pos.magic} "
                f"does not match this strategy's magic={self.magic_number}"
            )
        return mt5_pos

    # --- P&L, cost, equity -----------------------------------------------------

    def _simulated_latest_price(self, symbol: str) -> float:
        tick = self._mt5.symbol_info_tick(symbol)
        return (tick.bid + tick.ask) / 2.0

    def get_unrealized_pnl(self, position: Position) -> float:
        if not self.allow_live_trading:
            latest_price = self._simulated_latest_price(position.symbol)
            move = latest_price - position.entry_price
            return (move if position.side is Side.BUY else -move) * position.quantity

        positions = self._mt5.positions_get(ticket=int(position.position_id)) or ()
        if not positions:
            logger.warning("MT5Broker: position %s not found when reading unrealized PnL", position.position_id)
            return 0.0
        return positions[0].profit

    def get_trading_cost(self, symbol: str) -> float:
        self._select_symbol(symbol)
        tick = self._mt5.symbol_info_tick(symbol)
        return tick.ask - tick.bid

    def get_account_equity(self) -> float:
        info = self._mt5.account_info()
        return info.equity if info else 0.0

    def get_today_realized_pnl(self) -> float:
        if not self.allow_live_trading:
            return sum(trade.realized_pnl_cash for trade in self._simulated_closed_trades)

        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        deals = self._mt5.history_deals_get(start_of_day, now) or ()
        return sum(deal.profit for deal in deals if getattr(deal, "magic", None) == self.magic_number)

    # --- order_send plumbing -----------------------------------------------

    def _filling_fallback_order(self) -> list[str]:
        configured = self.config.filling_type
        return [configured, *[mode for mode in _FILLING_MODES if mode != configured]]

    def _send_order_with_filling_fallback(self, **request: object):
        """Try the configured filling mode first, then fall back through the others --
        different brokers/symbols accept different modes and there's no reliable way to
        know which without asking, so we try in order and log every attempt.
        """
        last_result = None
        for filling_name in self._filling_fallback_order():
            # A fresh dict per attempt -- reusing/mutating one risks any caller that keeps
            # a reference (loggers, test doubles, MT5 itself) seeing only the final value.
            attempt = dict(request)
            attempt["type_filling"] = getattr(self._mt5, _FILLING_MODE_ATTRS[filling_name])
            result = self._mt5.order_send(attempt)
            last_result = result
            retcode = getattr(result, "retcode", None)
            done_retcode = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
            logger.info(
                "order_send filling=%s retcode=%s comment=%s",
                filling_name, retcode, getattr(result, "comment", ""),
            )
            if retcode == done_retcode:
                return result
            logger.warning(
                "order_send rejected with filling=%s (retcode=%s) -- trying next filling mode",
                filling_name, retcode,
            )
        return last_result

    def _raise_if_order_failed(self, result, action: str) -> None:
        done_retcode = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
        retcode = getattr(result, "retcode", None)
        if retcode != done_retcode:
            raise RuntimeError(
                f"MT5Broker: {action} failed after trying all filling modes "
                f"(retcode={retcode}, comment={getattr(result, 'comment', '')!r})"
            )
