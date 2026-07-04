"""Unit tests for the M1 entry signal trigger and the risk manager's stop-loss calculation."""

import pandas as pd
import pytest

from trend_only_scalper.models import Side, Trend
from trend_only_scalper.risk.risk_manager import calculate_stop_loss
from trend_only_scalper.strategy.entry_signal import detect_entry_signal


def make_m1_df(open_, high, low, close, ema=100.0, vwap=100.0, atr=1.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [open_],
            "high": [high],
            "low": [low],
            "close": [close],
            "ema_20": [ema],
            "vwap": [vwap],
            "atr_14": [atr],
        }
    )


# --- M1 buy / sell signal detection --------------------------------------


def test_detect_m1_buy_signal():
    # pullback: low=99.9 near ema=100 (within 0.25 tol); bullish close>open; close(100.2)>ema(100)
    df = make_m1_df(open_=99.85, high=100.3, low=99.9, close=100.2, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.UP, spread_or_cost=0.1)
    assert signal is not None
    assert signal.side is Side.BUY


def test_detect_m1_sell_signal():
    # rebound: high=100.05 near ema=100; bearish close<open; close(99.7)<ema(100)
    df = make_m1_df(open_=100.15, high=100.05, low=99.6, close=99.7, ema=100.0, vwap=100.2, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.DOWN, confirm_m5=Trend.DOWN, spread_or_cost=0.1)
    assert signal is not None
    assert signal.side is Side.SELL


def test_no_signal_when_m15_and_m5_disagree():
    df = make_m1_df(open_=99.85, high=100.3, low=99.9, close=100.2, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.DOWN, spread_or_cost=0.1)
    assert signal is None


def test_no_signal_when_trend_is_none():
    df = make_m1_df(open_=99.85, high=100.3, low=99.9, close=100.2, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.NONE, confirm_m5=Trend.NONE, spread_or_cost=0.1)
    assert signal is None


def test_no_signal_when_candle_is_abnormally_large():
    # range = 10.4, atr=1.0, abnormal_candle_atr_multiple default 2.0 -> threshold 2.0
    df = make_m1_df(open_=99.85, high=105.0, low=94.6, close=100.2, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.UP, spread_or_cost=0.1)
    assert signal is None


def test_no_signal_when_atr_too_low_versus_spread():
    # atr=1.0, spread_or_cost=1.0, min_atr_spread_multiple default 3.0 -> needs atr >= 3.0
    df = make_m1_df(open_=99.85, high=100.3, low=99.9, close=100.2, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.UP, spread_or_cost=1.0)
    assert signal is None


def test_no_signal_when_pullback_condition_not_met():
    # low is far from both ema and vwap -- no pullback -- even though candle is bullish
    df = make_m1_df(open_=101.5, high=103.0, low=101.4, close=102.5, ema=100.0, vwap=99.8, atr=1.0)
    signal = detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.UP, spread_or_cost=0.1)
    assert signal is None


def test_entry_signal_missing_column_raises_clear_error():
    df = make_m1_df(open_=99.85, high=100.3, low=99.9, close=100.2).drop(columns=["atr_14"])
    with pytest.raises(ValueError, match="atr_14"):
        detect_entry_signal(df, trend_m15=Trend.UP, confirm_m5=Trend.UP, spread_or_cost=0.1)


# --- Risk manager: stop-loss calculation ----------------------------------


def make_swing_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.5, 102.5, 101.5],
            "low": [99.0, 98.5, 97.0, 98.0, 99.5],
            "atr_14": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )


def test_calculate_buy_stop_loss():
    df = make_swing_df()
    # swing low over lookback = 97.0, buffer = atr(1.0) * sl_atr_buffer(0.5) = 0.5
    stop_loss = calculate_stop_loss(df, side=Side.BUY, swing_lookback=5, sl_atr_buffer=0.5)
    assert stop_loss == pytest.approx(97.0 - 0.5)


def test_calculate_sell_stop_loss():
    df = make_swing_df()
    # swing high over lookback = 103.5, buffer = atr(1.0) * sl_atr_buffer(0.5) = 0.5
    stop_loss = calculate_stop_loss(df, side=Side.SELL, swing_lookback=5, sl_atr_buffer=0.5)
    assert stop_loss == pytest.approx(103.5 + 0.5)


def test_calculate_stop_loss_respects_swing_lookback_window():
    df = make_swing_df()
    # last 2 bars only: low=[98.0, 99.5] -> swing low = 98.0
    stop_loss = calculate_stop_loss(df, side=Side.BUY, swing_lookback=2, sl_atr_buffer=0.5)
    assert stop_loss == pytest.approx(98.0 - 0.5)


def test_calculate_stop_loss_none_on_empty_dataframe():
    df = pd.DataFrame(columns=["high", "low", "atr_14"])
    assert calculate_stop_loss(df, side=Side.BUY) is None


def test_calculate_stop_loss_missing_column_raises_clear_error():
    df = make_swing_df().drop(columns=["atr_14"])
    with pytest.raises(ValueError, match="atr_14"):
        calculate_stop_loss(df, side=Side.BUY)
