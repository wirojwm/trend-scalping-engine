"""CSV OHLCV loading and M1 -> M5/M15 resampling for the backtest replay.

No lookahead by construction: resample_ohlcv() buckets are left-labeled/left-closed (a
bucket labeled 09:05 covers [09:05, 09:10)), computed once from the full M1 series since
a closed bucket's OHLC only ever depends on the M1 rows within it, never later ones.
SimulatedBroker is responsible for only exposing a bucket once its end time has actually
elapsed in the replay -- see simulated_broker.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def load_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    """Load an M1 OHLCV CSV with columns time,open,high,low,close,volume."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Backtest input CSV not found: {path}")

    df = pd.read_csv(path)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required column(s) {missing}. Expected: {REQUIRED_COLUMNS}")

    df = df[REQUIRED_COLUMNS].copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def filter_date_range(
    df: pd.DataFrame, start_date: str | None, end_date: str | None
) -> pd.DataFrame:
    """Restrict to rows within [start_date, end_date] (inclusive), either bound optional."""
    if start_date:
        df = df[df["time"] >= pd.Timestamp(start_date, tz="UTC")]
    if end_date:
        df = df[df["time"] <= pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)]
    return df.reset_index(drop=True)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample M1 OHLCV into a coarser timeframe, e.g. '5min' or '15min'."""
    indexed = df.set_index("time")
    resampled = (
        indexed.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    return resampled.reset_index()
