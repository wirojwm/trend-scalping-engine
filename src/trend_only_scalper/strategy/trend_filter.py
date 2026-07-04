"""M15 main trend filter: decides UP / DOWN / NONE from the latest indicator-enriched bar."""

from __future__ import annotations

import pandas as pd

from trend_only_scalper.indicators import require_columns
from trend_only_scalper.models import Trend


def detect_trend(
    df: pd.DataFrame,
    close_col: str = "close",
    ema_fast_col: str = "ema_20",
    ema_slow_col: str = "ema_50",
    macd_hist_col: str = "macd_hist",
) -> Trend:
    """Trend UP if close/EMA20 are above EMA50 and MACD histogram is positive; DOWN if the
    mirror holds; NONE otherwise (including when the DataFrame is empty or indicators are
    still warming up / NaN).
    """
    require_columns(df, [close_col, ema_fast_col, ema_slow_col, macd_hist_col], "detect_trend")
    if df.empty:
        return Trend.NONE

    row = df.iloc[-1]
    close, ema_fast, ema_slow, macd_hist = (
        row[close_col],
        row[ema_fast_col],
        row[ema_slow_col],
        row[macd_hist_col],
    )
    if pd.isna(close) or pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(macd_hist):
        return Trend.NONE

    if close > ema_slow and ema_fast > ema_slow and macd_hist > 0:
        return Trend.UP
    if close < ema_slow and ema_fast < ema_slow and macd_hist < 0:
        return Trend.DOWN
    return Trend.NONE
