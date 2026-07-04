"""Replay historical OHLCV bars through the same broker-agnostic bot loop used for
dry-run/demo, producing a trade journal and metrics summary.

This is NOT a full backtesting engine: no vectorized execution, no portfolio/multi-symbol
handling, no tick-level order book simulation. It's a thin driver that feeds one bar at a
time to the same run_iteration() used everywhere else, so strategy behavior can be
sanity-checked against historical data before ever touching a real broker. It reuses --
never duplicates -- the indicator, strategy, risk, and journal modules: SimulatedBroker's
check_and_apply_stop_loss() closes a position exactly like a real broker's stop order
firing server-side would, and run_iteration() itself now detects and journals that kind
of broker-initiated close generically (see main.py's autonomous-close handling), so this
driver no longer needs its own copy of that bookkeeping or day-rollover reset logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from trend_only_scalper.backtest.data_loader import filter_date_range, load_ohlcv_csv, resample_ohlcv
from trend_only_scalper.backtest.simulated_broker import SimulatedBroker
from trend_only_scalper.config import StrategyConfig
from trend_only_scalper.main import run_iteration
from trend_only_scalper.models import ClosedTrade, DailyStats, LoopState


class BacktestConfig(BaseModel):
    input_csv: str
    symbol: str
    initial_equity: float = 10_000.0
    spread_points_or_price: float = 0.0002
    fee_rate: float = 0.0
    execution_mode: Literal["next_open"] = "next_open"
    slippage_points_or_price: float = 0.0
    start_date: str | None = None
    end_date: str | None = None
    output_journal_csv: str = "logs/backtest_journal.csv"


def load_backtest_config(path: str | Path) -> BacktestConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Backtest config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return BacktestConfig.model_validate(data)


@dataclass
class ReplayResult:
    bars_processed: int
    order_log: list[dict]
    trade_history: list[ClosedTrade]


def run_replay(
    backtest_cfg: BacktestConfig,
    strategy_cfg: StrategyConfig,
    strategy_id: str = "trend_only_scalper",
) -> ReplayResult:
    """Replay backtest_cfg.input_csv bar-by-bar through run_iteration(), returning the
    resulting order log and trade history. Journal rows accumulate at
    backtest_cfg.output_journal_csv as trades close.
    """
    m1 = load_ohlcv_csv(backtest_cfg.input_csv)
    m1 = filter_date_range(m1, backtest_cfg.start_date, backtest_cfg.end_date)
    if m1.empty:
        raise ValueError("Backtest: no M1 bars in the selected date range")

    m5 = resample_ohlcv(m1, "5min")
    m15 = resample_ohlcv(m1, "15min")

    broker = SimulatedBroker(backtest_cfg, strategy_id=strategy_id)
    broker.load_data(m1, m5, m15)

    state = LoopState(daily_stats=DailyStats(trading_day=m1["time"].iloc[0].date().isoformat()))

    for i in range(len(m1)):
        broker.set_current_bar(i)
        # Closes the position in the broker if its stop-loss was breached this bar --
        # exactly like a real exchange's stop order firing. run_iteration() below then
        # detects the vanished position generically and does all the bookkeeping
        # (journal, cooldown, trade/loss counters), the same as it would for MT5/Binance.
        broker.check_and_apply_stop_loss()
        run_iteration(broker, strategy_cfg, strategy_id, state, journal_path=backtest_cfg.output_journal_csv)

    return ReplayResult(
        bars_processed=len(m1),
        order_log=broker.get_order_log(),
        trade_history=broker.get_trade_history(),
    )
