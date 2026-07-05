# Senior Review Action Plan — trend_only_scalper

Consolidated, prioritized action plan derived from the senior code review (architecture,
Python quality, quant/trading-system correctness) of the committed project. This is a
living document: update it as items are fixed or re-prioritized, don't recreate it.

---

## 1. Executive summary

The project is architecturally sound: broker-agnostic strategy code, a single `Broker` ABC
implemented independently by `MockBroker`/`MT5Broker`/`BinanceBroker`/`SimulatedBroker`, and
all core safety rules (one position only, no counter-trend, no grid, no martingale, no
averaging down, hard SL, cash TP, breakeven lock, daily guard, cooldown) are enforced in
broker-agnostic code paths and covered by tests. The review found no defects in the
strategy/trend/risk decision logic itself.

The issues found are concentrated in **broker-specific unit conversions** (MT5 lot sizing
vs. the cash-based risk model) and a handful of **operational visibility / config-hygiene
gaps**. None require touching trend/confirmation/entry logic or the safety-rule
enforcement points. Fixes are being applied in small, independently-tested batches — see
[Recommended fix phases](#8-recommended-fix-phases).

---

## 2. Repository status at review time

- Branch: `master`. **Phase R1 and Phase R2 (all P0 and P1 items) are complete and pushed to
  `origin/master`:** `a3d16ec` → `c679c9c` → `6e305b1` (R1, items 1-4) → `2a06f69` → `776ab46`
  → `f43fc8a` → `cee035d` (R2, items 5-8).
- Test suite size: 183 tests before Phase R1, **207 passing** after R1+R2.
- No secrets, `.env` files, or credentials present in the repository or in review artifacts.

---

## 3. P0 critical issues (before demo testing)

| # | Issue | Why it matters | Risk if not fixed | Files | Status |
|---|---|---|---|---|---|
| 1 | Breakeven lock / journal exit-price wrong for MT5 lot sizing | `_cash_to_price_distance()`/`_price_from_pnl()` assumed `contract_size=1`, wrong for MT5 lots | Breakeven stop-loss computed at the wrong price on MT5 — a direct safety-rule violation | `strategy/position_manager.py`, `main.py`, `brokers/base.py`, `brokers/mt5_broker.py` | **Fixed** (`a3d16ec`) |
| 2 | MT5 simulated `get_unrealized_pnl` ignores contract size | Dry-run/demo mode (`allow_live_trading=False`) computed pnl as `move * quantity` only | Demo testing (the tool meant to catch exactly this class of bug) would show wrong pnl/TP triggers | `brokers/mt5_broker.py` | **Fixed** (`a3d16ec`) |
| 3 | VWAP truncation in M5 confirmation filter | `BAR_LOOKBACK=100` slices bars *before* `add_vwap()` runs, breaking the cumulative-per-session calculation (~2.4% deviation observed) | M5 confirmation filter feeds a materially wrong reference price into real entry decisions | `main.py` (`_add_confirmation_indicators`, M5 bar fetch) | **Fixed** (`c679c9c`) |
| 4 | Trading-cost cash/price-unit mismatch blocks all trading at BTC scale | `get_trading_cost()` is (correctly) a price-unit spread/fee estimate everywhere, but `main.py`'s entry gate compared it directly against the cash `tp_cash` target — root cause was in `main.py`, not the Binance broker itself | At BTC/USDT scale the price-unit number is large enough to permanently exceed `tp_cash`, silently preventing any trade on Binance demo | `main.py` (entry-gate cash conversion; `brokers/binance_broker.py` unchanged) | **Fixed** (`6e305b1`) |

---

## 4. P1 important issues (before longer demo testing)

| # | Issue | Why it matters | Risk if not fixed | Files | Status |
|---|---|---|---|---|---|
| 5 | `MT5Config`/`BinanceConfig` have no `model_validator` | `StrategyConfig` has `_forbid_dangerous_config`; broker configs have no equivalent | Dangerous broker-level config combinations aren't caught at load time | `config.py` | **Fixed** (`2a06f69`) |
| 6 | `safety-report` doesn't show `allow_live_trading` | Report only shows strategy-level flags + backend name string | Operator can misjudge whether live trading is actually enabled for a broker | `metrics.py`, `cli.py` | **Fixed** (`776ab46`) |
| 7 | Dead config fields (`lot`, `quantity`) can mislead sizing expectations | `mt5.yaml`'s `lot` and `binance.yaml`'s `quantity` are never read; `strategy.yaml`'s `default_quantity` is the real sizing knob for both | Operator edits a field expecting it to change order size — it silently has no effect | `config.py` | **Fixed** (`f43fc8a`) |
| 8 | `DailyStats` is in-memory only | `LoopState`/`DailyStats` have no persistence across process restarts | A mid-day restart after losses silently resets `trade_count`/`consecutive_losses`, weakening the daily guard | `models.py`, `cli.py` | **Fixed** (`cee035d`) |

---

## 5. P2 improvements

| # | Issue | Why it matters | Files |
|---|---|---|---|
| 9 | `CloseReason.BREAKEVEN_SL` never assigned by any real code path | Autonomous-close detection always tags `HARD_SL`, even when the stop had already been moved to breakeven | `main.py`, `models.py` |
| 10 | `.env` `BINANCE_TESTNET` OR-merge logic undocumented | Merge biases toward `testnet=True` unless both YAML and env disable it — safe, but confusing without a comment | `config.py`, `binance.yaml` |

---

## 6. P3 nice-to-have items

| # | Issue | Files |
|---|---|---|
| 11 | Test file names don't always match the module under test (e.g. `calculate_stop_loss` tested in `test_entry_signal.py`) | `tests/*.py` (rename only, coverage already exists) |

---

## 7. Deferred items (not necessary right now)

- `BinanceConfig.max_cost_ratio_to_tp` / `BinanceBroker.estimate_fee_cash()` /
  `is_cost_too_high_for_target()` are dead code (never called from `main.py`/`cli.py`).
  Harmless since unexecuted — defer until there's a concrete plan to wire them in, or a
  dedicated dead-code cleanup pass.
- Any finding referenced only informally in prior review conversation but not reproducible
  against current code — mark **needs clarification** and re-derive from the code before
  acting on it, rather than trusting a stale summary.

---

## 8. Recommended fix phases

```
Phase R1 — P0 safety blockers only [COMPLETE]
  1) Contract-size-aware breakeven + journal exit-price       [done - a3d16ec]
  2) MT5 simulated get_unrealized_pnl contract-size fix        [done - a3d16ec]
  3) VWAP truncation fix                                       [done - c679c9c]
  4) Trading-cost cash conversion fix (main.py, not binance)   [done - 6e305b1]

Phase R2 — P1 broker/config adapter issues [COMPLETE]
  5) MT5Config/BinanceConfig model_validator                    [done - 2a06f69]
  6) safety-report allow_live_trading visibility                [done - 776ab46]
  7) Dead lot/quantity field warning                             [done - f43fc8a]
  8) DailyStats persistence across restarts                      [done - cee035d]

Phase R3 — P1/P2 test coverage and documentation issues [next]
  9) Distinguish BREAKEVEN_SL from HARD_SL in autonomous close detection
  10) Document testnet env/yaml merge behavior
  11) (optional, low priority) rename test files to match module under test
```

One small batch at a time; each batch gets its own plan, approval, tests, and commit —
never a combined rewrite across phases.

---

## 9. Test plan per fix phase

**R1 (all done, 191 tests passing):**
- `test_position_manager.py`: contract_size≠1 case (MT5-style lot) + default contract_size=1.0
  regression case (items 1–2).
- `test_mt5_broker_contract.py`: `contract_size()` reads `trade_contract_size`; simulated
  `get_unrealized_pnl` scales correctly (items 1–2).
- `test_bot_loop.py`: M5 bars fetched with a `VWAP_BAR_LOOKBACK` (288) floor, and VWAP on a
  150-bar same-day session matches the untruncated calculation (item 3).
- `test_bot_loop.py`: entry gate blocks when the cash-equivalent cost meets/exceeds `tp_cash`,
  and allows a trade when the price-unit cost is large (BTC scale) but the cash-equivalent
  cost is small (item 4).

**R2 (all done, 207 tests passing):**
- `test_config.py`: invalid MT5/Binance config combinations raise at load time (item 5).
- `test_metrics.py`/`test_cli.py`: safety report includes `allow_live_trading` for both
  `true`/`false`, and omits it for mock/backtest backends (item 6).
- `test_config.py`: warning logged when a dead `lot`/`quantity` field is set to a non-default
  value, silent when left at default (item 7).
- `test_models.py`: `save_daily_stats`/`load_daily_stats` round-trip on the same day, reset on
  a new day, handle a missing/corrupt file gracefully. `test_cli.py`: a second
  `_run_continuous_loop` call (simulating a restart) picks up `trade_count` where the first
  left off (item 8).

**R3:**
- Autonomous-close test: after a `MODIFY_SL` (breakeven) then a broker-side close, the
  recorded reason must be `CloseReason.BREAKEVEN_SL`, not `HARD_SL`.
- No new tests required for the testnet-merge documentation change (behavior unchanged).

Every phase re-runs the full suite (`pytest -q`) and must show **zero regressions** before
its commit.

---

## 10. Commit plan per fix phase

| Phase | Commits |
|---|---|
| R1 | `fix review position management and mt5 broker safety` (`a3d16ec`, items 1+2) → `fix review vwap truncation handling` (`c679c9c`, item 3) → `fix review binance trading cost cash conversion` (`6e305b1`, item 4) — **all done** |
| R2 | `fix review config validation: mt5/binance model validators` (`2a06f69`) → `fix review broker adapter guards: safety-report shows allow_live_trading` (`776ab46`) → `fix review config validation: warn on unused lot/quantity fields` (`f43fc8a`) → `fix review safety blockers: persist daily guard stats across restarts` (`cee035d`) — **all done** |
| R3 | `fix review tests for risk guards: distinguish breakeven-sl from hard-sl` → `docs update review action plan: document testnet merge behavior` → (optional) `fix review tests for risk guards: rename test files to match module` |

Each commit: run full verification first (compileall, pytest, CLI smoke tests), then commit
only the files in scope for that item — never a combined "batch everything" commit.

---

## 11. Demo-readiness checklist

Before starting **any** demo session (MT5 or Binance), confirm:

- [x] All Phase R1 (P0) items fixed, tested, and committed
- [ ] `pytest -q` passes with zero failures on the commit being demoed
- [ ] `safety-report` reviewed and `allow_live_trading` confirmed `false` unless the demo
      explicitly intends to place real orders on a demo/testnet account
- [ ] Broker config's account/server fields double-checked as demo/testnet, not live
- [ ] Relevant testing plan followed end-to-end: `docs/mt5_demo_testing_plan.md` or
      `docs/binance_futures_testnet_testing_plan.md`
- [ ] Known-open items (Phase R3) reviewed so any related odd behavior during demo is
      recognized as already-known, not a new surprise

Longer/unattended demo runs additionally require:
- [x] Phase R2 item 8 (`DailyStats` persistence) fixed — `--state-path` on `mt5-demo`/
      `binance-demo` now survives a mid-session restart

---

## 12. Next immediate action

Phase R1 and Phase R2 (all P0 and P1 items) are complete. Next: start **Phase R3 item 9** —
distinguish `CloseReason.BREAKEVEN_SL` from `HARD_SL` in autonomous close detection — as its
own small batch: plan → approval → implement → test → commit. Continue down the phase list
in order (item 9 → 10 → optional 11); re-prioritize only if a new finding surfaces during
implementation, and record any re-prioritization here.
