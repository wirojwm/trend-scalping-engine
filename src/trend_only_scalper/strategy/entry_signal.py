"""M1 entry trigger: only fires with the trend, only when M15 and M5 agree.

Trades with the trend only. No counter-trend, no grid, no martingale, no averaging down --
this module returns at most one Signal for the single position the caller may open.
"""

from __future__ import annotations

import pandas as pd

from trend_only_scalper.indicators import require_columns
from trend_only_scalper.models import Signal, Side, Trend


def _is_abnormal_candle(candle_range: float, atr: float, abnormal_candle_atr_multiple: float) -> bool:
    return candle_range > atr * abnormal_candle_atr_multiple


def _atr_too_low_for_cost(atr: float, spread_or_cost: float, min_atr_spread_multiple: float) -> bool:
    return atr < spread_or_cost * min_atr_spread_multiple


def _near(price_a: float, price_b: float, max_distance: float) -> bool:
    return abs(price_a - price_b) <= max_distance


def detect_entry_signal(
    m1_df: pd.DataFrame,
    trend_m15: Trend,
    confirm_m5: Trend,
    spread_or_cost: float,
    pullback_atr_tolerance: float = 0.25,
    abnormal_candle_atr_multiple: float = 2.0,
    min_atr_spread_multiple: float = 3.0,
    close_col: str = "close",
    open_col: str = "open",
    high_col: str = "high",
    low_col: str = "low",
    ema_col: str = "ema_20",
    vwap_col: str = "vwap",
    atr_col: str = "atr_14",
) -> Signal | None:
    """Detect a trend-following M1 buy/sell trigger, or return None.

    No signal is returned if: M15/M5 disagree, either trend is NONE, indicators are still
    warming up, the candle range is abnormally large versus ATR, ATR is too low relative to
    spread/cost, or the pullback/rebound-to-EMA20-or-VWAP condition isn't met.
    """
    if trend_m15 != confirm_m5 or trend_m15 is Trend.NONE:
        return None

    require_columns(
        m1_df,
        [close_col, open_col, high_col, low_col, ema_col, vwap_col, atr_col],
        "detect_entry_signal",
    )
    if m1_df.empty:
        return None

    row = m1_df.iloc[-1]
    close, open_, high, low, ema, vwap, atr = (
        row[close_col],
        row[open_col],
        row[high_col],
        row[low_col],
        row[ema_col],
        row[vwap_col],
        row[atr_col],
    )
    if any(pd.isna(v) for v in (close, open_, high, low, ema, vwap, atr)):
        return None

    if _is_abnormal_candle(high - low, atr, abnormal_candle_atr_multiple):
        return None
    if _atr_too_low_for_cost(atr, spread_or_cost, min_atr_spread_multiple):
        return None

    pullback_distance = atr * pullback_atr_tolerance

    if trend_m15 is Trend.UP:
        pulled_back = _near(low, ema, pullback_distance) or _near(low, vwap, pullback_distance)
        bullish_close = close > open_
        returned_above_ema = close > ema
        if pulled_back and bullish_close and returned_above_ema:
            return Signal(side=Side.BUY, reason="m1_pullback_bounce", reference_price=close)
        return None

    # trend_m15 is Trend.DOWN (Trend.NONE already excluded above)
    rebounded = _near(high, ema, pullback_distance) or _near(high, vwap, pullback_distance)
    bearish_close = close < open_
    returned_below_ema = close < ema
    if rebounded and bearish_close and returned_below_ema:
        return Signal(side=Side.SELL, reason="m1_rebound_rejection", reference_price=close)
    return None
