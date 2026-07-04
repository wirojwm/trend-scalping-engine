"""Unit tests for the DataFrame-based indicator functions: add_ema, add_macd, add_atr, add_vwap."""

import pandas as pd
import pytest

from trend_only_scalper.indicators import add_atr, add_ema, add_macd, add_vwap


def make_ohlcv(rows: int, start: str = "2026-01-01 00:00", freq: str = "1min") -> pd.DataFrame:
    """A small, deterministic OHLCV DataFrame for indicator tests."""
    times = pd.date_range(start=start, periods=rows, freq=freq)
    close = [100.0 + i * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "time": times,
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [100.0] * rows,
        }
    )


# --- add_ema -------------------------------------------------------------


def test_add_ema_creates_columns():
    df = make_ohlcv(60)
    result = add_ema(df, fast_period=20, slow_period=50)
    assert "ema_20" in result.columns
    assert "ema_50" in result.columns


def test_add_ema_does_not_mutate_original():
    df = make_ohlcv(60)
    original_columns = list(df.columns)
    add_ema(df, fast_period=20, slow_period=50)
    assert list(df.columns) == original_columns


def test_add_ema_missing_column_raises_clear_error():
    df = make_ohlcv(10).drop(columns=["close"])
    with pytest.raises(ValueError, match="close"):
        add_ema(df)


def test_add_ema_handles_small_dataframe_safely():
    df = make_ohlcv(3)
    result = add_ema(df, fast_period=20, slow_period=50)
    assert result["ema_20"].isna().all()
    assert len(result) == 3


# --- add_macd --------------------------------------------------------------


def test_add_macd_creates_columns():
    df = make_ohlcv(80)
    result = add_macd(df)
    assert {"macd", "macd_signal", "macd_hist"}.issubset(result.columns)


def test_add_macd_does_not_mutate_original():
    df = make_ohlcv(80)
    original_columns = list(df.columns)
    add_macd(df)
    assert list(df.columns) == original_columns


def test_add_macd_missing_column_raises_clear_error():
    df = make_ohlcv(10).drop(columns=["close"])
    with pytest.raises(ValueError, match="close"):
        add_macd(df)


def test_add_macd_positive_histogram_for_uptrend():
    df = make_ohlcv(80)  # steadily rising close
    result = add_macd(df, fast_period=12, slow_period=26, signal_period=9)
    assert result["macd_hist"].iloc[-1] > 0


def test_add_macd_handles_small_dataframe_safely():
    df = make_ohlcv(3)
    result = add_macd(df)
    assert result["macd_hist"].isna().all()
    assert len(result) == 3


# --- add_atr -------------------------------------------------------------


def test_add_atr_creates_column():
    df = make_ohlcv(30)
    result = add_atr(df, period=14)
    assert "atr_14" in result.columns


def test_add_atr_does_not_mutate_original():
    df = make_ohlcv(30)
    original_columns = list(df.columns)
    add_atr(df, period=14)
    assert list(df.columns) == original_columns


def test_add_atr_missing_column_raises_clear_error():
    df = make_ohlcv(10).drop(columns=["high"])
    with pytest.raises(ValueError, match="high"):
        add_atr(df)


def test_add_atr_constant_range_converges_to_range():
    rows = 10
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=rows, freq="1min"),
            "open": [10.0] * rows,
            "high": [12.0] * rows,
            "low": [10.0] * rows,
            "close": [11.0] * rows,
            "volume": [100.0] * rows,
        }
    )
    result = add_atr(df, period=3)
    non_nan = result["atr_3"].dropna()
    assert len(non_nan) > 0
    assert all(v == pytest.approx(2.0) for v in non_nan)


def test_add_atr_handles_small_dataframe_safely():
    df = make_ohlcv(2)
    result = add_atr(df, period=14)
    assert result["atr_14"].isna().all()
    assert len(result) == 2


# --- add_vwap --------------------------------------------------------------


def test_add_vwap_creates_column():
    df = make_ohlcv(5)
    result = add_vwap(df)
    assert "vwap" in result.columns


def test_add_vwap_does_not_mutate_original():
    df = make_ohlcv(5)
    original_columns = list(df.columns)
    add_vwap(df)
    assert list(df.columns) == original_columns


def test_add_vwap_missing_column_raises_clear_error():
    df = make_ohlcv(5).drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        add_vwap(df)


def test_add_vwap_cumulative_math():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=3, freq="1h"),
            "open": [9.0, 9.0, 10.0],
            "high": [10.0, 11.0, 12.0],
            "low": [8.0, 9.0, 10.0],
            "close": [9.0, 10.0, 11.0],
            "volume": [100.0, 200.0, 100.0],
        }
    )
    result = add_vwap(df)
    # typical prices: 9.0, 10.0, 11.0 -> pv: 900, 2000, 1100
    assert result["vwap"].iloc[0] == pytest.approx(900 / 100)
    assert result["vwap"].iloc[1] == pytest.approx(2900 / 300)
    assert result["vwap"].iloc[2] == pytest.approx(4000 / 400)


def test_add_vwap_resets_by_date():
    df = pd.DataFrame(
        {
            "time": [
                pd.Timestamp("2026-01-01 00:00"),
                pd.Timestamp("2026-01-01 01:00"),
                pd.Timestamp("2026-01-02 00:00"),
            ],
            "open": [9.0, 9.0, 20.0],
            "high": [10.0, 11.0, 22.0],
            "low": [8.0, 9.0, 20.0],
            "close": [9.0, 10.0, 21.0],
            "volume": [100.0, 200.0, 50.0],
        }
    )
    result = add_vwap(df)
    # day 2 has a single bar -> vwap resets to that bar's own typical price (21.0)
    assert result["vwap"].iloc[2] == pytest.approx(21.0)


def test_add_vwap_handles_zero_volume_safely():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=2, freq="1h"),
            "open": [9.0, 9.0],
            "high": [10.0, 10.0],
            "low": [8.0, 8.0],
            "close": [9.0, 9.0],
            "volume": [0.0, 0.0],
        }
    )
    result = add_vwap(df)
    assert result["vwap"].isna().all()


def test_add_vwap_handles_missing_volume_values_safely():
    df = make_ohlcv(3)
    df.loc[1, "volume"] = None
    result = add_vwap(df)
    assert not result["vwap"].isna().any()
