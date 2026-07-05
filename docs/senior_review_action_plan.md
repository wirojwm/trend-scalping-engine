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

- Branch: `master`. Last fully committed history: `docs add mt5 and binance demo testing
  plans` → `Add CLAUDE.md, decision-log.md` → `docs update progress-note with P0-new
  priority and next-session plan`.
- **In progress (uncommitted at time of writing):** Phase R1 batch, item 1+2 — contract-size
  -aware breakeven lock + journal exit-price, and the matching MT5 simulated
  `get_unrealized_pnl` fix. 6 files changed (`brokers/base.py`, `brokers/mt5_broker.py`,
  `main.py`, `strategy/position_manager.py`, `tests/test_mt5_broker_contract.py`,
  `tests/test_position_manager.py`), verified via `pytest -q` (187 passed) and CLI smoke
  tests, awaiting explicit commit/push approval.
- Test suite size: 183 tests at last commit, 187 with the in-progress batch.
- No secrets, `.env` files, or credentials present in the repository or in review artifacts.

---

## 3. P0 critical issues (before demo testing)

| # | Issue | Why it matters | Risk if not fixed | Files | Status |
|---|---|---|---|---|---|
| 1 | Breakeven lock / journal exit-price wrong for MT5 lot sizing | `_cash_to_price_distance()`/`_price_from_pnl()` assumed `contract_size=1`, wrong for MT5 lots | Breakeven stop-loss computed at the wrong price on MT5 — a direct safety-rule violation | `strategy/position_manager.py`, `main.py`, `brokers/base.py`, `brokers/mt5_broker.py` | **Fix in progress** (uncommitted) |
| 2 | MT5 simulated `get_unrealized_pnl` ignores contract size | Dry-run/demo mode (`allow_live_trading=False`) computed pnl as `move * quantity` only | Demo testing (the tool meant to catch exactly this class of bug) would show wrong pnl/TP triggers | `brokers/mt5_broker.py` | **Fix in progress** (uncommitted) |
| 3 | VWAP truncation in M5 confirmation filter | `BAR_LOOKBACK=100` slices bars *before* `add_vwap()` runs, breaking the cumulative-per-session calculation (~2.4% deviation observed) | M5 confirmation filter feeds a materially wrong reference price into real entry decisions | `main.py` (`_add_confirmation_indicators`) | Not started |
| 4 | Binance `get_trading_cost()` unit mismatch blocks all trading | Returns a price-unit cost (`2 * fee_rate * price`), compared directly against flat cash `tp_cash` | At BTC/USDT scale this permanently exceeds `tp_cash`, silently preventing any trade on Binance demo | `brokers/binance_broker.py` | Not started |

---

## 4. P1 important issues (before longer demo testing)

| # | Issue | Why it matters | Risk if not fixed | Files |
|---|---|---|---|---|
| 5 | `MT5Config`/`BinanceConfig` have no `model_validator` | `StrategyConfig` has `_forbid_dangerous_config`; broker configs have no equivalent | Dangerous broker-level config combinations aren't caught at load time | `config.py` |
| 6 | `safety-report` doesn't show `allow_live_trading` | Report only shows strategy-level flags + backend name string | Operator can misjudge whether live trading is actually enabled for a broker | `metrics.py`, `cli.py` |
| 7 | Dead config fields (`lot`, `quantity`) can mislead sizing expectations | `mt5.yaml`'s `lot` and `binance.yaml`'s `quantity` are never read; `strategy.yaml`'s `default_quantity` is the real sizing knob for both | Operator edits a field expecting it to change order size — it silently has no effect | `config.py` |
| 8 | `DailyStats` is in-memory only | `LoopState`/`DailyStats` have no persistence across process restarts | A mid-day restart after losses silently resets `trade_count`/`consecutive_losses`, weakening the daily guard | `models.py`, `cli.py` |

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
Phase R1 — P0 safety blockers only
  1) Contract-size-aware breakeven + journal exit-price       [in progress]
  2) MT5 simulated get_unrealized_pnl contract-size fix        [in progress]
  3) VWAP truncation fix                                       [not started]
  4) Binance trading-cost unit-mismatch fix                    [not started]

