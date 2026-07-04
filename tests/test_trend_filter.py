"""Unit tests for the M15 trend filter and M5 confirmation filter."""

import pandas as pd
import pytest

from trend_only_scalper.models import Trend
from trend_only_scalper.strategy.confirmation_filter import confirm_trend
from trend_only_scalper.strategy.trend_filter import detect_trend


def make_trend_df(close, ema_fast, ema_slow, macd_hist) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [close],
            "ema_20": [ema_fast],
            "ema_50": [ema_slow],
            "macd_hist": [macd_hist],
        }
    )


def make_confirm_df(close, ema_fast, ema_slow, macd_hist, vwap) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [close],
            "ema_20": [ema_fast],
            "ema_50": [ema_slow],
            "macd_hist": [macd_hist],
            "vwap": [vwap],
        }
    )


# --- M15 trend filter ------------------------------------------------------


def test_detect_trend_up():
    df = make_trend_df(close=110, ema_fast=108, ema_slow=105, macd_hist=0.5)
    assert detect_trend(df) is Trend.UP


def test_detect_trend_down():
    df = make_trend_df(close=95, ema_fast=97, ema_slow=100, macd_hist=-0.5)
    assert detect_trend(df) is Trend.DOWN


def test_detect_trend_none_when_conditions_conflict():
    # close above ema_slow but ema_fast below ema_slow -- no agreement
    df = make_trend_df(close=110, ema_fast=99, ema_slow=105, macd_hist=0.5)
    assert detect_trend(df) is Trend.NONE


def test_detect_trend_none_when_macd_disagrees():
    df = make_trend_df(close=110, ema_fast=108, ema_slow=105, macd_hist=-0.1)
    assert detect_trend(df) is Trend.NONE


def test_detect_trend_none_on_empty_dataframe():
    df = pd.DataFrame(columns=["close", "ema_20", "ema_50", "macd_hist"])
    assert detect_trend(df) is Trend.NONE


def test_detect_trend_missing_column_raises_clear_error():
    df = pd.DataFrame({"close": [110], "ema_20": [108], "ema_50": [105]})
    with pytest.raises(ValueError, match="macd_hist"):
        detect_trend(df)


# --- M5 confirmation filter --------------------------------------------


def test_confirm_trend_up():
    df = make_confirm_df(close=110, ema_fast=108, ema_slow=105, macd_hist=0.5, vwap=109)
    assert confirm_trend(df) is Trend.UP


def test_confirm_trend_down():
    df = make_confirm_df(close=95, ema_fast=97, ema_slow=100, macd_hist=-0.5, vwap=96)
    assert confirm_trend(df) is Trend.DOWN


def test_confirm_trend_none_when_vwap_disagrees():
    # everything else says UP, but close is below vwap -- no confirmation
    df = make_confirm_df(close=110, ema_fast=108, ema_slow=105, macd_hist=0.5, vwap=115)
    assert confirm_trend(df) is Trend.NONE


def test_confirm_trend_none_on_empty_dataframe():
    df = pd.DataFrame(columns=["close", "ema_20", "ema_50", "macd_hist", "vwap"])
    assert confirm_trend(df) is Trend.NONE
