"""Dry-run demo: exercise run_iteration() against MockBroker with synthetic in-memory bars.

No credentials, no network, no MT5/Binance import. Demonstrates the full decision loop --
daily guard, cooldown, one-position-only, M15/M5 trend agreement, M1 entry, and cash
TP/breakeven position management -- purely through the Broker interface.

Usage:
    python scripts/run_dry_run.py [--config-dir config] [--iterations 30]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.config import load_strategy_config
from trend_only_scalper.indicators import add_atr, add_ema
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.logging_config import setup_logging
from trend_only_scalper.main import run_iteration
from trend_only_scalper.metrics import calculate_metrics, print_safety_report
from trend_only_scalper.models import DailyStats, LoopState

STRATEGY_ID = "trend_only_scalper"
WARMUP_BARS = 80
CONTINUATION_BARS = 25


def build_trend_series(bars: int, start: float, step: float, noise: float) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=bars, freq="1min")
    closes = [start + i * step for i in range(bars)]
    opens = [c - step for c in closes]
    highs = [max(o, c) + noise for o, c in zip(opens, closes)]
    lows = [min(o, c) - noise for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * bars}
    )


def build_m1_series_with_pullback() -> pd.DataFrame:
    """Warm-up uptrend, then one engineered pullback-and-bounce bar (sized from the actual
    EMA/ATR at that point), then a continuation uptrend long enough to reach the cash TP.
    """
    base = build_trend_series(bars=WARMUP_BARS, start=100.0, step=0.1, noise=0.05)
    enriched = add_atr(add_ema(base, fast_period=20, slow_period=50), period=14)
    last = enriched.iloc[-1]
    ema20, atr14 = last["ema_20"], last["atr_14"]

    pullback = pd.DataFrame(
        [
            {
                "time": base["time"].iloc[-1] + pd.Timedelta(minutes=1),
                "open": ema20 - 0.2 * atr14,
                "high": ema20 + 0.35 * atr14,
                "low": ema20 - 0.1 * atr14,
                "close": ema20 + 0.3 * atr14,
                "volume": 100.0,
            }
        ]
    )
    combined = pd.concat([base, pullback], ignore_index=True)

    continuation = build_trend_series(
        bars=CONTINUATION_BARS, start=combined["close"].iloc[-1], step=0.1, noise=0.05
    )
    continuation["time"] = pd.date_range(
        combined["time"].iloc[-1] + pd.Timedelta(minutes=1), periods=len(continuation), freq="1min"
    )
    return pd.concat([combined, continuation], ignore_index=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run the bot loop against MockBroker")
    parser.add_argument("--config-dir", default="config", help="Directory containing strategy.yaml")
    parser.add_argument("--iterations", type=int, default=30, help="Max loop iterations to run")
    parser.add_argument(
        "--journal-path", default="logs/trade_journal.csv", help="Where to append the CSV trade journal"
    )
    args = parser.parse_args(argv)

    setup_logging()
    cfg = load_strategy_config(Path(args.config_dir) / "strategy.yaml")
    print_safety_report(cfg, backend="mock")

    m1_full = build_m1_series_with_pullback()
    m15 = build_trend_series(bars=WARMUP_BARS, start=100.0, step=0.1, noise=0.05)
    m5 = build_trend_series(bars=WARMUP_BARS, start=100.0, step=0.1, noise=0.05)

    broker = MockBroker(symbol=cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M15", m15)
    broker.set_bars("M5", m5)

    state = LoopState(daily_stats=DailyStats(trading_day="2026-01-01"))

    reveal_from = WARMUP_BARS
    iterations = min(args.iterations, len(m1_full) - reveal_from)

    for i in range(1, iterations + 1):
        cutoff = reveal_from + i
        broker.set_bars("M1", m1_full.iloc[:cutoff])
        print(f"--- iteration {i} (M1 bars so far: {cutoff}) ---")
        run_iteration(broker, cfg, STRATEGY_ID, state, journal_path=args.journal_path)

    print("\nOrder log:")
    for order in broker.get_order_log():
        print(order)

    print("\nTrade history:")
    for trade in broker.get_trade_history():
        print(trade)

    print(f"\nTrade journal saved to: {Path(args.journal_path).resolve()}")
    metrics = calculate_metrics(read_journal_rows(args.journal_path))
    print("\nMetrics summary:")
    print(metrics)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
