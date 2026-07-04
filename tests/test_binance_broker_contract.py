"""Contract-style tests for BinanceBroker using a fully faked ccxt exchange object.

No real Binance API (or even the real ccxt network layer) is exercised -- these tests
only verify OUR adapter logic (OHLCV conversion, precision/minimum-quantity/notional
normalization, the live-trading safety gate, reduce-only close orders, and fee estimates),
since the real Binance API should never be called in tests.
"""

import pandas as pd
import pytest

from trend_only_scalper.brokers.binance_broker import BinanceBroker
from trend_only_scalper.config import BinanceConfig
from trend_only_scalper.models import CloseReason, Side

SYMBOL = "BTC/USDT"
STRATEGY_ID = "trend_only_scalper"


class FakeExchange:
    """Minimal stand-in for a ccxt exchange instance: just the surface BinanceBroker uses."""

    def __init__(self):
        self.markets_data: dict[str, dict] = {}
        self.tickers: dict[str, dict] = {}
        self.positions: list[dict] = []
        self.ohlcv_map: dict[str, list] = {}
        self.balance: dict = {"USDT": {"total": 0.0}}
        self.created_orders: list[dict] = []
        self.canceled_orders: list[tuple] = []
        self.my_trades: list[dict] = []

    def load_markets(self):
        return self.markets_data

    def market(self, symbol):
        return self.markets_data.get(symbol, {})

    def price_to_precision(self, symbol, price):
        digits = self.markets_data.get(symbol, {}).get("precision", {}).get("price", 2)
        return f"{round(price, digits):.{digits}f}"

    def amount_to_precision(self, symbol, amount):
        digits = self.markets_data.get(symbol, {}).get("precision", {}).get("amount", 3)
        return f"{round(amount, digits):.{digits}f}"

    def fetch_ohlcv(self, symbol, timeframe=None, limit=None):
        return self.ohlcv_map.get(symbol, [])

    def fetch_ticker(self, symbol):
        return self.tickers.get(symbol, {})

    def fetch_positions(self, symbols):
        symbol = symbols[0] if symbols else None
        return [p for p in self.positions if p.get("symbol") == symbol]

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        order = {
            "id": f"order-{len(self.created_orders) + 1}",
            "status": "closed",
            "symbol": symbol,
            "type": type_,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params or {},
        }
        self.created_orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        self.canceled_orders.append((order_id, symbol))
        return {"id": order_id, "status": "canceled"}

    def fetch_balance(self):
        return self.balance

    def fetch_my_trades(self, symbol, since=None):
        return self.my_trades

    def set_leverage(self, leverage, symbol):
        pass

    def close(self):
        pass


def make_broker(allow_live_trading: bool = False, **overrides) -> tuple[BinanceBroker, FakeExchange]:
    fake = FakeExchange()
    cfg = BinanceConfig(symbol=SYMBOL, allow_live_trading=allow_live_trading, **overrides)
    broker = BinanceBroker(cfg, strategy_id=STRATEGY_ID, exchange=fake)
    return broker, fake


def make_market(price_digits=2, amount_digits=3, min_amount=None, min_cost=None) -> dict:
    limits = {}
    if min_amount is not None:
        limits["amount"] = {"min": min_amount}
    if min_cost is not None:
        limits["cost"] = {"min": min_cost}
    return {"precision": {"price": price_digits, "amount": amount_digits}, "limits": limits}


# --- OHLCV conversion ------------------------------------------------------


def test_get_bars_converts_ohlcv_to_standard_dataframe():
    broker, fake = make_broker()
    fake.ohlcv_map[SYMBOL] = [
        [1700000000000, 100.0, 101.0, 99.0, 100.5, 10.0],
        [1700000060000, 100.5, 102.0, 100.0, 101.5, 15.0],
    ]

    df = broker.get_bars(SYMBOL, "M1", 2)

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [100.5, 101.5]
    assert df["volume"].tolist() == [10.0, 15.0]
    assert pd.api.types.is_datetime64_any_dtype(df["time"])


def test_get_bars_returns_empty_dataframe_when_no_ohlcv():
    broker, fake = make_broker()
    df = broker.get_bars(SYMBOL, "M15", 10)
    assert df.empty
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]


# --- Precision / minimum quantity / minimum notional -----------------------


def test_normalize_price_uses_exchange_precision():
    broker, fake = make_broker()
    fake.markets_data[SYMBOL] = make_market(price_digits=2)
    assert broker._normalize_price(SYMBOL, 123.4567) == pytest.approx(123.46)


def test_normalize_quantity_bumps_to_minimum_amount():
    broker, fake = make_broker()
    fake.markets_data[SYMBOL] = make_market(min_amount=0.01)
    assert broker._normalize_quantity(SYMBOL, 0.001, price=100.0) == pytest.approx(0.01)


