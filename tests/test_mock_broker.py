"""Unit tests for MockBroker: no credentials/network needed, simulates one position at a time."""

import pandas as pd
import pytest

from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.models import CloseReason, Side

SYMBOL = "EURUSD"


def make_m1_bars(closes: list[float]) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=len(closes), freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        }
    )


def make_broker(closes: list[float]) -> MockBroker:
    broker = MockBroker(symbol=SYMBOL, strategy_id="strat-1")
    broker.set_bars("M1", make_m1_bars(closes))
    return broker


# --- get_bars ------------------------------------------------------------


def test_get_bars_returns_injected_data_respecting_limit():
    broker = make_broker([100.0, 101.0, 102.0, 103.0])
    result = broker.get_bars(SYMBOL, "M1", limit=2)
    assert len(result) == 2
    assert result["close"].tolist() == [102.0, 103.0]


def test_get_bars_wrong_symbol_raises():
    broker = make_broker([100.0])
    with pytest.raises(ValueError, match=SYMBOL):
        broker.get_bars("BTCUSDT", "M1", limit=1)


def test_get_bars_unset_timeframe_returns_empty_dataframe():
    broker = make_broker([100.0])
    result = broker.get_bars(SYMBOL, "M5", limit=10)
    assert result.empty
    assert list(result.columns) == ["time", "open", "high", "low", "close", "volume"]


# --- open / close / modify --------------------------------------------


def test_open_market_order_creates_position_at_latest_price():
    broker = make_broker([100.0, 101.0, 102.0])
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=2.0, stop_loss=99.0)
    assert position.entry_price == 102.0
    assert position.quantity == 2.0
    assert broker.get_position_count(SYMBOL, "strat-1") == 1


def test_open_market_order_rejects_second_position():
    broker = make_broker([100.0])
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    with pytest.raises(RuntimeError, match="one-position-only"):
        broker.open_market_order(SYMBOL, Side.SELL, quantity=1.0, stop_loss=101.0)


def test_modify_stop_loss_updates_open_position():
    broker = make_broker([100.0])
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    broker.modify_stop_loss(position.position_id, 99.5)
    updated = broker.get_open_position(SYMBOL, "strat-1")
    assert updated.stop_loss == 99.5
    assert any(o["type"] == "MODIFY_SL" and o["new_stop_loss"] == 99.5 for o in broker.get_order_log())


def test_modify_stop_loss_unknown_position_id_raises():
    broker = make_broker([100.0])
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    with pytest.raises(ValueError, match="no open position"):
        broker.modify_stop_loss("does-not-exist", 99.5)


def test_close_position_records_trade_history_and_frees_slot():
    broker = make_broker([100.0])
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    broker.set_bars("M1", make_m1_bars([100.0, 101.5]))  # price moved up before close

    broker.close_position(position.position_id, CloseReason.TP_CASH)

    assert broker.get_position_count(SYMBOL, "strat-1") == 0
    history = broker.get_trade_history()
    assert len(history) == 1
    assert history[0].reason is CloseReason.TP_CASH
    assert history[0].exit_price == 101.5
    assert history[0].realized_pnl_cash == pytest.approx(1.5)


def test_close_position_unknown_id_raises():
    broker = make_broker([100.0])
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    with pytest.raises(ValueError, match="no open position"):
        broker.close_position("does-not-exist", CloseReason.MANUAL)


# --- PnL, cost, equity ---------------------------------------------------


def test_get_unrealized_pnl_buy_and_sell():
    broker = make_broker([100.0])
    buy = broker.open_market_order(SYMBOL, Side.BUY, quantity=2.0, stop_loss=95.0)
    broker.set_bars("M1", make_m1_bars([100.0, 103.0]))
    assert broker.get_unrealized_pnl(buy) == pytest.approx(6.0)  # (103-100)*2

    broker2 = make_broker([100.0])
    sell = broker2.open_market_order(SYMBOL, Side.SELL, quantity=2.0, stop_loss=105.0)
    broker2.set_bars("M1", make_m1_bars([100.0, 97.0]))
    assert broker2.get_unrealized_pnl(sell) == pytest.approx(6.0)  # (100-97)*2


def test_get_trading_cost_returns_configured_value():
    broker = MockBroker(symbol=SYMBOL, trading_cost=0.05)
    assert broker.get_trading_cost(SYMBOL) == 0.05


def test_get_today_realized_pnl_sums_closed_trades():
    broker = make_broker([100.0])
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    broker.set_bars("M1", make_m1_bars([100.0, 101.0]))
    broker.close_position(position.position_id, CloseReason.TP_CASH)
    assert broker.get_today_realized_pnl() == pytest.approx(1.0)


def test_get_account_equity_reflects_realized_pnl():
    broker = MockBroker(symbol=SYMBOL, starting_equity=1000.0)
    broker.set_bars("M1", make_m1_bars([100.0]))
    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=99.0)
    broker.set_bars("M1", make_m1_bars([100.0, 102.0]))
    broker.close_position(position.position_id, CloseReason.TP_CASH)
    assert broker.get_account_equity() == pytest.approx(1002.0)
