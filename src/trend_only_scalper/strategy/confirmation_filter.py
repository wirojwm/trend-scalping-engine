"""M5 trend confirmation filter: decides UP / DOWN / NONE, adding a VWAP check on top of
the same EMA/MACD structure used by the M15 trend filter.
"""

from __future__ import annotations

import pandas as pd

from trend_only_scalper.indicators import require_columns
from trend_only_scalper.models import Trend


def confirm_trend(
    df: pd.DataFrame,
    close_col: str = "close",
    ema_fast_col: str = "ema_20",
    ema_slow_col: str = "ema_50",
    macd_hist_col: str = "macd_hist",
    vwap_col: str = "vwap",
) -> Trend:
    """Confirmation UP requires close/EMA20 above EMA50, close above VWAP, and positive MACD
    histogram; DOWN is the mirror. NONE otherwise (including missing/NaN indicator data).
    """
    require_columns(
        df, [close_col, ema_fast_col, ema_slow_col, macd_hist_col, vwap_col], "confirm_trend"
    )
    if df.empty:
        return Trend.NONE

    row = df.iloc[-1]
    close, ema_fast, ema_slow, macd_hist, vwap = (
        row[close_col],
        row[ema_fast_col],
        row[ema_slow_col],
        row[macd_hist_col],
        row[vwap_col],
    )
    if any(pd.isna(v) for v in (close, ema_fast, ema_slow, macd_hist, vwap)):
        return Trend.NONE

    if close > ema_slow and ema_fast > ema_slow and close > vwap and macd_hist > 0:
        return Trend.UP
    if close < ema_slow and ema_fast < ema_slow and close < vwap and macd_hist < 0:
        return Trend.DOWN
    return Trend.NONE
