"""Integration tests for run_iteration(): the full decision loop against MockBroker.

Bars are generated programmatically (a smooth trend for warm-up, with a pullback/rebound
bar appended whose exact geometry is derived from the *actual* computed EMA/ATR at that
point) rather than hand-guessed constants, so these tests exercise the same indicator
math run_iteration() itself uses.
"""

import pandas as pd

from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.indicators import add_atr, add_ema
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.main import run_iteration
from trend_only_scalper.models import CloseReason, DailyStats, LoopState, Side
from trend_only_scalper.risk.cooldown import start_cooldown

SYMBOL = "EURUSD"
STRATEGY_ID = "trend_only_scalper"
WARMUP_BARS = 80


def make_config(**overrides) -> StrategyConfig:
    return StrategyConfig(symbol=SYMBOL, **overrides)


def make_trend_series(
    direction: str, bars: int = WARMUP_BARS, start: float = 100.0, step: float = 0.1, noise: float = 0.05
) -> pd.DataFrame:
    """A smooth, steadily trending OHLCV series -- enough bars to warm up EMA50/MACD/ATR."""
    times = pd.date_range("2026-01-01 00:00", periods=bars, freq="1min")
    sign = 1 if direction == "up" else -1
    closes = [start + sign * i * step for i in range(bars)]
    opens = [c - sign * step for c in closes]
    highs = [max(o, c) + noise for o, c in zip(opens, closes)]
    lows = [min(o, c) - noise for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * bars}
    )


def append_pullback_bar(df: pd.DataFrame, cfg: StrategyConfig, direction: str) -> pd.DataFrame:
    """Append one bar engineered to trigger the M1 buy/sell trigger, sized from the
    EMA/ATR actually computed off `df` -- not guessed constants.
    """
    enriched = add_atr(add_ema(df, fast_period=cfg.ema_fast, slow_period=cfg.ema_slow), period=cfg.atr_period)
    last = enriched.iloc[-1]
    ema = last[f"ema_{cfg.ema_fast}"]
    atr = last[f"atr_{cfg.atr_period}"]
    next_time = df["time"].iloc[-1] + pd.Timedelta(minutes=1)

    if direction == "up":
        low, close, open_, high = ema - 0.1 * atr, ema + 0.3 * atr, ema - 0.2 * atr, ema + 0.35 * atr
    else:
        high, close, open_, low = ema + 0.1 * atr, ema - 0.3 * atr, ema + 0.2 * atr, ema - 0.35 * atr

    new_row = pd.DataFrame(
        [{"time": next_time, "open": open_, "high": high, "low": low, "close": close, "volume": 100.0}]
    )
    return pd.concat([df, new_row], ignore_index=True)


def seed_broker(cfg: StrategyConfig, m15: pd.DataFrame, m5: pd.DataFrame, m1: pd.DataFrame) -> MockBroker:
    broker = MockBroker(symbol=cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M15", m15)
    broker.set_bars("M5", m5)
    broker.set_bars("M1", m1)
    return broker


def make_state(**daily_stats_overrides) -> LoopState:
    return LoopState(daily_stats=DailyStats(trading_day="2026-01-01", **daily_stats_overrides))


def buy_ready_broker() -> MockBroker:
    cfg = make_config()
    m15 = make_trend_series("up")
    m5 = make_trend_series("up")
    m1 = append_pullback_bar(make_trend_series("up"), cfg, "up")
    return cfg, seed_broker(cfg, m15, m5, m1)


# --- Daily guard blocks --------------------------------------------------


def test_no_trade_when_daily_guard_blocks_on_profit_target():
    cfg, broker = buy_ready_broker()
    broker.seed_realized_pnl(cfg.daily_profit_target)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0


def test_no_trade_when_daily_guard_blocks_on_max_loss():
    cfg, broker = buy_ready_broker()
    broker.seed_realized_pnl(cfg.daily_max_loss)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0


# --- Cooldown --------------------------------------------------------------


def test_no_trade_during_active_cooldown():
    cfg, broker = buy_ready_broker()
    state = make_state()
    state.cooldown = start_cooldown(3)

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0
    assert state.cooldown.bars_remaining == 2  # ticked down, still active


# --- One-position-only / manage-instead-of-scan -----------------------


def test_manages_existing_position_instead_of_scanning_new_entry():
    cfg, broker = buy_ready_broker()
    stop_loss = broker.get_bars(cfg.symbol, "M1", 1)["close"].iloc[-1] - 10
    broker.open_market_order(cfg.symbol, Side.BUY, quantity=1.0, stop_loss=stop_loss)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 1
    assert sum(1 for o in broker.get_order_log() if o["type"] == "OPEN") == 1


def test_never_opens_more_than_one_position():
    cfg, broker = buy_ready_broker()
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)
    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 1

    run_iteration(broker, cfg, STRATEGY_ID, state)  # a position now exists; must only manage it
    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 1
    assert sum(1 for o in broker.get_order_log() if o["type"] == "OPEN") == 1


# --- Trend agreement -----------------------------------------------------


