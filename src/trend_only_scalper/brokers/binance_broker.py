"""Binance broker adapter using ccxt (futures, testnet by default).

This is the ONLY module in the project that imports ccxt -- the bot loop and all
strategy/risk code depend solely on brokers.base.Broker, so nothing outside this file
needs to change to support Binance, and nothing here leaks into strategy logic.

Safety model: when config.allow_live_trading is False (the default), this adapter still
connects to Binance (testnet or mainnet, per config.testnet) for REAL market data (OHLCV,
tickers, balances -- all read-only) but SIMULATES order placement locally instead of
calling create_order(), the same way MockBroker/MT5Broker do. Real orders only go out
once allow_live_trading is explicitly true, and testnet should be preferred even then.

Binance futures has no per-order "magic number" like MT5 -- a position is reported per
symbol, not per originating order. This adapter therefore assumes the configured symbol
is dedicated to this one strategy (the standard convention for Binance bots); it does not
attempt to distinguish "our" position from a manually-opened one on the same symbol.

Stop-loss is not a field on a Binance position (unlike MT5) -- it's tracked here as a
separate STOP_MARKET reduce-only order, created alongside the entry and replaced whenever
modify_stop_loss() is called.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from trend_only_scalper.brokers.base import Broker
from trend_only_scalper.config import BinanceConfig
from trend_only_scalper.models import ClosedTrade, CloseReason, Position, Side

logger = logging.getLogger("trend_only_scalper.brokers.binance")

_TIMEFRAME_MAP = {"M1": "1m", "M5": "5m", "M15": "15m"}


def _import_ccxt_module():
    try:
        import ccxt
    except ImportError as exc:  # pragma: no cover - only hit without the package installed
        raise RuntimeError(
            "ccxt package is not installed. Install it with `pip install ccxt` before "
            "using BinanceBroker."
        ) from exc
    return ccxt


class BinanceBroker(Broker):
    def __init__(
        self,
        config: BinanceConfig,
        strategy_id: str = "trend_only_scalper",
        exchange: object | None = None,
    ) -> None:
        self.config = config
        self.strategy_id = strategy_id
        self.symbol = config.symbol
        self.allow_live_trading = config.allow_live_trading
        self._markets_loaded = False

        self._exchange = exchange if exchange is not None else self._build_exchange()

        # Tracks the SL price/order id per symbol -- Binance positions don't carry an SL
        # field, so we own this bookkeeping ourselves.
        self._stop_loss_by_symbol: dict[str, float] = {}
        self._stop_order_id_by_symbol: dict[str, str] = {}

        # Used only while allow_live_trading is False: mirrors MockBroker so the full loop
        # (management, cooldown, journal) still exercises correctly against real market
        # data without ever touching the live account.
        self._simulated_position: Position | None = None
        self._simulated_closed_trades: list[ClosedTrade] = []
        self._position_counter = 0

    def _build_exchange(self):
        ccxt = _import_ccxt_module()
        exchange = ccxt.binance(
            {
                "apiKey": self.config.api_key or "",
                "secret": self.config.api_secret or "",
                "enableRateLimit": True,
                "options": {"defaultType": "future" if self.config.market_type == "futures" else "spot"},
            }
        )
        if self.config.testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    # --- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        self._exchange.load_markets()
        self._markets_loaded = True
        logger.info(
            "Binance exchange connected (symbol=%s, market_type=%s, testnet=%s)",
            self.symbol, self.config.market_type, self.config.testnet,
        )
        if not self.allow_live_trading:
            logger.warning(
                "allow_live_trading is False -- BinanceBroker will use REAL market data but "
                "SIMULATE order placement locally. No real orders will be sent."
            )
        elif self.config.market_type == "futures":
            try:
                self._exchange.set_leverage(self.config.leverage, self.symbol)
            except Exception as exc:  # ccxt raises exchange-specific errors here
                logger.warning("Could not set leverage to %s for %s: %s", self.config.leverage, self.symbol, exc)

    def disconnect(self) -> None:
        close = getattr(self._exchange, "close", None)
        if callable(close):
            close()

    # --- normalization ---------------------------------------------------------

    def _market(self, symbol: str) -> dict:
        if not self._markets_loaded:
            self.connect()
        return self._exchange.market(symbol)

    def _normalize_price(self, symbol: str, price: float) -> float:
        return float(self._exchange.price_to_precision(symbol, price))

    def _normalize_quantity(self, symbol: str, quantity: float, price: float) -> float:
        market = self._market(symbol)
        limits = market.get("limits", {}) or {}

        normalized = float(self._exchange.amount_to_precision(symbol, quantity))

        min_amount = (limits.get("amount") or {}).get("min")
        if min_amount and normalized < min_amount:
            normalized = float(self._exchange.amount_to_precision(symbol, min_amount))

        min_cost = (limits.get("cost") or {}).get("min")
        if min_cost and price > 0 and normalized * price < min_cost:
            bumped = min_cost / price
            normalized = float(self._exchange.amount_to_precision(symbol, bumped))
            logger.warning(
                "Quantity bumped to %.8f to satisfy min notional %.4f for %s",
                normalized, min_cost, symbol,
            )
        return normalized

    # --- fees ------------------------------------------------------------------

    def estimate_fee_cash(self, quantity: float, price: float) -> float:
        """Round-trip (entry + exit) taker fee estimate, in cash, at fee_rate_estimate/side."""
        return 2 * self.config.fee_rate_estimate * quantity * price

    def is_cost_too_high_for_target(self, quantity: float, price: float, tp_cash: float) -> bool:
        """True if the estimated round-trip fee eats more than max_cost_ratio_to_tp of tp_cash."""
        if tp_cash <= 0:
            return False
        return self.estimate_fee_cash(quantity, price) / tp_cash > self.config.max_cost_ratio_to_tp

    # --- bars ------------------------------------------------------------------

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        ccxt_timeframe = _TIMEFRAME_MAP[timeframe]
        ohlcv = self._exchange.fetch_ohlcv(symbol, timeframe=ccxt_timeframe, limit=limit)
        columns = ["time", "open", "high", "low", "close", "volume"]
        if not ohlcv:
            return pd.DataFrame(columns=columns)

        raw = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(
            {
                "time": pd.to_datetime(raw["timestamp"], unit="ms", utc=True),
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "volume": raw["volume"],
            }
        )

    # --- prices -------------------------------------------------------------

    def _latest_price(self, symbol: str) -> float:
        ticker = self._exchange.fetch_ticker(symbol)
        return float(ticker.get("last") or ticker.get("close") or 0.0)

    # --- positions ---------------------------------------------------------

    def _fetch_positions(self, symbol: str) -> list[dict]:
        positions = self._exchange.fetch_positions([symbol]) or []
        return [p for p in positions if abs(float(p.get("contracts") or 0)) > 0]

    def _to_position(self, pos: dict) -> Position:
        side = Side.BUY if pos.get("side") == "long" else Side.SELL
        symbol = pos.get("symbol", self.symbol)
        timestamp_ms = pos.get("timestamp")
        opened_at = (
            datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            if timestamp_ms
            else datetime.now(timezone.utc)
        )
        return Position(
            position_id=f"{symbol}:{pos.get('side')}",
            symbol=symbol,
            side=side,
            quantity=abs(float(pos.get("contracts") or 0)),
            entry_price=float(pos.get("entryPrice") or 0.0),
            stop_loss=self._stop_loss_by_symbol.get(symbol, 0.0),
            opened_at=opened_at,
            strategy_id=self.strategy_id,
        )

    def get_open_position(self, symbol: str, strategy_id: str) -> Position | None:
        if not self.allow_live_trading:
            position = self._simulated_position
            return position if position and position.symbol == symbol else None

        positions = self._fetch_positions(symbol)
        if not positions:
            return None
        if len(positions) > 1:
            logger.warning(
                "Binance reports %d open positions for %s; one-position-only expects at "
                "most 1 -- using the first and leaving the rest untouched",
                len(positions), symbol,
            )
        return self._to_position(positions[0])

    def get_position_count(self, symbol: str, strategy_id: str) -> int:
        if not self.allow_live_trading:
            position = self._simulated_position
            return 1 if (position and position.symbol == symbol) else 0
        return len(self._fetch_positions(symbol))

    def _symbol_from_position_id(self, position_id: str) -> str:
        return position_id.split(":", 1)[0]

    # --- orders --------------------------------------------------------------

    def open_market_order(self, symbol: str, side: Side, quantity: float, stop_loss: float) -> Position:
        if self.get_position_count(symbol, self.strategy_id) > 0:
            raise RuntimeError("BinanceBroker: a position is already open; one-position-only rule violated")

        price = self._latest_price(symbol)
        normalized_qty = self._normalize_quantity(symbol, quantity, price)
        normalized_sl = self._normalize_price(symbol, stop_loss)

        if not self.allow_live_trading:
            logger.warning(
                "[SIMULATED, allow_live_trading=False] would open %s %s qty=%.8f price=%.5f sl=%.5f",
                side.value, symbol, normalized_qty, price, normalized_sl,
            )
            self._position_counter += 1
            position = Position(
                position_id=f"binance-sim-{self._position_counter}",
                symbol=symbol,
                side=side,
                quantity=normalized_qty,
                entry_price=price,
                stop_loss=normalized_sl,
                opened_at=datetime.now(timezone.utc),
                strategy_id=self.strategy_id,
            )
            self._simulated_position = position
            self._stop_loss_by_symbol[symbol] = normalized_sl
            return position

        ccxt_side = "buy" if side is Side.BUY else "sell"
        order = self._exchange.create_order(
            symbol, "market", ccxt_side, normalized_qty,
            params={"recvWindow": self.config.recv_window},
        )
        logger.info("Binance order response: id=%s status=%s", order.get("id"), order.get("status"))

        stop_side = "sell" if side is Side.BUY else "buy"
        try:
            stop_order = self._exchange.create_order(
                symbol, "STOP_MARKET", stop_side, normalized_qty, None,
                params={"stopPrice": normalized_sl, "reduceOnly": True, "recvWindow": self.config.recv_window},
            )
        except Exception as exc:
            # The entry already filled but its protective stop failed to attach -- a naked
            # position violates "hard SL required on every order". Flatten it immediately
            # with a compensating reduce-only market order rather than leaving it exposed,
            # then raise so the caller never treats this as a successful entry.
            logger.error(
                "Stop-loss order failed to place after entry filled (%s %s qty=%.8f) -- "
                "closing the position immediately to avoid a naked position: %s",
                side.value, symbol, normalized_qty, exc,
            )
            try:
                self._exchange.create_order(
                    symbol, "market", stop_side, normalized_qty,
                    params={"reduceOnly": True, "recvWindow": self.config.recv_window},
                )
                logger.error("Compensating close succeeded -- no naked position remains for %s.", symbol)
            except Exception as close_exc:
                logger.critical(
                    "COMPENSATING CLOSE ALSO FAILED for %s -- a REAL UNPROTECTED POSITION may "
                    "still be open on the exchange. Manual intervention required immediately: %s",
                    symbol, close_exc,
                )
            raise RuntimeError(
                f"BinanceBroker: stop-loss placement failed after entry filled for {symbol}; "
                "attempted a compensating close (see logs for outcome)"
            ) from exc

        self._stop_order_id_by_symbol[symbol] = stop_order.get("id")
        self._stop_loss_by_symbol[symbol] = normalized_sl

        position = self.get_open_position(symbol, self.strategy_id)
        if position is None:
            raise RuntimeError("BinanceBroker: order placed but no matching position was found afterward")
        return position

    def close_position(self, position_id: str, reason: CloseReason) -> None:
        if not self.allow_live_trading:
            position = self._simulated_position
            if position is None or position.position_id != position_id:
                raise ValueError(f"BinanceBroker: no simulated open position with id {position_id!r}")
            exit_price = self._latest_price(position.symbol)
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
            self._stop_loss_by_symbol.pop(position.symbol, None)
            return

        symbol = self._symbol_from_position_id(position_id)
        positions = self._fetch_positions(symbol)
        if not positions:
            raise ValueError(f"BinanceBroker: no open position found for {position_id!r}")
        pos = positions[0]
        quantity = abs(float(pos.get("contracts") or 0))
        close_side = "sell" if pos.get("side") == "long" else "buy"

        self._cancel_stop_order(symbol)

        order = self._exchange.create_order(
            symbol, "market", close_side, quantity,
            params={"reduceOnly": True, "recvWindow": self.config.recv_window},
        )
        logger.info(
            "Binance close order response: id=%s status=%s reason=%s",
            order.get("id"), order.get("status"), reason.value,
        )
        self._stop_loss_by_symbol.pop(symbol, None)

    def modify_stop_loss(self, position_id: str, new_stop_loss: float) -> None:
        if not self.allow_live_trading:
            position = self._simulated_position
            if position is None or position.position_id != position_id:
                raise ValueError(f"BinanceBroker: no simulated open position with id {position_id!r}")
            position.stop_loss = new_stop_loss
            self._stop_loss_by_symbol[position.symbol] = new_stop_loss
            logger.warning(
                "[SIMULATED, allow_live_trading=False] would modify SL position=%s new_sl=%.5f",
                position_id, new_stop_loss,
            )
            return

        symbol = self._symbol_from_position_id(position_id)
        positions = self._fetch_positions(symbol)
        if not positions:
            raise ValueError(f"BinanceBroker: no open position found for {position_id!r}")
        pos = positions[0]
        quantity = abs(float(pos.get("contracts") or 0))
        stop_side = "sell" if pos.get("side") == "long" else "buy"
        normalized_sl = self._normalize_price(symbol, new_stop_loss)
        old_stop_order_id = self._stop_order_id_by_symbol.get(symbol)

        # Place the NEW stop BEFORE cancelling the old one -- if this fails, the position
        # keeps its existing (still-valid, just not yet improved) protection instead of a
        # window with no stop-loss at all. A stray old reduce-only order left active for a
        # moment can't over-close the position once the new one is live.
        try:
            stop_order = self._exchange.create_order(
                symbol, "STOP_MARKET", stop_side, quantity, None,
                params={"stopPrice": normalized_sl, "reduceOnly": True, "recvWindow": self.config.recv_window},
            )
        except Exception as exc:
            logger.warning(
                "Failed to place updated stop-loss for %s -- the previous stop-loss order "
                "was left in place (not cancelled) and still protects the position: %s",
                symbol, exc,
            )
            raise

        self._stop_order_id_by_symbol[symbol] = stop_order.get("id")
        self._stop_loss_by_symbol[symbol] = normalized_sl

        if old_stop_order_id:
            try:
                self._exchange.cancel_order(old_stop_order_id, symbol)
            except Exception as exc:  # ccxt raises exchange-specific errors here
                logger.warning(
                    "Failed to cancel superseded stop order %s for %s (harmless -- the new "
                    "stop is already live): %s", old_stop_order_id, symbol, exc,
                )

    def _cancel_stop_order(self, symbol: str) -> None:
        stop_order_id = self._stop_order_id_by_symbol.pop(symbol, None)
        if not stop_order_id:
            return
        try:
            self._exchange.cancel_order(stop_order_id, symbol)
        except Exception as exc:  # ccxt raises exchange-specific errors here
            logger.warning("Failed to cancel stop order %s for %s: %s", stop_order_id, symbol, exc)

    # --- P&L, cost, equity -----------------------------------------------------

    def get_unrealized_pnl(self, position: Position) -> float:
        if not self.allow_live_trading:
            latest_price = self._latest_price(position.symbol)
            move = latest_price - position.entry_price
            return (move if position.side is Side.BUY else -move) * position.quantity

        positions = self._fetch_positions(position.symbol)
        if not positions:
            logger.warning("BinanceBroker: position not found for %s when reading unrealized PnL", position.symbol)
            return 0.0
        return float(positions[0].get("unrealizedPnl") or 0.0)

    def get_trading_cost(self, symbol: str) -> float:
        """Round-trip taker fee estimate, expressed as a price-unit spread equivalent
        (2 * fee_rate_estimate * price) -- consistent with how MT5's bid/ask spread is used
        by the strategy's ATR/spread and cost-buffer checks.
        """
        return 2 * self.config.fee_rate_estimate * self._latest_price(symbol)

    def get_account_equity(self) -> float:
        balance = self._exchange.fetch_balance()
        usdt = balance.get("USDT") or {}
        return float(usdt.get("total") or 0.0)

    def get_today_realized_pnl(self) -> float:
        if not self.allow_live_trading:
            return sum(trade.realized_pnl_cash for trade in self._simulated_closed_trades)

        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since_ms = int(start_of_day.timestamp() * 1000)
        trades = self._exchange.fetch_my_trades(self.symbol, since=since_ms) or []
        return sum(float((t.get("info") or {}).get("realizedPnl", 0.0)) for t in trades)
