"""Broker-agnostic data models shared across the strategy, risk, and broker layers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


# A Position's side is the same BUY/SELL concept as a Signal's side; this alias exists
# so position-management code can name it in the domain-appropriate way.
PositionSide = Side


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    NONE = "none"


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"


class AccountMode(str, Enum):
    DEMO = "demo"
    LIVE = "live"


class CloseReason(str, Enum):
    TP_CASH = "TP_CASH"
    HARD_SL = "HARD_SL"
    BREAKEVEN_SL = "BREAKEVEN_SL"
    MANUAL = "MANUAL"
    DAILY_GUARD = "DAILY_GUARD"


class PositionAction(str, Enum):
    """What position_manager.manage_position() decided to do this bar."""

    NONE = "none"
    MODIFY_SL = "modify_sl"
    CLOSE = "close"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Signal:
    """An entry signal produced by entry_signal.detect()."""

    side: Side
    reason: str
    reference_price: float


@dataclass
class Position:
    """An open position tracked by the strategy, independent of broker representation."""

    position_id: str
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    stop_loss: float
    opened_at: datetime
    strategy_id: str = "trend_only_scalper"
    magic_number: int = 0
    breakeven_applied: bool = False


@dataclass
class ClosedTrade:
    """A record of a completed trade, used for daily guard accounting and the CSV journal."""

    position_id: str
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    exit_price: float
    opened_at: datetime
    closed_at: datetime
    realized_pnl_cash: float
    reason: CloseReason


@dataclass
class DailyStats:
    """Running totals used by the daily guard, reset at the start of each trading day."""

    trading_day: str
    realized_pnl_cash: float = 0.0
    trade_count: int = 0
    consecutive_losses: int = 0
    trades: list[ClosedTrade] = field(default_factory=list)


def save_daily_stats(path: str | Path, stats: DailyStats) -> None:
    """Persist the daily-guard counters (not the closed-trade list, already in the CSV
    journal) so a mid-day process restart doesn't silently reset trade_count/
    consecutive_losses back to zero -- weakening the daily guard right when a losing
    streak is the reason it matters most.
    """
    data = {
        "trading_day": stats.trading_day,
        "realized_pnl_cash": stats.realized_pnl_cash,
        "trade_count": stats.trade_count,
        "consecutive_losses": stats.consecutive_losses,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def load_daily_stats(path: str | Path, trading_day: str) -> DailyStats:
    """Load persisted daily-guard counters for `trading_day`, or a fresh DailyStats if
    there's nothing persisted yet or the persisted file is from a different (earlier)
    trading day -- the normal calendar-day reset must still take priority.
    """
    path = Path(path)
    if not path.exists():
        return DailyStats(trading_day=trading_day)
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return DailyStats(trading_day=trading_day)
    if data.get("trading_day") != trading_day:
        return DailyStats(trading_day=trading_day)
    return DailyStats(
        trading_day=trading_day,
        realized_pnl_cash=data.get("realized_pnl_cash", 0.0),
        trade_count=data.get("trade_count", 0),
        consecutive_losses=data.get("consecutive_losses", 0),
    )


@dataclass(frozen=True)
class TradeResult:
    """The outcome of position_manager.manage_position() for the current bar."""

    action: PositionAction
    new_stop_loss: float | None = None
    close_reason: CloseReason | None = None


@dataclass(frozen=True)
class GuardState:
    """Whether the daily guard currently allows opening new trades, and why not if it doesn't."""

    trading_allowed: bool
    blocked_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CooldownState:
    """Bars remaining before new entries are allowed again. Never blocks position management."""

    bars_remaining: int = 0

    @property
    def active(self) -> bool:
        return self.bars_remaining > 0


@dataclass(frozen=True)
class OpenTradeContext:
    """Snapshot of the decision context at the moment a position was opened.

    Kept around across iterations (position management doesn't recompute trend/signal)
    so the trade journal can record entry-time context when the position later closes.

    `breakeven_applied` tracks this independently of the `Position` object itself: a live
    broker (MT5/Binance with allow_live_trading=True) rebuilds a fresh `Position` from the
    broker's own data on every `get_open_position()` call, so a flag set directly on that
    object would not survive to the next iteration -- `LoopState.open_trade_context` is the
    one thing that reliably persists across iterations regardless of broker.
    """

    timeframe_entry: str
    stop_loss_initial: float
    reason_open: str
    m15_trend: str
    m5_confirmation: str
    m1_signal: str
    breakeven_applied: bool = False


@dataclass
class LoopState:
    """Mutable state threaded between bot loop iterations: running daily stats and cooldown.

    trade_count/consecutive_losses are tracked here (not by the broker) since the Broker
    interface only exposes realized cash P&L, not trade-level win/loss bookkeeping.
    """

    daily_stats: DailyStats
    cooldown: CooldownState = field(default_factory=CooldownState)
    open_trade_context: OpenTradeContext | None = None
    # Last position observed while managing it. Lets run_iteration() notice when a
    # position vanishes WITHOUT us having closed it (e.g. a real broker's own hard
    # stop-loss order firing server-side) so that close still gets journaled and
    # started a cooldown, instead of silently disappearing. Cleared whenever we
    # ourselves close the position.
    last_known_position: Position | None = None
