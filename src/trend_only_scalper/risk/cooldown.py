"""Bar-counter cooldown after TP/breakeven/SL closes.

Cooldown is measured in M1 bars, never wall-clock time, and only ever gates new entries --
it is never consulted by position_manager while a position is open.
"""

from __future__ import annotations

from trend_only_scalper.models import CloseReason, CooldownState


def cooldown_bars_for_reason(
    reason: CloseReason,
    cooldown_after_tp_bars: int,
    cooldown_after_be_bars: int,
    cooldown_after_sl_bars: int,
) -> int:
    """How many bars of cooldown a close of the given reason should start, per config."""
    bars_by_reason = {
        CloseReason.TP_CASH: cooldown_after_tp_bars,
        CloseReason.BREAKEVEN_SL: cooldown_after_be_bars,
        CloseReason.HARD_SL: cooldown_after_sl_bars,
    }
    return bars_by_reason.get(reason, 0)


def start_cooldown(bars: int) -> CooldownState:
    """Begin a cooldown of `bars` M1 bars (clamped to non-negative)."""
    return CooldownState(bars_remaining=max(bars, 0))


def tick(state: CooldownState) -> CooldownState:
    """Advance one M1 bar; call this once per bar regardless of position state."""
    return CooldownState(bars_remaining=max(state.bars_remaining - 1, 0))


def is_active(state: CooldownState) -> bool:
    """True if new entries are currently blocked by cooldown."""
    return state.active