def test_normalize_quantity_bumps_for_minimum_notional():
    broker, fake = make_broker()
    fake.markets_data[SYMBOL] = make_market(min_amount=0.0001, min_cost=10.0)
    # price=100 -> need qty >= 0.1 to reach the $10 minimum notional
    result = broker._normalize_quantity(SYMBOL, 0.001, price=100.0)
    assert result == pytest.approx(0.1)


def test_normalize_quantity_unaffected_when_above_minimums():
    broker, fake = make_broker()
    fake.markets_data[SYMBOL] = make_market(min_amount=0.001, min_cost=1.0)
    assert broker._normalize_quantity(SYMBOL, 0.05, price=100.0) == pytest.approx(0.05)


# --- Live-trading safety gate ------------------------------------------


def test_open_market_order_blocked_and_simulated_when_allow_live_trading_false():
    broker, fake = make_broker(allow_live_trading=False)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}

    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    assert position is not None
    assert position.side is Side.BUY
    assert fake.created_orders == []  # create_order was never actually called
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 1


def test_open_market_order_rejects_second_position():
    broker, fake = make_broker(allow_live_trading=False)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}
    broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    with pytest.raises(RuntimeError, match="one-position-only"):
        broker.open_market_order(SYMBOL, Side.SELL, quantity=0.01, stop_loss=105.0)


def test_simulated_close_records_trade_and_frees_slot():
    broker, fake = make_broker(allow_live_trading=False)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    fake.tickers[SYMBOL] = {"last": 110.0}  # price moved up
    broker.close_position(position.position_id, CloseReason.TP_CASH)

    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0
    assert broker.get_today_realized_pnl() > 0


def test_simulated_modify_stop_loss_updates_position():
    broker, fake = make_broker(allow_live_trading=False)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    broker.modify_stop_loss(position.position_id, 100.0)

    updated = broker.get_open_position(SYMBOL, STRATEGY_ID)
    assert updated.stop_loss == pytest.approx(100.0)


# --- Live path: entry + reduce-only stop/close orders ----------------------


def test_open_market_order_creates_entry_and_stop_orders_when_live():
    broker, fake = make_broker(allow_live_trading=True)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}

    # Simulate the position actually appearing only once the entry order is placed -- not
    # before, or the pre-flight one-position-only check would see it as already open.
    original_create_order = fake.create_order

    def create_order_then_open_position(symbol, type_, side, amount, price=None, params=None):
        order = original_create_order(symbol, type_, side, amount, price=price, params=params)
        if type_ == "market":
            fake.positions.append(
                {"symbol": symbol, "side": "long", "contracts": amount, "entryPrice": 100.0,
                 "unrealizedPnl": 0.0, "timestamp": 1700000000000}
            )
        return order

    fake.create_order = create_order_then_open_position

    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    assert position.position_id == f"{SYMBOL}:long"
    assert len(fake.created_orders) == 2
    assert fake.created_orders[0]["type"] == "market"
    assert fake.created_orders[0]["side"] == "buy"
    assert fake.created_orders[1]["type"] == "STOP_MARKET"
    assert fake.created_orders[1]["side"] == "sell"
    assert fake.created_orders[1]["params"]["reduceOnly"] is True


def test_close_position_creates_reduce_only_order_when_live():
    broker, fake = make_broker(allow_live_trading=True)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}
    fake.positions = [
        {"symbol": SYMBOL, "side": "long", "contracts": 0.01, "entryPrice": 100.0,
         "unrealizedPnl": 1.0, "timestamp": 1700000000000},
    ]

    broker.close_position(f"{SYMBOL}:long", CloseReason.TP_CASH)

    reduce_only_orders = [o for o in fake.created_orders if o["params"].get("reduceOnly")]
    assert len(reduce_only_orders) == 1
    assert reduce_only_orders[0]["side"] == "sell"
    assert reduce_only_orders[0]["amount"] == pytest.approx(0.01)


def test_get_open_position_only_returns_matching_symbol():
    broker, fake = make_broker(allow_live_trading=True)
    fake.positions = [
        {"symbol": "ETH/USDT", "side": "long", "contracts": 1.0, "entryPrice": 2000.0,
         "unrealizedPnl": 0.0, "timestamp": 1700000000000},
    ]
    assert broker.get_open_position(SYMBOL, STRATEGY_ID) is None
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0


# --- Order/stop-loss atomicity on failure --------------------------------


