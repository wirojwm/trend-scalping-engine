"""Phase 1 smoke tests: config loading works without any broker credentials."""

import pytest

from trend_only_scalper.config import (
    load_app_config,
    load_binance_config,
    load_mt5_config,
    load_strategy_config,
)

CONFIG_DIR = "config"


def test_load_strategy_config_defaults():
    strategy = load_strategy_config(f"{CONFIG_DIR}/strategy.yaml")
    assert strategy.symbol == "EURUSD"
    assert strategy.dry_run is True
    assert strategy.one_position_only is True
    assert strategy.tp_cash == 1.50
    assert strategy.daily_max_loss == -30.0


def test_strategy_config_rejects_anti_patterns():
    from trend_only_scalper.config import StrategyConfig

    with pytest.raises(ValueError):
        StrategyConfig(symbol="EURUSD", allow_grid=True)
    with pytest.raises(ValueError):
        StrategyConfig(symbol="EURUSD", one_position_only=False)


def test_load_mt5_config_without_env_secrets():
    mt5_cfg = load_mt5_config(f"{CONFIG_DIR}/mt5.yaml")
    assert mt5_cfg.symbol == "EURUSD"
    assert mt5_cfg.magic == 987001
    assert mt5_cfg.allow_live_trading is False
    assert mt5_cfg.login is None  # no .env present in test environment


def test_mt5_config_rejects_invalid_values():
    from trend_only_scalper.config import MT5Config

    with pytest.raises(ValueError):
        MT5Config(symbol="EURUSD", magic=1, lot=0)
    with pytest.raises(ValueError):
        MT5Config(symbol="EURUSD", magic=1, deviation=-1)
    with pytest.raises(ValueError):
        MT5Config(symbol="EURUSD", magic=1, max_spread_points=0)
    with pytest.raises(ValueError):
        MT5Config(symbol="EURUSD", magic=1, timeout_ms=0)


def test_load_binance_config_defaults_to_testnet():
    binance_cfg = load_binance_config(f"{CONFIG_DIR}/binance.yaml")
    assert binance_cfg.market_type == "futures"
    assert binance_cfg.testnet is True
    assert binance_cfg.allow_live_trading is False


def test_binance_config_rejects_invalid_values():
    from trend_only_scalper.config import BinanceConfig

    with pytest.raises(ValueError):
        BinanceConfig(symbol="BTCUSDT", leverage=0)
    with pytest.raises(ValueError):
        BinanceConfig(symbol="BTCUSDT", quantity=0)
    with pytest.raises(ValueError):
        BinanceConfig(symbol="BTCUSDT", fee_rate_estimate=-0.001)
    with pytest.raises(ValueError):
        BinanceConfig(symbol="BTCUSDT", max_cost_ratio_to_tp=0)
    with pytest.raises(ValueError):
        BinanceConfig(symbol="BTCUSDT", recv_window=0)


def test_load_app_config_mock_backend():
    app_config = load_app_config(backend="mock", config_dir=CONFIG_DIR, env_path=".env.missing")
    assert app_config.mt5 is None
    assert app_config.binance is None
    assert app_config.dry_run is True
