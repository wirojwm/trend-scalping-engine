"""Unit tests for metrics.calculate_metrics() and the safety report."""

import math

import pytest

from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.metrics import build_safety_report, calculate_metrics


def make_journal_row(realized_pnl: float, reason_close: str, timestamp: str = "2026-01-01T10:00:00") -> dict:
    return {"realized_pnl": str(realized_pnl), "reason_close": reason_close, "timestamp": timestamp}


# --- Basic counts and win rate -------------------------------------------


def test_calculate_metrics_total_trades():
    rows = [
        make_journal_row(1.5, "TP_CASH"),
        make_journal_row(-0.8, "HARD_SL"),
        make_journal_row(0.05, "BREAKEVEN_SL"),
    ]
    metrics = calculate_metrics(rows)
    assert metrics.total_trades == 3


def test_calculate_metrics_win_rate():
    rows = [
        make_journal_row(1.5, "TP_CASH"),
        make_journal_row(1.5, "TP_CASH"),
        make_journal_row(-0.8, "HARD_SL"),
        make_journal_row(0.05, "BREAKEVEN_SL"),
    ]
    metrics = calculate_metrics(rows)
    assert metrics.wins == 2
    assert metrics.losses == 1
    assert metrics.breakeven_count == 1
    assert metrics.win_rate == pytest.approx(2 / 4)


def test_calculate_metrics_empty_rows():
    metrics = calculate_metrics([])
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.profit_factor == 0.0


# --- Profit factor safety --------------------------------------------------


def test_profit_factor_is_finite_with_normal_wins_and_losses():
    rows = [make_journal_row(2.0, "TP_CASH"), make_journal_row(-1.0, "HARD_SL")]
    metrics = calculate_metrics(rows)
    assert metrics.profit_factor == pytest.approx(2.0)


def test_profit_factor_is_infinite_when_gross_loss_is_zero_and_profit_exists():
    rows = [make_journal_row(1.5, "TP_CASH"), make_journal_row(0.05, "BREAKEVEN_SL")]
    metrics = calculate_metrics(rows)
    assert math.isinf(metrics.profit_factor)


def test_profit_factor_is_zero_when_no_profit_and_no_loss():
    rows = [make_journal_row(0.0, "BREAKEVEN_SL")]
    metrics = calculate_metrics(rows)
    assert metrics.profit_factor == 0.0


# --- Max consecutive losses ------------------------------------------------


def test_max_consecutive_losses():
    rows = [
        make_journal_row(-1.0, "HARD_SL"),
        make_journal_row(-1.0, "HARD_SL"),
        make_journal_row(1.5, "TP_CASH"),
        make_journal_row(-1.0, "HARD_SL"),
        make_journal_row(-1.0, "HARD_SL"),
        make_journal_row(-1.0, "HARD_SL"),
    ]
    metrics = calculate_metrics(rows)
    assert metrics.max_consecutive_losses == 3


def test_max_consecutive_losses_with_no_losses():
    rows = [make_journal_row(1.5, "TP_CASH")]
    metrics = calculate_metrics(rows)
    assert metrics.max_consecutive_losses == 0


# --- Averages and gross figures ------------------------------------------


def test_gross_and_average_figures():
    rows = [
        make_journal_row(2.0, "TP_CASH"),
        make_journal_row(4.0, "TP_CASH"),
        make_journal_row(-1.0, "HARD_SL"),
        make_journal_row(-3.0, "HARD_SL"),
    ]
    metrics = calculate_metrics(rows)
    assert metrics.gross_profit == pytest.approx(6.0)
    assert metrics.gross_loss == pytest.approx(4.0)
    assert metrics.net_pnl == pytest.approx(2.0)
    assert metrics.average_win == pytest.approx(3.0)
    assert metrics.average_loss == pytest.approx(-2.0)
    assert metrics.average_trade_pnl == pytest.approx(0.5)


def test_trades_per_day():
    rows = [
        make_journal_row(1.0, "TP_CASH", timestamp="2026-01-01T10:00:00"),
        make_journal_row(1.0, "TP_CASH", timestamp="2026-01-01T11:00:00"),
        make_journal_row(1.0, "TP_CASH", timestamp="2026-01-02T10:00:00"),
    ]
    metrics = calculate_metrics(rows)
    assert metrics.trades_per_day == pytest.approx(1.5)  # 3 trades / 2 distinct days


# --- Safety report --------------------------------------------------------


def test_safety_report_contains_required_flags():
    cfg = StrategyConfig(symbol="EURUSD")
    report = build_safety_report(cfg, backend="mock")

    for expected in [
        "one_position_only",
        "counter_trend_disabled",
        "grid_disabled",
        "martingale_disabled",
        "averaging_down_disabled",
        "dry_run",
        "daily_max_loss",
        "max_consecutive_losses",
        "max_trades_per_day",
        "broker_backend",
    ]:
        assert expected in report


def test_safety_report_reflects_config_values():
    cfg = StrategyConfig(symbol="EURUSD", daily_max_loss=-50.0, max_trades_per_day=42)
    report = build_safety_report(cfg, backend="mt5")
    assert "-50.0" in report
    assert "42" in report
    assert "mt5" in report


def test_safety_report_omits_allow_live_trading_when_not_applicable():
    cfg = StrategyConfig(symbol="EURUSD")
    report = build_safety_report(cfg, backend="mock")
    assert "allow_live_trading" not in report


def test_safety_report_shows_allow_live_trading_true():
    cfg = StrategyConfig(symbol="EURUSD")
    report = build_safety_report(cfg, backend="mt5", allow_live_trading=True)
    assert "allow_live_trading:      True" in report


def test_safety_report_shows_allow_live_trading_false():
    cfg = StrategyConfig(symbol="EURUSD")
    report = build_safety_report(cfg, backend="binance", allow_live_trading=False)
    assert "allow_live_trading:      False" in report
