"""Unified CLI: dry-run, replay, mt5-demo, binance-demo, safety-report.

A thin dispatcher over the broker-agnostic bot loop (main.run_iteration), the backtest
replay driver, and each broker adapter -- no new strategy/risk logic lives here. Every
subcommand is demo-first: MT5/Binance real order placement stays off until their config's
allow_live_trading is explicitly set to true (see each command's docstring below).

Examples:
    python -m trend_only_scalper.cli dry-run --config config/strategy.yaml
    python -m trend_only_scalper.cli replay --strategy config/strategy.yaml --backtest config/backtest.yaml
    python -m trend_only_scalper.cli mt5-demo --strategy config/strategy.yaml --broker config/mt5.yaml
    python -m trend_only_scalper.cli binance-demo --strategy config/strategy.yaml --broker config/binance.yaml
    python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

import pandas as pd

from trend_only_scalper.backtest.replay import load_backtest_config, run_replay
from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.config import load_binance_config, load_env, load_mt5_config, load_strategy_config
from trend_only_scalper.indicators import add_atr, add_ema
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.logging_config import setup_logging
from trend_only_scalper.main import run_iteration
from trend_only_scalper.metrics import calculate_metrics, print_safety_report
from trend_only_scalper.models import DailyStats, LoopState, load_daily_stats, save_daily_stats

logger = logging.getLogger("trend_only_scalper.cli")

STRATEGY_ID = "trend_only_scalper"
MAX_CONSECUTIVE_ITERATION_ERRORS = 5


class _ShutdownFlag:
    """Set by SIGINT/SIGTERM; continuous commands (mt5-demo/binance-demo) poll this."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, signum: int, frame: FrameType | None) -> None:
        print(f"\nShutdown requested (signal {signum}). Finishing current iteration...")
        self.requested = True


# --- dry-run: synthetic data + MockBroker, no credentials --------------------

_WARMUP_BARS = 80
_CONTINUATION_BARS = 25


def _build_trend_series(bars: int, start: float, step: float, noise: float) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=bars, freq="1min")
    closes = [start + i * step for i in range(bars)]
    opens = [c - step for c in closes]
    highs = [max(o, c) + noise for o, c in zip(opens, closes)]
    lows = [min(o, c) - noise for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * bars}
    )


def _build_m1_series_with_pullback() -> pd.DataFrame:
    """Warm-up uptrend, then one engineered pullback-and-bounce bar (sized from the actual
    EMA/ATR at that point), then a continuation uptrend long enough to reach the cash TP.
    """
    base = _build_trend_series(bars=_WARMUP_BARS, start=100.0, step=0.1, noise=0.05)
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

    continuation = _build_trend_series(bars=_CONTINUATION_BARS, start=combined["close"].iloc[-1], step=0.1, noise=0.05)
    continuation["time"] = pd.date_range(
        combined["time"].iloc[-1] + pd.Timedelta(minutes=1), periods=len(continuation), freq="1min"
    )
    return pd.concat([combined, continuation], ignore_index=True)


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Run the bot loop against MockBroker with synthetic in-memory bars.

    No credentials, no network, no MT5/Binance import -- safe to run anywhere.
    """
    strategy_cfg = load_strategy_config(args.config)
    print_safety_report(strategy_cfg, backend="mock")

    m1_full = _build_m1_series_with_pullback()
    m15 = _build_trend_series(bars=_WARMUP_BARS, start=100.0, step=0.1, noise=0.05)
    m5 = _build_trend_series(bars=_WARMUP_BARS, start=100.0, step=0.1, noise=0.05)

    broker = MockBroker(symbol=strategy_cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M15", m15)
    broker.set_bars("M5", m5)

    state = LoopState(daily_stats=DailyStats(trading_day="2026-01-01"))
    reveal_from = _WARMUP_BARS
    iterations = min(args.iterations, len(m1_full) - reveal_from)

    for i in range(1, iterations + 1):
        broker.set_bars("M1", m1_full.iloc[: reveal_from + i])
        print(f"--- iteration {i} ---")
        run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=args.journal_path)

    _print_journal_and_metrics(args.journal_path)
    return 0


# --- replay -------------------------------------------------------------


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a historical M1 OHLCV CSV through the bot loop (see config/backtest.yaml)."""
    strategy_cfg = load_strategy_config(args.strategy)
    backtest_cfg = load_backtest_config(args.backtest)

    print_safety_report(strategy_cfg, backend="backtest")
    print(f"\nReplaying {backtest_cfg.input_csv} (symbol={backtest_cfg.symbol})...")

    result = run_replay(backtest_cfg, strategy_cfg, strategy_id=STRATEGY_ID)

    print(f"\nBars processed: {result.bars_processed}")
    print(f"Trades closed:  {len(result.trade_history)}")
    _print_journal_and_metrics(backtest_cfg.output_journal_csv)
    return 0


