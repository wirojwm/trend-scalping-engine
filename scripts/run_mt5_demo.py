"""Run the bot loop against a real MT5 terminal connection.

Safety model: by default (config/mt5.yaml `allow_live_trading: false`) this connects to
MT5 for REAL market data -- bars, tick prices, account/symbol info -- but SIMULATES order
placement locally instead of sending real orders, exactly like MockBroker. No order ever
reaches the account unless `allow_live_trading: true` is set explicitly, and even then
only for this strategy's own magic number.

Requires a running, logged-in MT5 terminal, or login/server/password supplied via the
environment variables named by login_env/password_env/server_env in mt5.yaml (defaults:
MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env).

Usage:
    python scripts/run_mt5_demo.py [--config-dir config] [--iterations 0] [--loop-interval 5]

--iterations 0 (default) runs until Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trend_only_scalper.brokers.mt5_broker import MT5Broker
from trend_only_scalper.config import load_env, load_mt5_config, load_strategy_config
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.logging_config import setup_logging
from trend_only_scalper.main import run_iteration
from trend_only_scalper.metrics import calculate_metrics, print_safety_report
from trend_only_scalper.models import DailyStats, LoopState

STRATEGY_ID = "trend_only_scalper"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the bot loop against a real MT5 terminal")
    parser.add_argument("--config-dir", default="config", help="Directory containing *.yaml configs")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--iterations", type=int, default=0, help="Max iterations; 0 = run until Ctrl+C")
    parser.add_argument("--loop-interval", type=float, default=5.0, help="Seconds between iterations")
    parser.add_argument("--journal-path", default="logs/trade_journal_mt5.csv")
    args = parser.parse_args(argv)

    setup_logging()
    env = load_env(args.env_file)
    strategy_cfg = load_strategy_config(Path(args.config_dir) / "strategy.yaml")
    mt5_cfg = load_mt5_config(Path(args.config_dir) / "mt5.yaml", env=env)

    print_safety_report(strategy_cfg, backend="mt5")

    if mt5_cfg.allow_live_trading:
        print(
            "\n*** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to the connected "
            "MT5 account (magic=%d). Confirm this is a DEMO account before continuing. ***\n"
            % mt5_cfg.magic
        )
        input("Press Enter to continue, or Ctrl+C to abort... ")
    else:
        print(
            "\nallow_live_trading is False -- using REAL MT5 market data but SIMULATING "
            "order placement locally. No real orders will be sent.\n"
        )

    broker = MT5Broker(mt5_cfg, strategy_id=STRATEGY_ID)
    broker.connect()

    try:
        state = LoopState(daily_stats=DailyStats(trading_day=time.strftime("%Y-%m-%d")))
        iteration = 0
        while True:
            iteration += 1
            print(f"--- iteration {iteration} ---")
            run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=args.journal_path)
            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.loop_interval)
    except KeyboardInterrupt:
        print("\nInterrupted -- shutting down.")
    finally:
        broker.disconnect()

    print(f"\nTrade journal saved to: {Path(args.journal_path).resolve()}")
    metrics = calculate_metrics(read_journal_rows(args.journal_path))
    print("\nMetrics summary:")
    print(metrics)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