def test_open_market_order_closes_position_when_stop_placement_fails():
    """The entry fills, but the protective STOP_MARKET order fails -- rather than leaving
    a naked position, open_market_order() must compensate with an immediate reduce-only
    close and always raise (never return as if the entry succeeded).
    """
    broker, fake = make_broker(allow_live_trading=True)
    fake.markets_data[SYMBOL] = make_market()
    fake.tickers[SYMBOL] = {"last": 100.0}

    original_create_order = fake.create_order

    def entry_ok_stop_fails(symbol, type_, side, amount, price=None, params=None):
        if type_ == "STOP_MARKET":
            raise RuntimeError("exchange rejected stop order")
        return original_create_order(symbol, type_, side, amount, price=price, params=params)

    fake.create_order = entry_ok_stop_fails

    with pytest.raises(RuntimeError, match="stop-loss placement failed"):
        broker.open_market_order(SYMBOL, Side.BUY, quantity=0.01, stop_loss=95.0)

    order_types = [o["type"] for o in fake.created_orders]
    assert "STOP_MARKET" not in order_types
    assert order_types.count("market") == 2  # the entry, plus a compensating close

    compensating_close = fake.created_orders[-1]
    assert compensating_close["side"] == "sell"  # opposite of the BUY entry
    assert compensating_close["params"]["reduceOnly"] is True
    assert compensating_close["amount"] == pytest.approx(0.01)


def test_modify_stop_loss_keeps_old_stop_when_new_stop_placement_fails():
    """If placing the updated stop fails, the previous stop-loss order must NOT be
    cancelled -- the position stays protected at its old level rather than naked.
    """
    broker, fake = make_broker(allow_live_trading=True)
    fake.markets_data[SYMBOL] = make_market()
    fake.positions = [
        {"symbol": SYMBOL, "side": "long", "contracts": 0.01, "entryPrice": 100.0,
         "unrealizedPnl": 0.0, "timestamp": 1700000000000},
    ]
    broker._stop_order_id_by_symbol[SYMBOL] = "old-stop-order-id"
    broker._stop_loss_by_symbol[SYMBOL] = 95.0

    def always_fails(symbol, type_, side, amount, price=None, params=None):
        raise RuntimeError("exchange rejected new stop order")

    fake.create_order = always_fails

    with pytest.raises(RuntimeError, match="exchange rejected new stop order"):
        broker.modify_stop_loss(f"{SYMBOL}:long", 98.0)

    assert fake.canceled_orders == []  # the old, still-valid stop was never touched
    assert broker._stop_order_id_by_symbol[SYMBOL] == "old-stop-order-id"
    assert broker._stop_loss_by_symbol[SYMBOL] == 95.0


def test_modify_stop_loss_creates_new_stop_before_cancelling_old():
    """On the success path, the new stop must be live on the exchange before the old one
    is cancelled -- never the other way around.
    """
    broker, fake = make_broker(allow_live_trading=True)
    fake.markets_data[SYMBOL] = make_market()
    fake.positions = [
        {"symbol": SYMBOL, "side": "long", "contracts": 0.01, "entryPrice": 100.0,
         "unrealizedPnl": 0.0, "timestamp": 1700000000000},
    ]
    broker._stop_order_id_by_symbol[SYMBOL] = "old-stop-order-id"

    call_order = []
    original_create_order, original_cancel_order = fake.create_order, fake.cancel_order

    def tracking_create_order(symbol, type_, side, amount, price=None, params=None):
        call_order.append("create")
        return original_create_order(symbol, type_, side, amount, price=price, params=params)

    def tracking_cancel_order(order_id, symbol):
        call_order.append("cancel")
        return original_cancel_order(order_id, symbol)

    fake.create_order = tracking_create_order
    fake.cancel_order = tracking_cancel_order

    broker.modify_stop_loss(f"{SYMBOL}:long", 98.0)

    assert call_order == ["create", "cancel"]
    assert fake.canceled_orders == [("old-stop-order-id", SYMBOL)]


# --- Fee estimate ----------------------------------------------------------


def test_estimate_fee_cash():
    broker, fake = make_broker(fee_rate_estimate=0.0004)
    fee = broker.estimate_fee_cash(quantity=0.01, price=100.0)
    assert fee == pytest.approx(2 * 0.0004 * 0.01 * 100.0)


def test_is_cost_too_high_for_target():
    broker, fake = make_broker(fee_rate_estimate=0.01, max_cost_ratio_to_tp=0.1)
    assert broker.is_cost_too_high_for_target(quantity=1.0, price=100.0, tp_cash=1.5) is True
    assert broker.is_cost_too_high_for_target(quantity=0.0001, price=100.0, tp_cash=1.5) is False


def test_get_trading_cost_is_fee_based_price_equivalent():
    broker, fake = make_broker(fee_rate_estimate=0.0004)
    fake.tickers[SYMBOL] = {"last": 100.0}
    assert broker.get_trading_cost(SYMBOL) == pytest.approx(2 * 0.0004 * 100.0)


# --- Equity ------------------------------------------------------------


def test_get_account_equity_reads_usdt_total():
    broker, fake = make_broker()
    fake.balance = {"USDT": {"total": 543.21}}
    assert broker.get_account_equity() == pytest.approx(543.21)
