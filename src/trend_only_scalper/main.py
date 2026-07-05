"""Broker-agnostic bot loop: run_iteration() is the reusable engine every entry point uses.

`run_iteration()` is the full decision loop from the project spec (load bars -> indicators
-> daily guard -> manage-or-scan -> cooldown -> trend/confirmation -> entry -> stop loss ->
order), driven entirely through the `Broker` interface. It has no MT5/Binance/ccxt import,
so it works identically against MockBroker, MT5Broker, BinanceBroker, and SimulatedBroker
(the backtest replay driver).

`python -m trend_only_scalper.cli <command>` (see cli.py) is the primary entry point for
actually running the bot -- dry-run, replay, mt5-demo, binance-demo, safety-report. The
`run()` function below is a minimal legacy heartbeat scaffold kept for backward
compatibility with `python -m trend_only_scalper.main --once`; prefer cli.py for anything
beyond a quick config-loading smoke test.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import FrameType

import pandas as pd

from trend_only_scalper.brokers.base import Broker
from trend_only_scalper.config import AppConfig, StrategyConfig, load_app_config
from trend_only_scalper.indicators import add_atr, add_ema, add_macd, add_vwap
from trend_only_scalper.journal import JournalRow, write_journal_row
from trend_only_scalper.logging_config import DecisionLogEntry, log_decision, setup_logging
from trend_only_scalper.metrics import print_safety_report
from trend_only_scalper.models import (
    CloseReason,
    DailyStats,
    LoopState,
    OpenTradeContext,
    Position,
    PositionAction,
    Side,
    TradeResult,
    Trend,
)
from trend_only_scalper.risk.cooldown import cooldown_bars_for_reason, is_active, start_cooldown, tick
from trend_only_scalper.risk.daily_guard import evaluate_daily_guard
from trend_only_scalper.risk.risk_manager import calculate_stop_loss
from trend_only_scalper.strategy.confirmation_filter import confirm_trend
from trend_only_scalper.strategy.entry_signal import detect_entry_signal
from trend_only_scalper.strategy.position_manager import manage_position
from trend_only_scalper.strategy.trend_filter import detect_trend

logger = logging.getLogger("trend_only_scalper.main")
loop_logger = logging.getLogger("trend_only_scalper.bot_loop")

BAR_LOOKBACK = 100  # bars fetched per timeframe each iteration -- enough for slow-EMA warmup
VWAP_BAR_LOOKBACK = 288  # M5 bars in a full 24h session (24*60/5) -- add_vwap() resets its
# cumulative sum per calendar date, so the M5 fetch must always reach back to today's first
# bar or the VWAP silently starts mid-session instead of from the session open.
DEFAULT_JOURNAL_PATH = "logs/trade_journal.csv"


class ShutdownFlag:
    """Set by SIGINT/SIGTERM handlers; the main loop polls this each iteration."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, signum: int, frame: FrameType | None) -> None:
        logger.info("Shutdown requested (signal %s). Finishing current iteration...", signum)
        self.requested = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="trend_only_scalper dry-run entry point")
    parser.add_argument(
        "--backend",
        choices=["mt5", "binance", "mock"],
        default=None,
        help="Override BROKER_BACKEND from .env (default: value from .env, else 'mock')",
    )
    parser.add_argument("--config-dir", default="config", help="Directory containing *.yaml configs")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument(
        "--loop-interval",
        type=float,
        default=5.0,
        help="Seconds between heartbeat iterations",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single iteration then exit (useful for smoke-testing config)",
    )
    return parser.parse_args(argv)


def log_startup_banner(config: AppConfig, backend: str) -> None:
    logger.info("=" * 60)
    logger.info("trend_only_scalper")
    logger.info("Backend:        %s", backend)
    logger.info("Symbol:         %s", config.strategy.symbol)
    logger.info("Account mode:   %s", config.strategy.account_mode)
    logger.info("DRY RUN:        %s", config.dry_run)
    if not config.dry_run:
        logger.warning(
            "dry_run is False, but no real MT5/Binance adapter is implemented yet -- "
            "no live orders can be placed regardless."
        )
    logger.info("=" * 60)


