"""Typed configuration loading for strategy.yaml, mt5.yaml, binance.yaml, and .env secrets.

Non-secret behavior lives in config/*.yaml; secrets (API keys, account credentials) only
ever come from environment variables / .env, never from YAML or source code.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, model_validator

logger = logging.getLogger("trend_only_scalper.config")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class StrategyConfig(BaseModel):
    symbol: str
    account_mode: Literal["demo", "live"] = "demo"

    trend_timeframe: str = "M15"
    confirm_timeframe: str = "M5"
    entry_timeframe: str = "M1"

    ema_fast: int = 20
    ema_slow: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14

    pullback_atr_tolerance: float = 0.25
    abnormal_candle_atr_multiple: float = 2.0
    min_atr_spread_multiple: float = 3.0
    swing_lookback: int = 20
    sl_atr_buffer: float = 0.5

    # MVP fixed order size; risk-based position sizing from stop distance is future work.
    default_quantity: float = 1.0

    tp_cash: float = 1.50
    breakeven_trigger_cash: float = 0.70
    breakeven_lock_cash: float = 0.05

    daily_profit_target: float = 200.0
    daily_max_loss: float = -30.0
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 150

    cooldown_after_tp_bars: int = 1
    cooldown_after_be_bars: int = 2
    cooldown_after_sl_bars: int = 5

    one_position_only: bool = True
    allow_counter_trend: bool = False
    allow_grid: bool = False
    allow_martingale: bool = False
    allow_averaging_down: bool = False

    dry_run: bool = True

    @model_validator(mode="after")
    def _forbid_dangerous_config(self) -> "StrategyConfig":
        """Fail loudly at startup rather than silently running with an unsafe or
        nonsensical configuration -- this project only ever implements one-position,
        trend-only trading with a hard stop-loss on every trade.
        """
        violations: list[str] = []

        if not self.one_position_only:
            violations.append("one_position_only must be true")
        if self.allow_counter_trend:
            violations.append("allow_counter_trend must be false")
        if self.allow_grid:
            violations.append("allow_grid must be false")
        if self.allow_martingale:
            violations.append("allow_martingale must be false")
        if self.allow_averaging_down:
            violations.append("allow_averaging_down must be false")
        if self.daily_max_loss >= 0:
            violations.append("daily_max_loss must be negative (a real cash loss limit)")
        if self.max_trades_per_day <= 0:
            violations.append("max_trades_per_day must be positive")
        if self.tp_cash <= 0:
            violations.append("tp_cash must be positive")
        if self.breakeven_trigger_cash <= 0:
            violations.append("breakeven_trigger_cash must be positive")
        if self.swing_lookback < 1:
            violations.append("swing_lookback must be >= 1 -- hard stop-loss requires a swing lookback window")
        if self.sl_atr_buffer < 0:
            violations.append("sl_atr_buffer must be >= 0")

        if violations:
            raise ValueError(
                f"strategy.yaml has unsafe or invalid configuration: {violations}. "
                "This bot only supports one-position, trend-only trading with a hard stop-loss."
            )
        return self


class MT5Config(BaseModel):
    symbol: str
    magic: int
    lot: float = 0.01
    deviation: int = 20
    max_spread_points: int = 30
    timeout_ms: int = 10000
    filling_type: Literal["IOC", "FOK", "RETURN"] = "IOC"
    order_comment: str = "trend_only_scalper"

    # Safety: real orders are blocked (simulated locally instead) until this is explicitly true.
    allow_live_trading: bool = False

    # Names of the environment variables holding secrets -- never the secrets themselves.
    login_env: str = "MT5_LOGIN"
    password_env: str = "MT5_PASSWORD"
    server_env: str = "MT5_SERVER"
    terminal_path_optional: str | None = None

    # Secrets, resolved from the environment (via the *_env indirection above) at load
    # time -- never stored in YAML.
    login: int | None = Field(default=None, exclude=True)
    password: str | None = Field(default=None, exclude=True)
    server: str | None = Field(default=None, exclude=True)
    path: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _forbid_invalid_config(self) -> "MT5Config":
        """Fail loudly at startup on nonsensical order/connection parameters, rather than
        letting MT5 reject (or worse, silently misinterpret) them at order-send time.
        """
        violations: list[str] = []

        if self.lot <= 0:
            violations.append("lot must be positive")
        if self.deviation < 0:
            violations.append("deviation must be >= 0")
        if self.max_spread_points <= 0:
            violations.append("max_spread_points must be positive")
        if self.timeout_ms <= 0:
            violations.append("timeout_ms must be positive")

        if violations:
            raise ValueError(f"mt5.yaml has invalid configuration: {violations}")
        return self


class BinanceConfig(BaseModel):
    symbol: str
    market_type: Literal["futures", "spot"] = "futures"
    testnet: bool = True
    dry_run: bool = True

    # Safety: real orders are blocked (simulated locally instead) until this is explicitly true.
    allow_live_trading: bool = False

    # Names of the environment variables holding secrets -- never the secrets themselves.
    api_key_env: str = "BINANCE_API_KEY"
    api_secret_env: str = "BINANCE_API_SECRET"

    leverage: int = 2
    quantity: float = 0.001               # fixed order size in base-asset units
    max_cost_ratio_to_tp: float = 0.5     # warn/skip if est. round-trip fee > this fraction of tp_cash
    fee_rate_estimate: float = 0.0004     # taker fee rate per side, e.g. 0.04%
    recv_window: int = 5000

    # Secrets, resolved from the environment (via the *_env indirection above) at load
    # time -- never stored in YAML. SecretStr keeps the value out of repr()/str()/logs;
    # callers must use .get_secret_value() to read the plaintext.
    api_key: SecretStr | None = Field(default=None, exclude=True)
    api_secret: SecretStr | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _forbid_invalid_config(self) -> "BinanceConfig":
        """Fail loudly at startup on nonsensical order/fee parameters, rather than letting
        ccxt reject (or worse, silently misinterpret) them at order-send time.
        """
        violations: list[str] = []

        if self.leverage < 1:
            violations.append("leverage must be >= 1")
        if self.quantity <= 0:
            violations.append("quantity must be positive")
        if self.fee_rate_estimate < 0:
            violations.append("fee_rate_estimate must be >= 0")
        if self.max_cost_ratio_to_tp <= 0:
            violations.append("max_cost_ratio_to_tp must be positive")
        if self.recv_window <= 0:
            violations.append("recv_window must be positive")

        if violations:
            raise ValueError(f"binance.yaml has invalid configuration: {violations}")
        return self


class EnvSecrets(BaseModel):
    broker_backend: Literal["mt5", "binance", "mock"] = "mock"
    dry_run: bool = True

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None

    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_testnet: bool = True


class AppConfig(BaseModel):
    """Aggregate config for a single run: strategy rules + the selected broker's settings."""

    strategy: StrategyConfig
    env: EnvSecrets
    mt5: MT5Config | None = None
    binance: BinanceConfig | None = None

    @property
    def dry_run(self) -> bool:
        # Env DRY_RUN=false can only relax strategy.yaml's dry_run if both explicitly agree;
        # if either source asks for a safe/dry run, honor it.
        return self.strategy.dry_run or self.env.dry_run


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping at the top level")
    return data


