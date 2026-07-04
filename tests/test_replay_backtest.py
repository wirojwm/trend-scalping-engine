"""Tests for the backtest replay: data loading/resampling, SimulatedBroker mechanics,
and full-replay integration (one-position-only, daily max loss, max trades per day,
journal, metrics).

Indicator periods are kept small in these tests (not the EURUSD-scale production
defaults) so M15 warmup only needs a small number of M1 bars -- this is purely a test
speed/size concern, not a change to production config.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trend_only_scalper.backtest.data_loader import load_ohlcv_csv, resample_ohlcv
from trend_only_scalper.backtest.replay import BacktestConfig, run_replay
from trend_only_scalper.backtest.simulated_broker import SimulatedBroker
from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.indicators import add_atr, add_ema
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.main import run_iteration
from trend_only_scalper.metrics import calculate_metrics
from trend_only_scalper.models import CloseReason, DailyStats, LoopState, Side

SYMBOL = "TEST"
STRATEGY_ID = "trend_only_scalper"

SMALL_PERIODS = dict(
    ema_fast=3, ema_slow=5, macd_fast=2, macd_slow=3, macd_signal=2,
    atr_period=3, swing_lookback=3,
)
WARMUP_BARS = 100  # >> 5 M15 bars (75 M1 bars) needed for ema_slow=5 warmup on M15


def make_strategy_config(**overrides) -> StrategyConfig:
    defaults = dict(symbol=SYMBOL, **SMALL_PERIODS)
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def make_backtest_config(csv_path, **overrides) -> BacktestConfig:
    defaults = dict(
        input_csv=str(csv_path),
        symbol=SYMBOL,
        spread_points_or_price=0.02,
        output_journal_csv=str(csv_path.parent / "journal.csv"),
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def build_trend_series(
    direction: str, bars: int = WARMUP_BARS, start: float = 100.0, step: float = 0.1, noise: float = 0.05
) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=bars, freq="1min", tz="UTC")
    sign = 1 if direction == "up" else -1
    closes = [start + sign * i * step for i in range(bars)]
    opens = [c - sign * step for c in closes]
    highs = [max(o, c) + noise for o, c in zip(opens, closes)]
    lows = [min(o, c) - noise for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * bars}
    )


def append_pullback_bar(df: pd.DataFrame, direction: str) -> pd.DataFrame:
    """Append one bar engineered to trigger the M1 buy/sell trigger, sized from the
    EMA/ATR actually computed off `df` (small periods -- see SMALL_PERIODS).
    """
    enriched = add_atr(
        add_ema(df, fast_period=SMALL_PERIODS["ema_fast"], slow_period=SMALL_PERIODS["ema_slow"]),
        period=SMALL_PERIODS["atr_period"],
    )
    last = enriched.iloc[-1]
    ema = last[f"ema_{SMALL_PERIODS['ema_fast']}"]
    atr = last[f"atr_{SMALL_PERIODS['atr_period']}"]
    next_time = df["time"].iloc[-1] + pd.Timedelta(minutes=1)

    if direction == "up":
        low, close, open_, high = ema - 0.1 * atr, ema + 0.3 * atr, ema - 0.2 * atr, ema + 0.35 * atr
    else:
        high, close, open_, low = ema + 0.1 * atr, ema - 0.3 * atr, ema + 0.2 * atr, ema - 0.35 * atr

    new_row = pd.DataFrame(
        [{"time": next_time, "open": open_, "high": high, "low": low, "close": close, "volume": 100.0}]
    )
    return pd.concat([df, new_row], ignore_index=True)


def append_continuation(df: pd.DataFrame, direction: str, bars: int, step: float = 0.1, noise: float = 0.05) -> pd.DataFrame:
    sign = 1 if direction == "up" else -1
    continuation = build_trend_series(
        direction, bars=bars, start=df["close"].iloc[-1] + sign * step, step=step, noise=noise
    )
    continuation["time"] = pd.date_range(
        df["time"].iloc[-1] + pd.Timedelta(minutes=1), periods=bars, freq="1min", tz="UTC"
    )
    return pd.concat([df, continuation], ignore_index=True)


def write_csv(df: pd.DataFrame, tmp_path) -> "Path":
    path = tmp_path / "m1.csv"
    out = df.copy()
    out["time"] = out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False)
    return path


# --- Data loading / resampling ------------------------------------------


def test_replay_can_load_sample_csv(tmp_path):
    df = build_trend_series("up", bars=10)
    path = write_csv(df, tmp_path)

    loaded = load_ohlcv_csv(path)

    assert list(loaded.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(loaded) == 10


def test_load_ohlcv_csv_missing_column_raises(tmp_path):
    df = build_trend_series("up", bars=5).drop(columns=["volume"])
    path = tmp_path / "bad.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="volume"):
        load_ohlcv_csv(path)


def test_replay_resamples_m1_to_m5_and_m15():
    # 15 M1 bars -> exactly 3 M5 buckets and 1 M15 bucket, with hand-checkable OHLC.
    times = pd.date_range("2026-01-01 00:00", periods=15, freq="1min", tz="UTC")
    closes = [float(i) for i in range(15)]
    df = pd.DataFrame(
        {
            "time": times,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1.0] * 15,
        }
    )

    m5 = resample_ohlcv(df, "5min")
    m15 = resample_ohlcv(df, "15min")

    assert len(m5) == 3
    assert len(m15) == 1
    # first M5 bucket covers bars 0-4: open=first(0), high=max(0..4)+1=5, low=min-1=-1, close=last(4)
    assert m5.iloc[0]["open"] == 0.0
    assert m5.iloc[0]["high"] == 5.0
    assert m5.iloc[0]["low"] == -1.0
    assert m5.iloc[0]["close"] == 4.0
    assert m5.iloc[0]["volume"] == 5.0
    # the single M15 bucket covers all 15 bars
    assert m15.iloc[0]["open"] == 0.0
    assert m15.iloc[0]["close"] == 14.0
    assert m15.iloc[0]["volume"] == 15.0


# --- SimulatedBroker direct tests ----------------------------------------


def test_simulated_broker_can_open_close_and_modify_stop_loss(tmp_path):
    df = build_trend_series("up", bars=5)
    cfg = make_backtest_config(write_csv(df, tmp_path))
    broker = SimulatedBroker(cfg, strategy_id=STRATEGY_ID)
    broker.load_data(df, df, df)
    broker.set_current_bar(2)

    position = broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=95.0)
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 1

    broker.modify_stop_loss(position.position_id, 99.0)
    assert broker.get_open_position(SYMBOL, STRATEGY_ID).stop_loss == 99.0

    broker.close_position(position.position_id, CloseReason.TP_CASH)
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0
    assert len(broker.get_trade_history()) == 1


def test_stop_loss_simulation_works_for_buy(tmp_path):
    df = build_trend_series("up", bars=5)
    cfg = make_backtest_config(write_csv(df, tmp_path))
    broker = SimulatedBroker(cfg, strategy_id=STRATEGY_ID)
    broker.load_data(df, df, df)
    broker.set_current_bar(2)
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=df["low"].iloc[3] + 100)  # force a hit

    broker.set_current_bar(3)
    closed_trade = broker.check_and_apply_stop_loss()

    assert closed_trade is not None
    assert closed_trade.reason is CloseReason.HARD_SL
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0


def test_stop_loss_simulation_works_for_sell(tmp_path):
    df = build_trend_series("up", bars=5)
    cfg = make_backtest_config(write_csv(df, tmp_path))
    broker = SimulatedBroker(cfg, strategy_id=STRATEGY_ID)
    broker.load_data(df, df, df)
    broker.set_current_bar(2)
    broker.open_market_order(SYMBOL, Side.SELL, quantity=1.0, stop_loss=df["high"].iloc[3] - 100)  # force a hit

    broker.set_current_bar(3)
    closed_trade = broker.check_and_apply_stop_loss()

    assert closed_trade is not None
    assert closed_trade.reason is CloseReason.HARD_SL
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0


def test_no_stop_loss_hit_returns_none(tmp_path):
    df = build_trend_series("up", bars=5)
    cfg = make_backtest_config(write_csv(df, tmp_path))
    broker = SimulatedBroker(cfg, strategy_id=STRATEGY_ID)
    broker.load_data(df, df, df)
    broker.set_current_bar(2)
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=-1000.0)  # unreachable

    broker.set_current_bar(3)
    assert broker.check_and_apply_stop_loss() is None
    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 1


# --- Full replay integration ---------------------------------------------


def test_replay_does_not_open_more_than_one_position(tmp_path):
    strategy_cfg = make_strategy_config()
    base = build_trend_series("up", bars=WARMUP_BARS)
    m1 = append_pullback_bar(base, "up")
    m1 = append_continuation(m1, "up", bars=30)
    # a second pullback-like dip further out -- must NOT open a second position while one is open
    m1 = append_pullback_bar(m1, "up")
    m1 = append_continuation(m1, "up", bars=20)

    backtest_cfg = make_backtest_config(write_csv(m1, tmp_path))
    result = run_replay(backtest_cfg, strategy_cfg, strategy_id=STRATEGY_ID)  # must not raise

    open_count = sum(1 for o in result.order_log if o["type"] == "OPEN")
    close_count = sum(1 for o in result.order_log if o["type"] == "CLOSE")
    assert open_count - close_count <= 1


def test_replay_writes_journal_and_produces_metrics(tmp_path):
    strategy_cfg = make_strategy_config(tp_cash=1.0, default_quantity=1.0)
    base = build_trend_series("up", bars=WARMUP_BARS)
    m1 = append_pullback_bar(base, "up")
    m1 = append_continuation(m1, "up", bars=30, step=0.15)  # steep enough to reach tp_cash=1.0 quickly

    backtest_cfg = make_backtest_config(write_csv(m1, tmp_path))
    result = run_replay(backtest_cfg, strategy_cfg, strategy_id=STRATEGY_ID)

    assert len(result.trade_history) >= 1
    rows = read_journal_rows(backtest_cfg.output_journal_csv)
    assert len(rows) == len(result.trade_history)
    assert rows[0]["reason_close"] == "TP_CASH"

    metrics = calculate_metrics(rows)
    assert metrics.total_trades == len(result.trade_history)
    assert metrics.wins >= 1


def test_replay_respects_max_trades_per_day(tmp_path):
    strategy_cfg = make_strategy_config(max_trades_per_day=1, tp_cash=1.0, default_quantity=1.0)
    base = build_trend_series("up", bars=WARMUP_BARS)
    m1 = append_pullback_bar(base, "up")
    m1 = append_continuation(m1, "up", bars=30, step=0.15)  # first trade reaches TP here
    m1 = append_pullback_bar(m1, "up")  # a second, independent entry opportunity
    m1 = append_continuation(m1, "up", bars=30, step=0.15)

    backtest_cfg = make_backtest_config(write_csv(m1, tmp_path))
    result = run_replay(backtest_cfg, strategy_cfg, strategy_id=STRATEGY_ID)

    assert len(result.trade_history) == 1  # the second opportunity is blocked by max_trades_per_day


def test_replay_respects_daily_max_loss(tmp_path):
    strategy_cfg = make_strategy_config(daily_max_loss=-0.05, cooldown_after_sl_bars=0)
    df = build_trend_series("up", bars=10)
    backtest_cfg = make_backtest_config(write_csv(df, tmp_path))

    broker = SimulatedBroker(backtest_cfg, strategy_id=STRATEGY_ID)
    broker.load_data(df, df, df)
    state = LoopState(daily_stats=DailyStats(trading_day=df["time"].iloc[0].date().isoformat()))

    # Manually open a position (bypassing organic signal discovery -- this test targets the
    # guard, not entry detection) and force a REAL loss: the stop sits right at bar 3's own
    # low, just below the fill price, so hitting it produces a small negative P&L.
    broker.set_current_bar(2)
    broker.open_market_order(SYMBOL, Side.BUY, quantity=1.0, stop_loss=df["low"].iloc[3])

    # run_iteration observes the open position once (as a live loop would), so it can
    # later notice the position vanishing when the broker applies the stop-loss below.
    run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=backtest_cfg.output_journal_csv)

    broker.set_current_bar(3)
    closed_trade = broker.check_and_apply_stop_loss()
    assert closed_trade is not None
    assert closed_trade.realized_pnl_cash < 0  # a real loss, not the inverted-SL win this used to assert

    # run_iteration should detect the vanished position and record the HARD_SL loss,
    # updating daily_stats -- which the guard check below then relies on.
    run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=backtest_cfg.output_journal_csv)
    assert state.daily_stats.realized_pnl_cash <= strategy_cfg.daily_max_loss

    # Guard should now block any further trading for the rest of the day.
    broker.set_current_bar(4)
    run_iteration(broker, strategy_cfg, STRATEGY_ID, state, journal_path=backtest_cfg.output_journal_csv)

    assert broker.get_position_count(SYMBOL, STRATEGY_ID) == 0