def test_no_trade_when_m15_and_m5_disagree():
    cfg = make_config()
    m15 = make_trend_series("up")
    m5 = make_trend_series("down")
    m1 = append_pullback_bar(make_trend_series("up"), cfg, "up")
    broker = seed_broker(cfg, m15, m5, m1)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0


def test_no_trade_when_no_m1_entry_signal():
    cfg = make_config()
    m15 = make_trend_series("up")
    m5 = make_trend_series("up")
    m1 = make_trend_series("up")  # no pullback engineered -- should not trigger
    broker = seed_broker(cfg, m15, m5, m1)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0


# --- Opening trades with the trend --------------------------------------


def test_opens_buy_when_m15_m5_up_and_m1_buy_signal():
    cfg, broker = buy_ready_broker()
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    position = broker.get_open_position(cfg.symbol, STRATEGY_ID)
    assert position is not None
    assert position.side is Side.BUY


def test_opens_sell_when_m15_m5_down_and_m1_sell_signal():
    cfg = make_config()
    m15 = make_trend_series("down")
    m5 = make_trend_series("down")
    m1 = append_pullback_bar(make_trend_series("down"), cfg, "down")
    broker = seed_broker(cfg, m15, m5, m1)
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    position = broker.get_open_position(cfg.symbol, STRATEGY_ID)
    assert position is not None
    assert position.side is Side.SELL


# --- Broker-initiated close detection (e.g. a real hard stop-loss firing) --------------


def test_run_iteration_detects_broker_initiated_close_and_records_hard_sl(tmp_path):
    """If the broker closes the position on its own (a real MT5/Binance hard stop-loss
    order firing server-side, simulated here by calling close_position() directly instead
    of through manage_position()), the NEXT run_iteration() call must still notice, journal
    it as HARD_SL, and start the SL cooldown -- not silently lose track of it.
    """
    cfg, broker = buy_ready_broker()
    state = make_state()
    journal_path = tmp_path / "journal.csv"

    run_iteration(broker, cfg, STRATEGY_ID, state, journal_path=str(journal_path))
    position = broker.get_open_position(cfg.symbol, STRATEGY_ID)
    assert position is not None
    assert not journal_path.exists()  # nothing closed yet

    # Simulate the broker's own stop-loss firing: the position closes through the
    # broker's native mechanism, bypassing run_iteration()/manage_position() entirely.
    broker.close_position(position.position_id, CloseReason.HARD_SL)
    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0

    run_iteration(broker, cfg, STRATEGY_ID, state, journal_path=str(journal_path))

    rows = read_journal_rows(str(journal_path))
    assert len(rows) == 1
    assert rows[0]["reason_close"] == "HARD_SL"
    assert state.daily_stats.trade_count == 1
    assert state.cooldown.bars_remaining == cfg.cooldown_after_sl_bars
    assert state.last_known_position is None
    assert state.open_trade_context is None


def test_broker_initiated_close_does_not_double_count_a_close_we_decided_ourselves():
    """A normal TP close (decided by manage_position(), closed via run_iteration() itself)
    must clear last_known_position so the NEXT call doesn't also treat it as an
    autonomous close.
    """
    cfg, broker = buy_ready_broker()
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)
    position = broker.get_open_position(cfg.symbol, STRATEGY_ID)
    assert position is not None

    # Force a cash-TP close on the next call by advancing price far past tp_cash.
    tp_price = position.entry_price + cfg.tp_cash / cfg.default_quantity + 1.0
    m1 = broker.get_bars(cfg.symbol, "M1", 1000)
    bumped = pd.concat(
        [
            m1,
            pd.DataFrame(
                [
                    {
                        "time": m1["time"].iloc[-1] + pd.Timedelta(minutes=1),
                        "open": position.entry_price, "high": tp_price + 0.1,
                        "low": position.entry_price - 0.1, "close": tp_price, "volume": 100.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    broker.set_bars("M1", bumped)

    run_iteration(broker, cfg, STRATEGY_ID, state)
    assert state.daily_stats.trade_count == 1  # closed via our own TP decision
    assert state.last_known_position is None

    # One more call with no position open: must NOT be mistaken for a second autonomous close.
    run_iteration(broker, cfg, STRATEGY_ID, state)
    assert state.daily_stats.trade_count == 1


# --- Daily stats reset at a calendar-day boundary --------------------------


def test_run_iteration_resets_daily_stats_on_new_calendar_day():
    cfg = make_config()
    m15 = make_trend_series("up")
    m5 = make_trend_series("up")
    m1 = make_trend_series("up")  # no signal needed; this test only checks the reset

    m1_new_day = m1.copy()
    m1_new_day["time"] = pd.date_range("2026-01-02 00:00", periods=len(m1), freq="1min")
    broker = seed_broker(cfg, m15, m5, m1_new_day)

    # Simulate yesterday having already hit every daily cap.
    state = make_state(trade_count=cfg.max_trades_per_day, consecutive_losses=cfg.max_consecutive_losses)

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert state.daily_stats.trading_day == "2026-01-02"
    assert state.daily_stats.trade_count == 0
    assert state.daily_stats.consecutive_losses == 0