def _parse_bool_env(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_env(env_path: Path | str = DEFAULT_ENV_PATH) -> EnvSecrets:
    """Load secrets and run-mode flags from a .env file (if present) and the environment."""
    env_path = Path(env_path)
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    mt5_login_raw = os.getenv("MT5_LOGIN")
    return EnvSecrets(
        broker_backend=os.getenv("BROKER_BACKEND", "mock"),  # type: ignore[arg-type]
        dry_run=_parse_bool_env(os.getenv("DRY_RUN"), default=True),
        mt5_login=int(mt5_login_raw) if mt5_login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        mt5_path=os.getenv("MT5_PATH") or None,
        binance_api_key=os.getenv("BINANCE_API_KEY") or None,
        binance_api_secret=os.getenv("BINANCE_API_SECRET") or None,
        binance_testnet=_parse_bool_env(os.getenv("BINANCE_TESTNET"), default=True),
    )


def load_strategy_config(path: Path | str = DEFAULT_CONFIG_DIR / "strategy.yaml") -> StrategyConfig:
    return StrategyConfig.model_validate(_read_yaml(Path(path)))


def load_mt5_config(
    path: Path | str = DEFAULT_CONFIG_DIR / "mt5.yaml",
    env: EnvSecrets | None = None,
) -> MT5Config:
    data = _read_yaml(Path(path))
    if env is None:
        load_env()  # ensure .env has been loaded into os.environ as a side effect

    login_raw = os.getenv(data.get("login_env", "MT5_LOGIN"))
    data.update(
        login=int(login_raw) if login_raw else None,
        password=os.getenv(data.get("password_env", "MT5_PASSWORD")) or None,
        server=os.getenv(data.get("server_env", "MT5_SERVER")) or None,
        path=data.get("terminal_path_optional"),
    )
    cfg = MT5Config.model_validate(data)
    if cfg.lot != MT5Config.model_fields["lot"].default:
        logger.warning(
            "mt5.yaml's 'lot' field is set to %s, but it is never used for order sizing -- "
            "strategy.yaml's default_quantity is the real sizing knob for every broker. "
            "Edit default_quantity instead, or this change has no effect.",
            cfg.lot,
        )
    return cfg


def load_binance_config(
    path: Path | str = DEFAULT_CONFIG_DIR / "binance.yaml",
    env: EnvSecrets | None = None,
) -> BinanceConfig:
    data = _read_yaml(Path(path))
    if env is None:
        load_env()  # ensure .env has been loaded into os.environ as a side effect

    data.update(
        api_key=os.getenv(data.get("api_key_env", "BINANCE_API_KEY")) or None,
        api_secret=os.getenv(data.get("api_secret_env", "BINANCE_API_SECRET")) or None,
    )
    # Merge is (yaml testnet) OR (env BINANCE_TESTNET), and _parse_bool_env defaults the env
    # side to True when BINANCE_TESTNET is unset -- so the result is only ever False when
    # BOTH binance.yaml's testnet: false AND an explicit BINANCE_TESTNET=false (or 0/no/off)
    # agree. Either side alone left at its safer default keeps testnet=True. This is
    # intentional: going to mainnet should never be a one-sided accident.
    env_testnet = _parse_bool_env(os.getenv("BINANCE_TESTNET"), default=True)
    data["testnet"] = data.get("testnet", True) or env_testnet
    cfg = BinanceConfig.model_validate(data)
    if cfg.quantity != BinanceConfig.model_fields["quantity"].default:
        logger.warning(
            "binance.yaml's 'quantity' field is set to %s, but it is never used for order "
            "sizing -- strategy.yaml's default_quantity is the real sizing knob for every "
            "broker. Edit default_quantity instead, or this change has no effect.",
            cfg.quantity,
        )
    return cfg


def load_app_config(
    backend: str | None = None,
    config_dir: Path | str = DEFAULT_CONFIG_DIR,
    env_path: Path | str = DEFAULT_ENV_PATH,
) -> AppConfig:
    """Load the full config for one run. `backend` overrides BROKER_BACKEND from .env."""
    config_dir = Path(config_dir)
    env = load_env(env_path)
    backend = backend or env.broker_backend

    strategy = load_strategy_config(config_dir / "strategy.yaml")
    mt5_cfg = load_mt5_config(config_dir / "mt5.yaml", env=env) if backend == "mt5" else None
    binance_cfg = (
        load_binance_config(config_dir / "binance.yaml", env=env) if backend == "binance" else None
    )
    return AppConfig(strategy=strategy, env=env, mt5=mt5_cfg, binance=binance_cfg)