def _ema_fast_col(cfg: StrategyConfig) -> str:
    return f"ema_{cfg.ema_fast}"


def _ema_slow_col(cfg: StrategyConfig) -> str:
    return f"ema_{cfg.ema_slow}"


def _atr_col(cfg: StrategyConfig) -> str:
    return f"atr_{cfg.atr_period}"


def _add_trend_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = add_ema(df, fast_period=cfg.ema_fast, slow_period=cfg.ema_slow)
    df = add_macd(df, fast_period=cfg.macd_fast, slow_period=cfg.macd_slow, signal_period=cfg.macd_signal)
    return df


def _add_confirmation_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = _add_trend_indicators(df, cfg)
    return add_vwap(df)


def _add_entry_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = add_ema(df, fast_period=cfg.ema_fast, slow_period=cfg.ema_slow)
    df = add_vwap(df)
    return add_atr(df, period=cfg.atr_period)


def _record_closed_trade(
    state: LoopState, reason: CloseReason, realized_pnl_cash: float, cfg: StrategyConfig
) -> None:
    state.daily_stats.trade_count += 1
    state.daily_stats.consecutive_losses = (
        state.daily_stats.consecutive_losses + 1 if realized_pnl_cash < 0 else 0
    )
    bars = cooldown_bars_for_reason(
        reason, cfg.cooldown_after_tp_bars, cfg.cooldown_after_be_bars, cfg.cooldown_after_sl_bars
    )
    state.cooldown = start_cooldown(bars)


def _maybe_reset_daily_stats(state: LoopState, m1_bars: pd.DataFrame) -> None:
    """Reset daily_stats at a calendar-day boundary, derived from the latest M1 bar's own
    timestamp rather than wall-clock time -- this makes the reset correct for both live
    trading (the latest bar IS "now") and the backtest replay (the latest bar is whatever
    point in history is currently being replayed), with no extra parameter needed.

    Without this, trade_count/consecutive_losses would accumulate forever across days in
    any long-running process, turning max_trades_per_day into a lifetime cap.
    """
    if m1_bars.empty:
        return
    latest = m1_bars["time"].iloc[-1]
    current_date = latest.date().isoformat() if hasattr(latest, "date") else None
    if current_date and current_date != state.daily_stats.trading_day:
        state.daily_stats = DailyStats(trading_day=current_date)


def _price_from_pnl(
    entry_price: float, quantity: float, pnl_cash: float, side: Side, contract_size: float = 1.0
) -> float:
    """Invert the PnL model (pnl = qty * contract_size * price_move) to recover the exit
    price, without needing a broker-specific "get exit price" method on the Broker interface.
    contract_size must be the broker's actual Broker.contract_size(symbol) -- 1.0 for linear
    instruments, e.g. an MT5 symbol's trade_contract_size for lot-based ones.
    """
    move = pnl_cash / (quantity * contract_size)
    return entry_price + move if side is Side.BUY else entry_price - move


def _write_trade_journal_row(
    journal_path: str | Path,
    broker: Broker,
    strategy_id: str,
    cfg: StrategyConfig,
    state: LoopState,
    position: Position,
    result: TradeResult,
    realized_pnl: float,
) -> None:
    context = state.open_trade_context
    exit_price = _price_from_pnl(
        position.entry_price,
        position.quantity,
        realized_pnl,
        position.side,
        contract_size=broker.contract_size(position.symbol),
    )
    row = JournalRow(
        timestamp=datetime.now(),
        strategy_id=strategy_id,
        broker=type(broker).__name__,
        symbol=position.symbol,
        timeframe_entry=context.timeframe_entry if context else cfg.entry_timeframe,
        side=position.side.value,
        quantity=position.quantity,
        entry_price=position.entry_price,
        exit_price=exit_price,
        stop_loss_initial=context.stop_loss_initial if context else position.stop_loss,
        stop_loss_final=position.stop_loss,
        realized_pnl=realized_pnl,
        fees_or_cost=broker.get_trading_cost(position.symbol),
        reason_open=context.reason_open if context else "",
        reason_close=result.close_reason.value,
        m15_trend=context.m15_trend if context else "n/a",
        m5_confirmation=context.m5_confirmation if context else "n/a",
        m1_signal=context.m1_signal if context else "n/a",
        tp_cash=cfg.tp_cash,
        breakeven_trigger_cash=cfg.breakeven_trigger_cash,
        daily_pnl_after_trade=broker.get_today_realized_pnl(),
        consecutive_losses_after_trade=state.daily_stats.consecutive_losses,
        trades_today=state.daily_stats.trade_count,
        dry_run=cfg.dry_run,
    )
    write_journal_row(journal_path, row)