Phase R2 — P1 broker/config adapter issues
  5) MT5Config/BinanceConfig model_validator
  6) safety-report allow_live_trading visibility
  7) Dead lot/quantity field warning
  8) DailyStats persistence across restarts

Phase R3 — P1/P2 test coverage and documentation issues
  9) Distinguish BREAKEVEN_SL from HARD_SL in autonomous close detection
  10) Document testnet env/yaml merge behavior
  11) (optional, low priority) rename test files to match module under test
```

One small batch at a time; each batch gets its own plan, approval, tests, and commit —
never a combined rewrite across phases.

---

## 9. Test plan per fix phase

**R1:**
- `test_position_manager.py`: contract_size≠1 case (MT5-style lot) + default contract_size=1.0
  regression case (done for items 1–2).
- `test_mt5_broker_contract.py`: `contract_size()` reads `trade_contract_size`; simulated
  `get_unrealized_pnl` scales correctly (done for items 1–2).
- VWAP fix: synthetic multi-day OHLCV test asserting VWAP matches full-session calculation,
  not a truncated window.
- Binance cost fix: `test_binance_broker_contract.py` case asserting cost is a cash amount
  scaled by quantity, not a bare price-unit difference.

**R2:**
- `test_config.py`: invalid MT5/Binance config combinations raise at load time.
- `test_metrics.py`/`test_cli.py`: safety report includes `allow_live_trading` for both
  `true`/`false`.
- `test_config.py`: warning logged when a dead `lot`/`quantity` field is set to a non-default
  value.
- Daily guard persistence: restart-then-resume test restores `trade_count`/
  `consecutive_losses` when `trading_day` matches, resets when the day has changed.

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
| R1 | `fix review safety blockers: contract-size-aware breakeven lock and mt5 simulated pnl` (items 1+2, batched — done, pending approval) → `fix review safety blockers: vwap truncation in m5 confirmation` (item 3) → `fix review safety blockers: binance trading-cost unit mismatch` (item 4) |
| R2 | `fix review config validation: mt5/binance model validators` → `fix review broker adapter guards: safety-report shows allow_live_trading` → `fix review config validation: warn on unused lot/quantity fields` → `fix review safety blockers: persist daily guard stats across restarts` |
| R3 | `fix review tests for risk guards: distinguish breakeven-sl from hard-sl` → `docs update review action plan: document testnet merge behavior` → (optional) `fix review tests for risk guards: rename test files to match module` |

Each commit: run full verification first (compileall, pytest, CLI smoke tests), then commit
only the files in scope for that item — never a combined "batch everything" commit.

---

## 11. Demo-readiness checklist

Before starting **any** demo session (MT5 or Binance), confirm:

- [ ] All Phase R1 (P0) items fixed, tested, and committed
- [ ] `pytest -q` passes with zero failures on the commit being demoed
- [ ] `safety-report` reviewed and `allow_live_trading` confirmed `false` unless the demo
      explicitly intends to place real orders on a demo/testnet account
- [ ] Broker config's account/server fields double-checked as demo/testnet, not live
- [ ] Relevant testing plan followed end-to-end: `docs/mt5_demo_testing_plan.md` or
      `docs/binance_futures_testnet_testing_plan.md`
- [ ] Known-open items (Phase R2/R3) reviewed so any related odd behavior during demo is
      recognized as already-known, not a new surprise

Longer/unattended demo runs additionally require:
- [ ] Phase R2 item 8 (`DailyStats` persistence) fixed, or an explicit decision to keep the
      session short enough that a mid-session restart is not a realistic risk

---

## 12. Next immediate action

1. Get explicit approval to commit + push the in-progress Phase R1 item 1+2 batch
   (contract-size fix), currently verified but uncommitted.
2. Start Phase R1 item 3 (VWAP truncation) as its own small batch: plan → approval →
   implement → test → commit.
3. Continue down the phase list in order (R1 → R2 → R3); re-prioritize only if a new
   finding surfaces during implementation, and record any re-prioritization here.
