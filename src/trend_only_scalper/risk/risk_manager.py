"""Stop-loss calculation from recent swing structure plus an ATR buffer.

Broker-agnostic: works on any indicator-enriched OHLCV DataFrame with an ATR column.
"""

from __future__ import annotations

import pandas as pd

from trend_only_scalper.indicators import require_columns
from trend_only_scalper.models import Side


def calculate_stop_loss(
    df: pd.DataFrame,
    side: Side,
    swing_lookback: int = 20,
    sl_atr_buffer: float = 0.5,
    high_col: str = "high",
    low_col: str = "low",
    atr_col: str = "atr_14",
) -> float | None:
    """BUY stop-loss = recent swing low minus an ATR buffer; SELL = recent swing high plus one.

    `swing_lookback` bars are taken from the end of `df`. Returns None if there isn't enough
    data yet (empty DataFrame or ATR/swing values still NaN) rather than an invalid stop.
    """
    require_columns(df, [high_col, low_col, atr_col], "calculate_stop_loss")
    if df.empty:
        return None

    atr = df[atr_col].iloc[-1]
    if pd.isna(atr):
        return None

    window = df.tail(swing_lookback)
    buffer = atr * sl_atr_buffer

    if side is Side.BUY:
        swing_low = window[low_col].min()
        return None if pd.isna(swing_low) else swing_low - buffer

    swing_high = window[high_col].max()
    return None if pd.isna(swing_high) else swing_high + buffer
