"""Pure indicator calculations on OHLCV DataFrames: EMA, MACD histogram, ATR, session VWAP.

Every function takes a DataFrame with at least the columns it needs (a subset of
time/open/high/low/close/volume), returns a *new* DataFrame with indicator column(s)
appended, and never mutates the input. No strategy decisions or broker calls happen here.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def require_columns(df: pd.DataFrame, required: Sequence[str], func_name: str) -> None:
    """Raise a clear ValueError if any of `required` columns are missing from `df`.

    Shared by indicators.py and the strategy/risk modules that consume indicator output.
    """
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"{func_name}: missing required column(s) {missing}. "
            f"DataFrame has columns: {list(df.columns)}"
        )


def add_ema(
    df: pd.DataFrame,
    price_col: str = "close",
    fast_period: int = 20,
    slow_period: int = 50,
) -> pd.DataFrame:
    """Append `ema_{fast_period}` and `ema_{slow_period}` columns computed from `price_col`."""
    require_columns(df, [price_col], "add_ema")
    out = df.copy()
    price = out[price_col]
    out[f"ema_{fast_period}"] = price.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    out[f"ema_{slow_period}"] = price.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    return out


def add_macd(
    df: pd.DataFrame,
    price_col: str = "close",
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Append `macd`, `macd_signal`, and `macd_hist` columns computed from `price_col`."""
    require_columns(df, [price_col], "add_macd")
    out = df.copy()
    price = out[price_col]
    ema_fast = price.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    ema_slow = price.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()

    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = macd_line - signal_line
    return out


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Append an `atr_{period}` column using Wilder's smoothing (ewm with alpha=1/period)."""
    require_columns(df, ["high", "low", "close"], "add_atr")
    out = df.copy()
    prev_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out[f"atr_{period}"] = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return out


def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Append a `vwap` column: cumulative volume-weighted typical price, reset at each date.

    Typical price = (high + low + close) / 3. Missing volume is treated as 0; if cumulative
    volume for a session is 0, vwap is NaN for those rows rather than raising or returning inf.
    """
    require_columns(df, ["time", "high", "low", "close", "volume"], "add_vwap")
    out = df.copy()
    date = pd.to_datetime(out["time"]).dt.date
    typical_price = (out["high"] + out["low"] + out["close"]) / 3.0
    volume = out["volume"].fillna(0.0)

    pv_cumsum = (typical_price * volume).groupby(date).cumsum()
    volume_cumsum = volume.groupby(date).cumsum().replace(0, np.nan)

    out["vwap"] = pv_cumsum / volume_cumsum
    return out