# --- mt5-demo / binance-demo: real market data, orders gated by allow_live_trading -----


def _run_continuous_loop(broker, strategy_cfg, args) -> None:
    shutdown = _ShutdownFlag()
    signal.signal(signal.SIGINT, shutdown.request)
    signal.signal(signal.SIGTERM, shutdown.request)

    broker.connect()
    try:
        trading_day = time.strftime("%Y-%m-%d")
        daily_stats = load_daily_stats(args.state_path, trading_day)
        state = LoopState(daily_stats=daily_stats)
        iteration = 0
        consecutive_errors = 0
        while not shutdown.requested:
            iteration += 1
            print(f"--- iteration {iteration} ---")
            try:
                run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=args.journal_path)
                consecutive_errors = 0
            except Exception:
                # A transient broker/network error must not kill a process managing a
                # real open position -- its hard stop-loss stays live on the broker
                # regardless, but TP/breakeven/cooldown/journal need the loop to keep
                # running. Only give up after repeated consecutive failures, which
                # signals something is actually broken (bad credentials, dead
                # connection) rather than a one-off blip.
                consecutive_errors += 1
                logger.exception(
                    "run_iteration() raised an unexpected error (%d/%d consecutive) -- "
                    "continuing the loop; any open position's hard stop-loss remains "
                    "active on the broker regardless.",
                    consecutive_errors, MAX_CONSECUTIVE_ITERATION_ERRORS,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ITERATION_ERRORS:
                    logger.error(
                        "%d consecutive errors -- stopping (this is not a one-off blip).",
                        consecutive_errors,
                    )
                    raise
            save_daily_stats(args.state_path, state.daily_stats)
            if args.iterations and iteration >= args.iterations:
                break
            if not shutdown.requested:
                time.sleep(args.loop_interval)
    finally:
        broker.disconnect()


def cmd_mt5_demo(args: argparse.Namespace) -> int:
    """Run the bot loop against a real MT5 terminal connection.

    Dry-run by default: config/mt5.yaml's allow_live_trading defaults to false, so real
    market data is used but every order is simulated locally. Set allow_live_trading: true
    (demo account only!) to send real orders.
    """
    from trend_only_scalper.brokers.mt5_broker import MT5Broker

    env = load_env(args.env_file)
    strategy_cfg = load_strategy_config(args.strategy)
    mt5_cfg = load_mt5_config(args.broker, env=env)

    print_safety_report(strategy_cfg, backend="mt5", allow_live_trading=mt5_cfg.allow_live_trading)
    if mt5_cfg.allow_live_trading:
        print(
            "\n*** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to the connected "
            f"MT5 account (magic={mt5_cfg.magic}). Confirm this is a DEMO account first. ***\n"
        )
        input("Press Enter to continue, or Ctrl+C to abort... ")
    else:
        print("\nallow_live_trading is False -- real MT5 data, simulated order placement only.\n")

    broker = MT5Broker(mt5_cfg, strategy_id=STRATEGY_ID)
    _run_continuous_loop(broker, strategy_cfg, args)
    _print_journal_and_metrics(args.journal_path)
    return 0


def cmd_binance_demo(args: argparse.Namespace) -> int:
    """Run the bot loop against Binance via ccxt, futures testnet by default.

    Dry-run by default: config/binance.yaml's allow_live_trading defaults to false, so
    real market data is used but every order is simulated locally. Set allow_live_trading:
    true (testnet first!) to send real orders.
    """
    from trend_only_scalper.brokers.binance_broker import BinanceBroker

    env = load_env(args.env_file)
    strategy_cfg = load_strategy_config(args.strategy)
    binance_cfg = load_binance_config(args.broker, env=env)

    print_safety_report(strategy_cfg, backend="binance", allow_live_trading=binance_cfg.allow_live_trading)
    print(f"testnet: {binance_cfg.testnet}   market_type: {binance_cfg.market_type}")
    if binance_cfg.allow_live_trading:
        print(
            "\n*** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to Binance "
            f"({'TESTNET' if binance_cfg.testnet else 'MAINNET -- REAL FUNDS'}). ***\n"
        )
        input("Press Enter to continue, or Ctrl+C to abort... ")
    else:
        print("\nallow_live_trading is False -- real Binance data, simulated order placement only.\n")

    broker = BinanceBroker(binance_cfg, strategy_id=STRATEGY_ID)
    _run_continuous_loop(broker, strategy_cfg, args)
    _print_journal_and_metrics(args.journal_path)
    return 0


# --- safety-report --------------------------------------------------------


def cmd_safety_report(args: argparse.Namespace) -> int:
    """Print the current strategy.yaml's safety settings (anti-pattern guards, risk limits).

    For --backend mt5/binance, also loads that broker's config so the report shows
    allow_live_trading -- the flag that actually gates whether real orders can be sent.
    """
    strategy_cfg = load_strategy_config(args.strategy)

    allow_live_trading: bool | None = None
    if args.backend == "mt5":
        env = load_env(args.env_file)
        mt5_cfg = load_mt5_config(args.broker or "config/mt5.yaml", env=env)
        allow_live_trading = mt5_cfg.allow_live_trading
    elif args.backend == "binance":
        env = load_env(args.env_file)
        binance_cfg = load_binance_config(args.broker or "config/binance.yaml", env=env)
        allow_live_trading = binance_cfg.allow_live_trading

    print_safety_report(strategy_cfg, backend=args.backend, allow_live_trading=allow_live_trading)
    return 0


# --- shared helpers ------------------------------------------------------


def _print_journal_and_metrics(journal_path: str) -> None:
    if Path(journal_path).exists():
        print(f"\nJournal saved to: {Path(journal_path).resolve()}")
    else:
        print("\nNo trades closed -- journal not created.")
    metrics = calculate_metrics(read_journal_rows(journal_path))
    print("\nMetrics summary:")
    print(metrics)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trend_only_scalper", description="Trend-only scalper CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser("dry-run", help="Run against MockBroker; no credentials required")
    dry_run.add_argument("--config", default="config/strategy.yaml", help="Path to strategy.yaml")
    dry_run.add_argument("--iterations", type=int, default=30)
    dry_run.add_argument("--journal-path", default="logs/trade_journal.csv")
    dry_run.set_defaults(func=cmd_dry_run)

    replay = subparsers.add_parser("replay", help="Replay a historical OHLCV CSV through the bot loop")
    replay.add_argument("--strategy", default="config/strategy.yaml", help="Path to strategy.yaml")
    replay.add_argument("--backtest", default="config/backtest.yaml", help="Path to backtest.yaml")
    replay.set_defaults(func=cmd_replay)

    mt5_demo = subparsers.add_parser("mt5-demo", help="Run against a real MT5 terminal (dry-run by default)")
    mt5_demo.add_argument("--strategy", default="config/strategy.yaml", help="Path to strategy.yaml")
    mt5_demo.add_argument("--broker", default="config/mt5.yaml", help="Path to mt5.yaml")
    mt5_demo.add_argument("--env-file", default=".env")
    mt5_demo.add_argument("--iterations", type=int, default=0, help="0 = run until Ctrl+C")
    mt5_demo.add_argument("--loop-interval", type=float, default=5.0)
    mt5_demo.add_argument("--journal-path", default="logs/trade_journal_mt5.csv")
    mt5_demo.add_argument(
        "--state-path", default="logs/daily_stats_mt5.json",
        help="Where daily-guard counters (trade_count/consecutive_losses) persist across restarts",
    )
    mt5_demo.set_defaults(func=cmd_mt5_demo)

    binance_demo = subparsers.add_parser(
        "binance-demo", help="Run against Binance via ccxt (testnet + dry-run by default)"
    )
    binance_demo.add_argument("--strategy", default="config/strategy.yaml", help="Path to strategy.yaml")
    binance_demo.add_argument("--broker", default="config/binance.yaml", help="Path to binance.yaml")
    binance_demo.add_argument("--env-file", default=".env")
    binance_demo.add_argument("--iterations", type=int, default=0, help="0 = run until Ctrl+C")
    binance_demo.add_argument("--loop-interval", type=float, default=5.0)
    binance_demo.add_argument("--journal-path", default="logs/trade_journal_binance.csv")
    binance_demo.add_argument(
        "--state-path", default="logs/daily_stats_binance.json",
        help="Where daily-guard counters (trade_count/consecutive_losses) persist across restarts",
    )
    binance_demo.set_defaults(func=cmd_binance_demo)

    safety_report = subparsers.add_parser("safety-report", help="Print the current config's safety settings")
    safety_report.add_argument("--strategy", default="config/strategy.yaml", help="Path to strategy.yaml")
    safety_report.add_argument("--backend", default="mock", choices=["mock", "mt5", "binance", "backtest"])
    safety_report.add_argument(
        "--broker", default=None,
        help="Path to mt5.yaml/binance.yaml (only used when --backend is mt5/binance; "
        "defaults to config/mt5.yaml or config/binance.yaml)",
    )
    safety_report.add_argument("--env-file", default=".env")
    safety_report.set_defaults(func=cmd_safety_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
