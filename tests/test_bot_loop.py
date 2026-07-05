"""Integration tests for run_iteration(): the full decision loop against MockBroker.

Bars are generated programmatically (a smooth trend for warm-up, with a pullback/rebound
bar appended whose exact geometry is derived from the *actual* computed EMA/ATR at that
point) rather than hand-guessed constants, so these tests exercise the same indicator
math run_iteration() itself uses.
"""

import pandas as pd
import pytest

from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.indicators import add_atr, add_ema, add_vwap
from trend_only_scalper.journal import read_journal_rows
from trend_only_scalper.main import BAR_LOOKBACK, VWAP_BAR_LOOKBACK, run_iteration
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


# --- M5 VWAP truncation (BAR_LOOKBACK must not cut off the M5 session) -----


class RecordingBroker(MockBroker):
    """A MockBroker that records the `limit` each get_bars() call was made with."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_bars_limits: dict[str, int] = {}

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self.get_bars_limits[timeframe] = limit
        return super().get_bars(symbol, timeframe, limit)


def test_m5_bars_are_fetched_with_a_full_day_floor_for_vwap():
    # BAR_LOOKBACK (100) is tuned for slow-EMA warmup, not for add_vwap()'s per-calendar-date
    # cumulative sum -- the M5 fetch must use a larger floor (VWAP_BAR_LOOKBACK) so a session
    # more than ~8 hours old never gets its early bars silently dropped before VWAP runs.
    cfg = make_config()
    broker = RecordingBroker(symbol=cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M15", make_trend_series("up"))
    broker.set_bars("M5", make_trend_series("up"))
    broker.set_bars("M1", make_trend_series("up"))
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_bars_limits["M5"] == max(BAR_LOOKBACK, VWAP_BAR_LOOKBACK)
    assert broker.get_bars_limits["M15"] == BAR_LOOKBACK
    assert broker.get_bars_limits["M1"] == BAR_LOOKBACK


def test_m5_vwap_matches_full_session_when_more_than_bar_lookback_bars_elapsed():
    # 150 M5 bars, all on the same calendar day: more than BAR_LOOKBACK (100), so a naive
    # tail(BAR_LOOKBACK) fetch would drop the first 50 bars of today's session and produce a
    # truncated (wrong) VWAP. VWAP_BAR_LOOKBACK (288) covers a full day, so nothing is dropped.
    cfg = make_config()
    m5_full = make_trend_series("up", bars=150)
    expected_vwap = add_vwap(m5_full)["vwap"].iloc[-1]

    broker = MockBroker(symbol=cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M5", m5_full)
    fetched_m5 = broker.get_bars(cfg.symbol, "M5", max(BAR_LOOKBACK, VWAP_BAR_LOOKBACK))
    actual_vwap = add_vwap(fetched_m5)["vwap"].iloc[-1]

    assert actual_vwap == pytest.approx(expected_vwap)
    # Sanity check: truncating to BAR_LOOKBACK alone (the old, buggy behavior) would have
    # produced a different value, proving the floor is actually necessary here.
    truncated_vwap = add_vwap(m5_full.tail(BAR_LOOKBACK))["vwap"].iloc[-1]
    assert truncated_vwap != pytest.approx(actual_vwap)


# --- Trading-cost gate (cash-equivalent, not raw price-unit) ---------------


def test_trade_blocked_when_cash_equivalent_trading_cost_meets_or_exceeds_tp():
    cfg, broker = buy_ready_broker()
    # cash-equivalent = trading_cost * default_quantity(1.0) * contract_size(1.0) == tp_cash
    broker.trading_cost = cfg.tp_cash / cfg.default_quantity
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0


def test_trade_allowed_when_price_unit_cost_is_large_but_cash_equivalent_is_small():
    # Simulates BTC/USDT scale: get_trading_cost() returns a large price-unit number (e.g.
    # 2 * fee_rate * price ~= 48 for a $60k BTC price), which used to be compared directly
    # against tp_cash and would wrongly block every trade. Converted to cash via a realistic
    # small default_quantity (0.01), the true cost (0.48) is well under tp_cash (1.50).
    # Bars are scaled to a BTC-like price/volatility so the (unrelated) ATR-vs-spread entry
    # filter -- which legitimately compares this same price-unit cost against ATR -- doesn't
    # itself reject the trade.
    cfg = make_config(default_quantity=0.01)
    m15 = make_trend_series("up", start=60_000.0, step=60.0, noise=60.0)
    m5 = make_trend_series("up", start=60_000.0, step=60.0, noise=60.0)
    m1 = append_pullback_bar(make_trend_series("up", start=60_000.0, step=60.0, noise=60.0), cfg, "up")
    broker = seed_broker(cfg, m15, m5, m1)
    broker.trading_cost = 48.0
    state = make_state()

    run_iteration(broker, cfg, STRATEGY_ID, state)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 1
