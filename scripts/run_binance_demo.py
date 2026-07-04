"""Run the bot loop against Binance (via ccxt), futures testnet by default.

Safety model: by default (config/binance.yaml `allow_live_trading: false`) this connects
to Binance for REAL market data -- OHLCV, tickers, balances -- but SIMULATES order
placement locally instead of sending real orders, exactly like MockBroker/MT5Broker. No
order ever reaches the exchange unless `allow_live_trading: true` is set explicitly, and
`testnet: true` (the default) should be kept even then.

Requires BINANCE_API_KEY/BINANCE_API_SECRET in .env (or the variable names configured via
binance.yaml's api_key_env/api_secret_env) -- read-only market data calls work without
keys on most public endpoints, but balance/position/order calls require them.

Usage:
    python scripts/run_binance_demo.py [--config-dir config] [--iterations 0] [--loop-interval 5]

--iterations 0 (default) runs until Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trend_only_scalper.brokers.binance_broker import BinanceBroker
from trend_only_scalper.config import load_binance_config, load_env, load_strategy_config
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.logging_config import setup_logging
from trend_only_scalper.main import run_iteration
from trend_only_scalper.metrics import calculate_metrics, print_safety_report
from trend_only_scalper.models import DailyStats, LoopState

STRATEGY_ID = "trend_only_scalper"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the bot loop against Binance (ccxt)")
    parser.add_argument("--config-dir", default="config", help="Directory containing *.yaml configs")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--iterations", type=int, default=0, help="Max iterations; 0 = run until Ctrl+C")
    parser.add_argument("--loop-interval", type=float, default=5.0, help="Seconds between iterations")
    parser.add_argument("--journal-path", default="logs/trade_journal_binance.csv")
    args = parser.parse_args(argv)

    setup_logging()
    env = load_env(args.env_file)
    strategy_cfg = load_strategy_config(Path(args.config_dir) / "strategy.yaml")
    binance_cfg = load_binance_config(Path(args.config_dir) / "binance.yaml", env=env)

    print_safety_report(strategy_cfg, backend="binance")
    print(f"testnet: {binance_cfg.testnet}   market_type: {binance_cfg.market_type}")

    if binance_cfg.allow_live_trading:
        print(
            "\n*** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to Binance "
            f"({'TESTNET' if binance_cfg.testnet else 'MAINNET -- REAL FUNDS'}). ***\n"
        )
        if not binance_cfg.testnet:
            print("*** testnet is FALSE -- this would trade with REAL FUNDS on mainnet. ***\n")
        input("Press Enter to continue, or Ctrl+C to abort... ")
    else:
        print(
            "\nallow_live_trading is False -- using REAL Binance market data but SIMULATING "
            "order placement locally. No real orders will be sent.\n"
        )

    broker = BinanceBroker(binance_cfg, strategy_id=STRATEGY_ID)
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
