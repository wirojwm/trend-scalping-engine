"""Unit tests for the CSV trade journal writer/reader, plus an integration check that the
bot loop itself produces journal output when a trade closes.
"""

import csv
from datetime import datetime

import pandas as pd

from trend_only_scalper.brokers.mock_broker import MockBroker
from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.indicators import add_atr, add_ema
from trend_only_scalper.journal import JournalRow, read_journal_rows, write_journal_row
from trend_only_scalper.main import run_iteration
from trend_only_scalper.models import DailyStats, LoopState

SYMBOL = "EURUSD"
STRATEGY_ID = "trend_only_scalper"


def make_row(**overrides) -> JournalRow:
    defaults = dict(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        strategy_id=STRATEGY_ID,
        broker="MockBroker",
        symbol=SYMBOL,
        timeframe_entry="M1",
        side="buy",
        quantity=1.0,
        entry_price=100.0,
        exit_price=101.5,
        stop_loss_initial=98.5,
        stop_loss_final=100.05,
        realized_pnl=1.5,
        fees_or_cost=0.02,
        reason_open="m1_pullback_bounce",
        reason_close="TP_CASH",
        m15_trend="up",
        m5_confirmation="up",
        m1_signal="buy",
        tp_cash=1.5,
        breakeven_trigger_cash=0.7,
        daily_pnl_after_trade=1.5,
        consecutive_losses_after_trade=0,
        trades_today=1,
        dry_run=True,
    )
    defaults.update(overrides)
    return JournalRow(**defaults)


# --- write_journal_row / read_journal_rows --------------------------------


def test_write_journal_row_creates_csv_file(tmp_path):
    path = tmp_path / "journal.csv"
    write_journal_row(path, make_row())
    assert path.exists()


def test_write_journal_row_appends_without_overwriting(tmp_path):
    path = tmp_path / "journal.csv"
    write_journal_row(path, make_row(reason_close="TP_CASH"))
    write_journal_row(path, make_row(reason_close="HARD_SL"))

    rows = read_journal_rows(path)
    assert len(rows) == 2
    assert rows[0]["reason_close"] == "TP_CASH"
    assert rows[1]["reason_close"] == "HARD_SL"


def test_journal_header_written_exactly_once(tmp_path):
    path = tmp_path / "journal.csv"
    write_journal_row(path, make_row())
    write_journal_row(path, make_row())

    with path.open("r", newline="", encoding="utf-8") as fh:
        raw_rows = list(csv.reader(fh))

    header_rows = [r for r in raw_rows if r and r[0] == "timestamp"]
    assert len(header_rows) == 1
    assert len(raw_rows) == 3  # 1 header + 2 data rows


def test_read_journal_rows_returns_empty_list_when_file_missing(tmp_path):
    assert read_journal_rows(tmp_path / "does_not_exist.csv") == []


def test_journal_row_contains_all_required_fields(tmp_path):
    path = tmp_path / "journal.csv"
    write_journal_row(path, make_row())
    rows = read_journal_rows(path)

    expected_fields = {
        "timestamp", "strategy_id", "broker", "symbol", "timeframe_entry", "side",
        "quantity", "entry_price", "exit_price", "stop_loss_initial", "stop_loss_final",
        "realized_pnl", "fees_or_cost", "reason_open", "reason_close", "m15_trend",
        "m5_confirmation", "m1_signal", "tp_cash", "breakeven_trigger_cash",
        "daily_pnl_after_trade", "consecutive_losses_after_trade", "trades_today", "dry_run",
    }
    assert expected_fields.issubset(rows[0].keys())


# --- Integration: the bot loop itself writes a journal row on close --------


def make_config(**overrides) -> StrategyConfig:
    return StrategyConfig(symbol=SYMBOL, **overrides)


def make_trend_series(bars: int = 80, start: float = 100.0, step: float = 0.1, noise: float = 0.05) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=bars, freq="1min")
    closes = [start + i * step for i in range(bars)]
    opens = [c - step for c in closes]
    highs = [max(o, c) + noise for o, c in zip(opens, closes)]
    lows = [min(o, c) - noise for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"time": times, "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * bars}
    )


def append_pullback_bar(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    enriched = add_atr(add_ema(df, fast_period=cfg.ema_fast, slow_period=cfg.ema_slow), period=cfg.atr_period)
    last = enriched.iloc[-1]
    ema, atr = last[f"ema_{cfg.ema_fast}"], last[f"atr_{cfg.atr_period}"]
    next_time = df["time"].iloc[-1] + pd.Timedelta(minutes=1)
    new_row = pd.DataFrame(
        [
            {
                "time": next_time,
                "open": ema - 0.2 * atr,
                "high": ema + 0.35 * atr,
                "low": ema - 0.1 * atr,
                "close": ema + 0.3 * atr,
                "volume": 100.0,
            }
        ]
    )
    return pd.concat([df, new_row], ignore_index=True)


def test_bot_loop_writes_journal_row_when_trade_closes(tmp_path):
    cfg = make_config()
    journal_path = tmp_path / "journal.csv"

    base = make_trend_series()
    m1_with_entry = append_pullback_bar(base, cfg)

    broker = MockBroker(symbol=cfg.symbol, strategy_id=STRATEGY_ID)
    broker.set_bars("M15", make_trend_series())
    broker.set_bars("M5", make_trend_series())
    broker.set_bars("M1", m1_with_entry)

    state = LoopState(daily_stats=DailyStats(trading_day="2026-01-01"))

    # Open the position.
    run_iteration(broker, cfg, STRATEGY_ID, state, journal_path=journal_path)
    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 1
    assert not journal_path.exists()  # nothing closed yet -- no journal row expected

    # Advance price enough to hit the cash take-profit, then run again to trigger the close.
    entry_price = m1_with_entry["close"].iloc[-1]
    tp_price = entry_price + cfg.tp_cash / cfg.default_quantity + 0.01
    m1_after_tp = pd.concat(
        [
            m1_with_entry,
            pd.DataFrame(
                [
                    {
                        "time": m1_with_entry["time"].iloc[-1] + pd.Timedelta(minutes=1),
                        "open": entry_price,
                        "high": tp_price + 0.1,
                        "low": entry_price - 0.1,
                        "close": tp_price,
                        "volume": 100.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    broker.set_bars("M1", m1_after_tp)

    run_iteration(broker, cfg, STRATEGY_ID, state, journal_path=journal_path)

    assert broker.get_position_count(cfg.symbol, STRATEGY_ID) == 0
    assert journal_path.exists()
    rows = read_journal_rows(journal_path)
    assert len(rows) == 1
    assert rows[0]["reason_close"] == "TP_CASH"
    assert rows[0]["symbol"] == cfg.symbol
