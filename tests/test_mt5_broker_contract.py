"""Contract-style tests for MT5Broker using a fully faked MetaTrader5 module.

No real MT5 terminal or the actual MetaTrader5 package behavior is required -- these
tests only verify OUR adapter logic (DataFrame conversion, magic-number filtering,
price/volume normalization, filling-mode fallback, and the live-trading safety gate),
since MT5 itself may not be available in CI.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trend_only_scalper.brokers.mt5_broker import MT5Broker
from trend_only_scalper.config import MT5Config
from trend_only_scalper.models import CloseReason, Side

SYMBOL = "EURUSD"
MAGIC = 987001


class FakeSymbolInfo:
    def __init__(self, digits=5, volume_min=0.01, volume_step=0.01, volume_max=100.0):
        self.digits = digits
        self.volume_min = volume_min
        self.volume_step = volume_step
        self.volume_max = volume_max


class FakePosition:
    def __init__(self, ticket, symbol, volume, price_open, sl, tp, type_, magic, profit, time_):
        self.ticket = ticket
        self.symbol = symbol
        self.volume = volume
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.type = type_
        self.magic = magic
        self.profit = profit
        self.time = time_


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 module: just the surface MT5Broker uses."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3
    TRADE_RETCODE_DONE = 10009
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15

    def __init__(self):
        self.symbol_info_map: dict[str, FakeSymbolInfo] = {}
        self.tick_map: dict[str, SimpleNamespace] = {}
        self.positions: list[FakePosition] = []
        self.rates_map: dict[str, np.ndarray] = {}
        self.order_send_results: list[SimpleNamespace] = []
        self.sent_requests: list[dict] = []
        self.account = SimpleNamespace(equity=10_000.0)
        self.initialize_result = True

    def initialize(self, **kwargs):
        return self.initialize_result

    def last_error(self):
        return (1, "fake error")

    def shutdown(self):
        pass

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info(self, symbol):
        return self.symbol_info_map.get(symbol)

    def symbol_info_tick(self, symbol):
        return self.tick_map.get(symbol)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self.rates_map.get(symbol)

    def positions_get(self, symbol=None, ticket=None):
        results = self.positions
        if symbol is not None:
            results = [p for p in results if p.symbol == symbol]
        if ticket is not None:
            results = [p for p in results if p.ticket == ticket]
        return tuple(results)

    def order_send(self, request):
        self.sent_requests.append(request)
        if self.order_send_results:
            return self.order_send_results.pop(0)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="ok")

    def account_info(self):
        return self.account

    def history_deals_get(self, date_from, date_to):
        return ()


def make_rates(times, opens, highs, lows, closes, tick_volumes) -> np.ndarray:
    dtype = [
        ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
        ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8"),
    ]
    arr = np.zeros(len(times), dtype=dtype)
    arr["time"], arr["open"], arr["high"] = times, opens, highs
    arr["low"], arr["close"], arr["tick_volume"] = lows, closes, tick_volumes
    return arr


def make_broker(allow_live_trading: bool = False, **overrides) -> tuple[MT5Broker, FakeMT5]:
    fake = FakeMT5()
    cfg = MT5Config(symbol=SYMBOL, magic=MAGIC, allow_live_trading=allow_live_trading, **overrides)
    broker = MT5Broker(cfg, strategy_id="trend_only_scalper", mt5_module=fake)
    return broker, fake


# --- DataFrame conversion --------------------------------------------------


def test_get_bars_converts_mt5_rates_to_standard_dataframe():
    broker, fake = make_broker()
    fake.rates_map[SYMBOL] = make_rates(
        times=[1700000000, 1700000060],
        opens=[1.10, 1.11],
        highs=[1.12, 1.13],
        lows=[1.09, 1.10],
        closes=[1.115, 1.125],
        tick_volumes=[100, 150],
    )

    df = broker.get_bars(SYMBOL, "M1", 2)

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [1.115, 1.125]
    assert df["volume"].tolist() == [100, 150]
    assert pd.api.types.is_datetime64_any_dtype(df["time"])


def test_get_bars_returns_empty_dataframe_when_no_rates():
    broker, fake = make_broker()
    df = broker.get_bars(SYMBOL, "M5", 10)
    assert df.empty
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]


# --- Position filtering by magic number -------------------------------


def test_get_open_position_filters_by_magic_number():
    broker, fake = make_broker(allow_live_trading=True)
    fake.positions = [
        FakePosition(1, SYMBOL, 0.10, 1.10, 1.09, 0.0, 0, MAGIC, 5.0, 1700000000),
        FakePosition(2, SYMBOL, 0.20, 1.20, 1.19, 0.0, 1, 555555, -3.0, 1700000100),
    ]

    position = broker.get_open_position(SYMBOL, "trend_only_scalper")

    assert position is not None
    assert position.position_id == "1"
    assert position.side is Side.BUY
    assert broker.get_position_count(SYMBOL, "trend_only_scalper") == 1


def test_positions_with_different_magic_numbers_are_ignored_entirely():
    broker, fake = make_broker(allow_live_trading=True)
    fake.positions = [
        FakePosition(99, SYMBOL, 0.5, 1.30, 1.29, 0.0, 0, 555555, 2.0, 1700000000),
    ]

    assert broker.get_open_position(SYMBOL, "trend_only_scalper") is None
    assert broker.get_position_count(SYMBOL, "trend_only_scalper") == 0


def test_close_position_refuses_foreign_magic_number():
    broker, fake = make_broker(allow_live_trading=True)
    fake.positions = [
        FakePosition(42, SYMBOL, 0.1, 1.10, 1.09, 0.0, 0, 555555, 1.0, 1700000000),
    ]

    with pytest.raises(ValueError, match="magic"):
        broker.close_position("42", CloseReason.MANUAL)


# --- Volume / price normalization ------------------------------------


def test_normalize_volume_rounds_to_step_and_clamps():
    broker, fake = make_broker()
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo(volume_min=0.01, volume_step=0.01, volume_max=1.0)

    assert broker._normalize_volume(SYMBOL, 0.123) == pytest.approx(0.12)
    assert broker._normalize_volume(SYMBOL, 5.0) == pytest.approx(1.0)   # clamped to max
    assert broker._normalize_volume(SYMBOL, 0.001) == pytest.approx(0.01)  # clamped to min


def test_normalize_price_rounds_to_symbol_digits():
    broker, fake = make_broker()
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo(digits=3)

    assert broker._normalize_price(SYMBOL, 1.123456) == pytest.approx(1.123)


# --- Live trading safety gate ------------------------------------------


def test_open_market_order_blocked_and_simulated_when_allow_live_trading_false():
    broker, fake = make_broker(allow_live_trading=False)
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)

    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.095)

    assert position is not None
    assert position.side is Side.BUY
    assert fake.sent_requests == []  # order_send was never actually called
    assert broker.get_position_count(SYMBOL, "trend_only_scalper") == 1


def test_open_market_order_rejects_second_position_when_simulated():
    broker, fake = make_broker(allow_live_trading=False)
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)
    broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.095)

    with pytest.raises(RuntimeError, match="one-position-only"):
        broker.open_market_order(SYMBOL, Side.SELL, quantity=0.1, stop_loss=1.11)


def test_simulated_close_records_trade_and_frees_slot():
    broker, fake = make_broker(allow_live_trading=False)
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)
    broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.095)

    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.12, ask=1.121)  # price moved up
    broker.close_position("mt5-sim-1", CloseReason.TP_CASH)

    assert broker.get_position_count(SYMBOL, "trend_only_scalper") == 0
    assert broker.get_today_realized_pnl() > 0


def test_simulated_modify_stop_loss_updates_position():
    broker, fake = make_broker(allow_live_trading=False)
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.095)

    broker.modify_stop_loss(position.position_id, 1.10)

    updated = broker.get_open_position(SYMBOL, "trend_only_scalper")
    assert updated.stop_loss == 1.10


# --- Order filling mode fallback ----------------------------------------


def test_open_market_order_falls_back_through_filling_modes_when_live():
    broker, fake = make_broker(allow_live_trading=True, filling_type="IOC")
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)
    fake.order_send_results = [
        SimpleNamespace(retcode=10030, comment="unsupported filling mode"),  # IOC rejected
        SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE, comment="ok"),      # FOK accepted
    ]

    # Simulate the position actually appearing only once order_send succeeds -- not before,
    # or the pre-flight one-position-only check would see it as already open.
    original_order_send = fake.order_send

    def order_send_then_open_position(request):
        result = original_order_send(request)
        if result.retcode == fake.TRADE_RETCODE_DONE:
            fake.positions.append(FakePosition(7, SYMBOL, 0.1, 1.101, 1.09, 0.0, 0, MAGIC, 0.0, 1700000000))
        return result

    fake.order_send = order_send_then_open_position

    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.09)

    assert position.position_id == "7"
    assert len(fake.sent_requests) == 2
    assert fake.sent_requests[0]["type_filling"] == fake.ORDER_FILLING_IOC
    assert fake.sent_requests[1]["type_filling"] == fake.ORDER_FILLING_FOK


def test_open_market_order_raises_when_all_filling_modes_rejected():
    broker, fake = make_broker(allow_live_trading=True, filling_type="IOC")
    fake.symbol_info_map[SYMBOL] = FakeSymbolInfo()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.10, ask=1.101)
    fake.order_send_results = [
        SimpleNamespace(retcode=10030, comment="bad"),
        SimpleNamespace(retcode=10030, comment="bad"),
        SimpleNamespace(retcode=10030, comment="bad"),
    ]

    with pytest.raises(RuntimeError, match="failed after trying all filling modes"):
        broker.open_market_order(SYMBOL, Side.BUY, quantity=0.1, stop_loss=1.09)

    assert len(fake.sent_requests) == 3  # tried IOC, FOK, RETURN


# --- Equity / trading cost -------------------------------------------------


def test_get_account_equity_reads_from_account_info():
    broker, fake = make_broker()
    fake.account = SimpleNamespace(equity=5432.10)
    assert broker.get_account_equity() == pytest.approx(5432.10)


def test_get_trading_cost_is_ask_minus_bid():
    broker, fake = make_broker()
    fake.tick_map[SYMBOL] = SimpleNamespace(bid=1.1000, ask=1.1002)
    assert broker.get_trading_cost(SYMBOL) == pytest.approx(0.0002)