def run_iteration(
    broker: Broker,
    cfg: StrategyConfig,
    strategy_id: str,
    state: LoopState,
    bar_lookback: int = BAR_LOOKBACK,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
) -> LoopState:
    """Execute exactly one bot-loop iteration against `broker` and return the updated state.

    Never opens more than one position, never trades counter-trend, never scans for a new
    entry while a position is open -- see the module docstring for the full decision order.
    Logs exactly one structured decision entry per call, and appends a trade journal row
    whenever a position closes.
    """
    symbol = cfg.symbol

    decision = {
        "symbol": symbol,
        "has_open_position": False,
        "daily_guard_status": "allowed",
        "cooldown_status": "inactive",
        "trading_cost_status": "ok",
        "m15_trend": "n/a",
        "m5_confirmation": "n/a",
        "m1_signal": "none",
        "action_taken": "none",
        "no_trade_reason": "",
    }

    def finish() -> LoopState:
        log_decision(DecisionLogEntry(**decision))
        return state

    m15_bars = broker.get_bars(symbol, "M15", bar_lookback)
    m5_bars = broker.get_bars(symbol, "M5", max(bar_lookback, VWAP_BAR_LOOKBACK))
    m1_bars = broker.get_bars(symbol, "M1", bar_lookback)

    _maybe_reset_daily_stats(state, m1_bars)
    previous_realized_pnl_cash = state.daily_stats.realized_pnl_cash

    m15 = _add_trend_indicators(m15_bars, cfg)
    m5 = _add_confirmation_indicators(m5_bars, cfg)
    m1 = _add_entry_indicators(m1_bars, cfg)

    state.daily_stats.realized_pnl_cash = broker.get_today_realized_pnl()
    guard_state = evaluate_daily_guard(
        state.daily_stats,
        cfg.daily_profit_target,
        cfg.daily_max_loss,
        cfg.max_consecutive_losses,
        cfg.max_trades_per_day,
    )
    decision["daily_guard_status"] = (
        "allowed" if guard_state.trading_allowed else f"blocked:{','.join(guard_state.blocked_reasons)}"
    )

    # Manage-or-scan gate: while a position is open, only ever manage it, then return --
    # never evaluate a new entry in the same iteration (one-position-only rule).
    position = broker.get_open_position(symbol, strategy_id)

    if position is None and state.last_known_position is not None:
        # We were tracking an open position last call, and the broker now reports none --
        # but WE never closed it (that always clears last_known_position immediately
        # below). This means the broker closed it on its own, most likely a hard
        # stop-loss order firing server-side (MT5/Binance). manage_position() never
        # decides this case by design (see strategy/position_manager.py), so without
        # this check the close would vanish with no journal row, no cooldown, and no
        # trade/loss counter update -- letting the bot re-enter immediately next bar.
        vanished = state.last_known_position
        realized_pnl = state.daily_stats.realized_pnl_cash - previous_realized_pnl_cash
        # If we'd already moved the stop to breakeven before it vanished, the order that
        # fired IS that (moved) stop -- a breakeven-locked close, not the original hard SL.
        context = state.open_trade_context
        close_reason = (
            CloseReason.BREAKEVEN_SL if context and context.breakeven_applied else CloseReason.HARD_SL
        )
        autonomous_result = TradeResult(action=PositionAction.CLOSE, close_reason=close_reason)
        _record_closed_trade(state, close_reason, realized_pnl, cfg)
        loop_logger.warning(
            "position=%s vanished without an explicit close (broker-side %s assumed) -- "
            "recording pnl=%.4f cooldown_bars=%d",
            vanished.position_id, close_reason.value, realized_pnl, state.cooldown.bars_remaining,
        )
        _write_trade_journal_row(
            journal_path, broker, strategy_id, cfg, state, vanished, autonomous_result, realized_pnl
        )
        state.open_trade_context = None
        state.last_known_position = None
        decision["action_taken"] = f"autonomous_close_{close_reason.value.lower()}"
        decision["no_trade_reason"] = "position_closed_by_broker"
        # Return now, exactly like a manage_position()-decided close does -- the cooldown
        # this just started should count down starting next call, not lose a bar to the
        # tick() below firing within this same call.
        return finish()

    if position is not None:
        state.last_known_position = position
        decision["has_open_position"] = True
        unrealized = broker.get_unrealized_pnl(position)
        result = manage_position(
            position,
            unrealized,
            cfg.tp_cash,
            cfg.breakeven_trigger_cash,
            cfg.breakeven_lock_cash,
            cost_buffer_price=broker.get_trading_cost(symbol),
            contract_size=broker.contract_size(symbol),
        )
        loop_logger.info(
            "position open side=%s qty=%s entry=%.5f sl=%.5f pnl=%.4f -> action=%s",
            position.side.value, position.quantity, position.entry_price,
            position.stop_loss, unrealized, result.action.value,
        )
        if result.action is PositionAction.CLOSE:
            broker.close_position(position.position_id, result.close_reason)
            _record_closed_trade(state, result.close_reason, unrealized, cfg)
            loop_logger.info(
                "closed position=%s reason=%s pnl=%.4f cooldown_bars=%d",
                position.position_id, result.close_reason.value, unrealized,
                state.cooldown.bars_remaining,
            )
            _write_trade_journal_row(journal_path, broker, strategy_id, cfg, state, position, result, unrealized)
            state.open_trade_context = None
            state.last_known_position = None
            decision["action_taken"] = "manage_close"
        elif result.action is PositionAction.MODIFY_SL:
            broker.modify_stop_loss(position.position_id, result.new_stop_loss)
            loop_logger.info(
                "modified stop loss position=%s new_sl=%.5f", position.position_id, result.new_stop_loss
            )
            if state.open_trade_context is not None:
                state.open_trade_context = replace(state.open_trade_context, breakeven_applied=True)
            decision["action_taken"] = "manage_modify_sl"
        else:
            decision["action_taken"] = "manage_none"
        return finish()

    if not guard_state.trading_allowed:
        loop_logger.info("no trade: daily guard blocked (%s)", guard_state.blocked_reasons)
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = decision["daily_guard_status"]
        return finish()

    # Cooldown is bar-counted and only ever gates new entries (never position management,
    # which already returned above).
    state.cooldown = tick(state.cooldown)
    decision["cooldown_status"] = (
        f"active:{state.cooldown.bars_remaining}" if is_active(state.cooldown) else "inactive"
    )
    if is_active(state.cooldown):
        loop_logger.info(
            "no trade: cooldown active (%d bars remaining)", state.cooldown.bars_remaining
        )
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = "cooldown_active"
        return finish()

    trading_cost = broker.get_trading_cost(symbol)
    # get_trading_cost() is a price-unit spread/fee estimate (consistent with how it's used
    # below as an ATR-scale cost buffer), so it must be converted to cash -- via the same
    # quantity/contract_size that would actually be used to open the trade -- before it can
    # be compared against the cash tp_cash target. Comparing the raw price-unit number
    # directly (the old bug) happened to be harmless at FX/MT5 price scale, but permanently
    # blocked trading at BTC/USDT scale where the price-unit number is large.
    trading_cost_cash = trading_cost * cfg.default_quantity * broker.contract_size(symbol)
    if trading_cost_cash >= cfg.tp_cash:
        decision["trading_cost_status"] = "too_high"
        loop_logger.info(
            "no trade: trading cost too high versus target (cost=%.4f >= tp_cash=%.4f)",
            trading_cost_cash, cfg.tp_cash,
        )
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = "trading_cost_too_high"
        return finish()

    trend_m15 = detect_trend(m15, ema_fast_col=_ema_fast_col(cfg), ema_slow_col=_ema_slow_col(cfg))
    confirm_m5 = confirm_trend(m5, ema_fast_col=_ema_fast_col(cfg), ema_slow_col=_ema_slow_col(cfg))
    decision["m15_trend"] = trend_m15.value
    decision["m5_confirmation"] = confirm_m5.value
    loop_logger.info("m15_trend=%s m5_confirmation=%s", trend_m15.value, confirm_m5.value)

    if trend_m15 != confirm_m5 or trend_m15 is Trend.NONE:
        loop_logger.info("no trade: M15/M5 disagree or trend is NONE")
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = "m15_m5_disagree_or_none"
        return finish()

    entry_signal = detect_entry_signal(
        m1,
        trend_m15,
        confirm_m5,
        spread_or_cost=trading_cost,
        pullback_atr_tolerance=cfg.pullback_atr_tolerance,
        abnormal_candle_atr_multiple=cfg.abnormal_candle_atr_multiple,
        min_atr_spread_multiple=cfg.min_atr_spread_multiple,
        ema_col=_ema_fast_col(cfg),
        atr_col=_atr_col(cfg),
    )
    if entry_signal is None:
        loop_logger.info("no trade: no M1 entry signal this bar")
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = "no_m1_entry_signal"
        return finish()
    decision["m1_signal"] = entry_signal.side.value

    stop_loss = calculate_stop_loss(
        m1, entry_signal.side, swing_lookback=cfg.swing_lookback, sl_atr_buffer=cfg.sl_atr_buffer,
        atr_col=_atr_col(cfg),
    )
    if stop_loss is None:
        loop_logger.info("no trade: stop-loss could not be calculated (insufficient data)")
        decision["action_taken"] = "no_trade"
        decision["no_trade_reason"] = "invalid_stop_loss"
        return finish()

    quantity = cfg.default_quantity
    opened = broker.open_market_order(symbol, entry_signal.side, quantity, stop_loss)
    loop_logger.info(
        "opened %s %s qty=%s entry=%.5f sl=%.5f reason=%s",
        entry_signal.side.value, symbol, quantity, opened.entry_price, stop_loss, entry_signal.reason,
    )
    # Track it from the moment it opens, not just from the next time we observe it still
    # open -- otherwise a position that closes (e.g. an immediate stop-loss fill in a fast
    # market) before the next call would never be detected as an autonomous close at all.
    state.last_known_position = opened
    state.open_trade_context = OpenTradeContext(
        timeframe_entry=cfg.entry_timeframe,
        stop_loss_initial=stop_loss,
        reason_open=entry_signal.reason,
        m15_trend=trend_m15.value,
        m5_confirmation=confirm_m5.value,
        m1_signal=entry_signal.side.value,
    )
    decision["action_taken"] = f"opened_{entry_signal.side.value}"
    return finish()


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()

    config = load_app_config(
        backend=args.backend, config_dir=args.config_dir, env_path=args.env_file
    )
    backend = args.backend or config.env.broker_backend
    log_startup_banner(config, backend)
    print_safety_report(config.strategy, backend)

    shutdown = ShutdownFlag()
    signal.signal(signal.SIGINT, shutdown.request)
    signal.signal(signal.SIGTERM, shutdown.request)

    iteration = 0
    while not shutdown.requested:
        iteration += 1
        logger.info(
            "Heartbeat #%d -- config loaded OK. This is the legacy scaffold; use "
            "`python -m trend_only_scalper.cli dry-run` (or replay/mt5-demo/binance-demo) "
            "to actually run the bot loop.",
            iteration,
        )
        if args.once:
            break
        time.sleep(args.loop_interval)

    logger.info("Shutdown complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
