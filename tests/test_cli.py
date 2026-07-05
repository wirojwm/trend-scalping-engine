"""Tests for the unified CLI: dispatch/initialization, safety-report, and the config
validation every subcommand relies on. mt5-demo/binance-demo's broker-specific behavior
is covered by their own contract tests (test_mt5_broker_contract.py /
test_binance_broker_contract.py); these tests focus on what's CLI-specific.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from trend_only_scalper import cli
from trend_only_scalper.config import StrategyConfig, load_binance_config, load_mt5_config

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeBroker:
    """Minimal stand-in for connect()/disconnect() -- _run_continuous_loop only needs these."""

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass


def _subcommand_names() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None):
            return set(action.choices.keys())
    raise AssertionError("no subparsers action found")


# --- dry-run can initialize, no credentials required -----------------------


def test_cli_dry_run_initializes_and_runs(tmp_path, capsys):
    args = cli.build_parser().parse_args(
        [
            "dry-run",
            "--config", "config/strategy.yaml",
            "--iterations", "3",
            "--journal-path", str(tmp_path / "journal.csv"),
        ]
    )
    assert cli.cmd_dry_run(args) == 0
    assert "Safety Report" in capsys.readouterr().out


def test_cli_dry_run_requires_no_broker_credentials(monkeypatch, tmp_path):
    for var in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
        monkeypatch.delenv(var, raising=False)
    args = cli.build_parser().parse_args(
        ["dry-run", "--iterations", "1", "--journal-path", str(tmp_path / "journal.csv")]
    )
    assert cli.cmd_dry_run(args) == 0


# --- safety-report can run ------------------------------------------------


def test_cli_safety_report_runs(capsys):
    args = cli.build_parser().parse_args(["safety-report", "--strategy", "config/strategy.yaml"])
    assert cli.cmd_safety_report(args) == 0
    out = capsys.readouterr().out
    assert "one_position_only" in out
    assert "daily_max_loss" in out
    assert "broker_backend" in out
    assert "allow_live_trading" not in out  # mock backend has no live-trading concept


def test_cli_safety_report_shows_allow_live_trading_for_mt5(capsys):
    args = cli.build_parser().parse_args(
        [
            "safety-report", "--strategy", "config/strategy.yaml", "--backend", "mt5",
            "--broker", "config/mt5.yaml", "--env-file", ".env.missing",
        ]
    )
    assert cli.cmd_safety_report(args) == 0
    assert "allow_live_trading:      False" in capsys.readouterr().out


def test_cli_safety_report_shows_allow_live_trading_for_binance(capsys):
    args = cli.build_parser().parse_args(
        [
            "safety-report", "--strategy", "config/strategy.yaml", "--backend", "binance",
            "--broker", "config/binance.yaml", "--env-file", ".env.missing",
        ]
    )
    assert cli.cmd_safety_report(args) == 0
    assert "allow_live_trading:      False" in capsys.readouterr().out


# --- dangerous config rejected / safe config accepted ----------------------


@pytest.mark.parametrize(
    "overrides,expected_match",
    [
        ({"allow_counter_trend": True}, "allow_counter_trend"),
        ({"allow_grid": True}, "allow_grid"),
        ({"allow_martingale": True}, "allow_martingale"),
        ({"allow_averaging_down": True}, "allow_averaging_down"),
        ({"one_position_only": False}, "one_position_only"),
        ({"daily_max_loss": 0.0}, "daily_max_loss"),
        ({"daily_max_loss": 5.0}, "daily_max_loss"),
        ({"max_trades_per_day": 0}, "max_trades_per_day"),
        ({"tp_cash": 0.0}, "tp_cash"),
        ({"breakeven_trigger_cash": -1.0}, "breakeven_trigger_cash"),
        ({"swing_lookback": 0}, "swing_lookback"),
        ({"sl_atr_buffer": -0.1}, "sl_atr_buffer"),
    ],
)
def test_dangerous_config_is_rejected(overrides, expected_match):
    with pytest.raises(ValueError, match=expected_match):
        StrategyConfig(symbol="EURUSD", **overrides)


def test_safe_config_is_accepted():
    cfg = StrategyConfig(symbol="EURUSD")
    assert cfg.one_position_only is True
    assert cfg.daily_max_loss < 0
    assert cfg.tp_cash > 0
    assert cfg.breakeven_trigger_cash > 0
    assert cfg.swing_lookback >= 1


def test_production_strategy_yaml_is_safe_config():
    # The checked-in config/strategy.yaml itself must pass -- not just hand-built examples.
    from trend_only_scalper.config import load_strategy_config

    cfg = load_strategy_config("config/strategy.yaml")
    assert cfg.one_position_only is True


# --- Live trading blocked unless explicitly enabled -------------------


def test_mt5_config_defaults_block_live_trading():
    assert load_mt5_config("config/mt5.yaml").allow_live_trading is False


def test_binance_config_defaults_block_live_trading():
    cfg = load_binance_config("config/binance.yaml")
    assert cfg.allow_live_trading is False
    assert cfg.testnet is True


# --- Continuous loop survives transient errors, gives up on persistent ones ------------


def test_continuous_loop_recovers_from_transient_errors(monkeypatch):
    call_count = {"n": 0}

    def flaky_run_iteration(broker, cfg, strategy_id, state, journal_path=None):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError("transient broker error")
        return state

    monkeypatch.setattr(cli, "run_iteration", flaky_run_iteration)
    args = argparse.Namespace(iterations=4, loop_interval=0.0, journal_path="unused.csv")

    cli._run_continuous_loop(_FakeBroker(), StrategyConfig(symbol="EURUSD"), args)

    assert call_count["n"] == 4  # kept going despite 2 failures, completed all 4 requested


def test_continuous_loop_stops_after_too_many_consecutive_errors(monkeypatch):
    call_count = {"n": 0}

    def always_fails(broker, cfg, strategy_id, state, journal_path=None):
        call_count["n"] += 1
        raise RuntimeError("permanently broken")

    monkeypatch.setattr(cli, "run_iteration", always_fails)
    args = argparse.Namespace(iterations=0, loop_interval=0.0, journal_path="unused.csv")

    with pytest.raises(RuntimeError, match="permanently broken"):
        cli._run_continuous_loop(_FakeBroker(), StrategyConfig(symbol="EURUSD"), args)

    assert call_count["n"] == cli.MAX_CONSECUTIVE_ITERATION_ERRORS


# --- README commands match implemented CLI ------------------------------


def test_readme_cli_commands_match_implemented_subcommands():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    subcommands = _subcommand_names()

    matches = re.findall(r"trend_only_scalper\.cli\s+([a-z0-9-]+)", readme)
    assert matches, "README should document `python -m trend_only_scalper.cli <command>` examples"
    for name in matches:
        assert name in subcommands, f"README references unknown CLI command: {name!r}"

    # And every implemented subcommand should be documented at least once.
    for name in subcommands:
        assert name in matches, f"CLI command {name!r} is not documented anywhere in README.md"
