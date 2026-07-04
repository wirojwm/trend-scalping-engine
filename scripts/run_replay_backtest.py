"""Replay a historical M1 OHLCV CSV through the bot loop and report metrics.

Not a full backtesting engine -- see src/trend_only_scalper/backtest/simulated_broker.py
for the documented simplifications (bar-resolution fills, SL checked before TP/BE, etc).

Usage:
    python scripts/run_replay_backtest.py [--config-dir config] [--backtest-config PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trend_only_scalper.backtest.replay import load_backtest_config, run_replay
from trend_only_scalper.config import load_strategy_config
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.logging_config import setup_logging
from trend_only_scalper.metrics import calculate_metrics, print_safety_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a historical OHLCV CSV through the bot loop")
    parser.add_argument("--config-dir", default="config", help="Directory containing strategy.yaml/backtest.yaml")
    parser.add_argument("--backtest-config", default=None, help="Defaults to <config-dir>/backtest.yaml")
    args = parser.parse_args(argv)

    setup_logging()
    config_dir = Path(args.config_dir)
    backtest_cfg = load_backtest_config(args.backtest_config or config_dir / "backtest.yaml")
    strategy_cfg = load_strategy_config(config_dir / "strategy.yaml")

    print_safety_report(strategy_cfg, backend="backtest")
    print(f"\nReplaying {backtest_cfg.input_csv} (symbol={backtest_cfg.symbol})...")

    result = run_replay(backtest_cfg, strategy_cfg)

    print(f"\nBars processed: {result.bars_processed}")
    print(f"Trades closed:  {len(result.trade_history)}")
    print(f"\nJournal saved to: {Path(backtest_cfg.output_journal_csv).resolve()}")

    metrics = calculate_metrics(read_journal_rows(backtest_cfg.output_journal_csv))
    print("\nMetrics summary:")
    print(metrics)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
