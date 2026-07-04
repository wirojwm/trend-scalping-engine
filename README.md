# trend_only_scalper

## Project purpose

A trend-only scalping bot framework for MT5 and Binance, built demo-first with
loop engineering: each phase added one small, fully-tested layer (indicators → strategy
logic → risk guards → broker adapters → replay backtest → unified CLI) on top of a single
broker-agnostic decision loop. The strategy logic never imports MT5, ccxt, or any broker
SDK directly — every backend (`MockBroker`, `MT5Broker`, `BinanceBroker`, and the backtest's
`SimulatedBroker`) implements the same `Broker` interface, so swapping backends never
touches strategy code.

> ## ⚠️ DEMO-FIRST — READ BEFORE RUNNING ANYTHING
> This project is built and tested for **demo accounts and paper/testnet trading only.**
> `dry_run` defaults to `true` everywhere, `strategy.yaml` refuses to load if any
> anti-pattern flag (grid, martingale, averaging down, counter-trend, multi-position) or
> unsafe risk value is set, and `config/mt5.yaml`/`config/binance.yaml` both default
> `allow_live_trading` to `false` — both adapters fetch **real** market data but only ever
> *simulate* order placement until that flag is explicitly set to `true`.
>
> ## ⚠️ LIVE TRADING WARNING
> Setting `allow_live_trading: true` sends **real orders** to whatever account your
> terminal/credentials point at. `MT5Broker.connect()` and `BinanceBroker` do not check
> whether that account is a demo account — **you must verify this yourself**, every time,
> before enabling it. Never enable it against an account you are not prepared to lose money
> on. See the [Safety checklist](#safety-checklist-before-enabling-any-real-demo-order)
> below before ever setting this flag.

## Strategy explanation

Three-timeframe, trend-only, one-position-at-a-time scalper:

- **M15 — trend filter.** `close > ema_slow`, `ema_fast > ema_slow`, `macd_hist > 0` → UP
  (mirrored for DOWN). Otherwise NONE — no trend, no trade.
- **M5 — confirmation.** Same structure as M15, plus `close` vs. `vwap` on the same side.
  Must agree with the M15 trend or no trade happens.
- **M1 — entry trigger.** Only evaluated once M15 and M5 agree: price pulls back to (or
  rebounds from) EMA20/VWAP within an ATR-scaled tolerance, the candle closes back in the
  trend direction, the candle range isn't abnormal versus ATR, and ATR is large enough
  relative to spread/cost to be worth trading.

Trades only when M15 and M5 agree — **never counter-trend**. Every position gets a hard
stop-loss immediately on entry, a cash-based take-profit, and a cash-based breakeven lock
once sufficiently in profit. Explicitly **not** implemented, by design: grid trading,
martingale, averaging down, hedge/opposite-side entries, or multiple concurrent positions.

### Safety rules (enforced in code, not just documentation)

- **One position only** — `should_scan_for_entry()` / every broker's `open_market_order()`
  refuses a second position; `StrategyConfig` refuses to load if `one_position_only: false`.
- **No counter-trend** — `detect_entry_signal()` requires M15==M5 and not NONE; config
  refuses to load if `allow_counter_trend: true`.
- **No grid, no martingale, no averaging down** — never implemented; config refuses to
  load if any of `allow_grid` / `allow_martingale` / `allow_averaging_down` is `true`.
- **Hard stop-loss required on every trade** — computed from swing structure + ATR buffer
  before any order is sent; config refuses to load if `swing_lookback < 1`.
- **Daily max loss** — `daily_max_loss` (must be negative) halts new entries for the day
  once hit; position management continues.
- **Max trades per day** — `max_trades_per_day` (must be positive) halts new entries once
  reached.
- **Max consecutive losses** — halts new entries once `max_consecutive_losses` is reached.
- **Cooldown after every close** — separate bar counts after TP / breakeven / hard-SL.

## Project layout

```
config/                  Non-secret YAML configuration (strategy, mt5, binance, backtest)
data/                    Sample OHLCV fixture for the replay backtest (sample_m1.csv)
src/trend_only_scalper/  Application code
  strategy/              Trend filter, confirmation filter, entry signal, position manager
  risk/                  Daily guard, risk manager, cooldown
  brokers/               Broker interface, MockBroker, mt5_broker.py, binance_broker.py
  backtest/              Replay simulator: data_loader, simulated_broker, replay
  journal.py             CSV trade journal writer/reader
  metrics.py             Performance metrics + safety report, computed from the journal
  main.py                run_iteration(): the full broker-agnostic bot loop
  cli.py                 Unified CLI -- dry-run / replay / mt5-demo / binance-demo / safety-report
tests/                   Unit tests (run without any broker connection)
scripts/                 Standalone runners (predate the CLI; still work, see below)
```

## Development approach: loop engineering

Built in phases, smallest-working-version first, each fully tested before the next began:

1. Project structure, config loader, models, README
2. Indicators (EMA, MACD, ATR, VWAP) + tests
3. Strategy logic (trend filter, confirmation filter, entry signal, risk manager)
4. Position manager, daily guard, cooldown
5. Broker interface + MockBroker + the full bot loop (`run_iteration`)
6. Trade journal, structured decision log, performance metrics, safety report
7. MT5 broker adapter
8. Binance broker adapter
9. Replay backtest: historical OHLCV through the same bot loop
10. **Unified CLI, config validation, final documentation** *(this phase)*

## Installation

Requires Python 3.10+. MetaTrader5 support requires Windows.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .        # so `trend_only_scalper` is importable everywhere
```

## Environment variables

Copy `.env.example` to `.env` and fill in only what you need for the backend you're using:

```powershell
copy .env.example .env
```

`.env` holds **secrets and run-mode flags only**:

| Variable | Purpose |
|---|---|
| `BROKER_BACKEND` | `mt5` \| `binance` \| `mock` — used by legacy `main.py`, not required by `cli.py` (each subcommand names its backend) |
| `DRY_RUN` | Global safety flag; `strategy.yaml`'s own `dry_run` is honored if either says true |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | MT5 credentials — variable *names* configurable via `mt5.yaml`'s `login_env`/`password_env`/`server_env` |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Binance credentials — variable *names* configurable via `binance.yaml`'s `api_key_env`/`api_secret_env` |
| `BINANCE_TESTNET` | Extra safety net on top of `binance.yaml`'s `testnet` (the safer of the two always wins) |

Everything else — symbol, indicator periods, risk limits, cost simulation — lives in
`config/*.yaml` and is safe to commit; no secrets ever belong there.

## Configuration

| File | Purpose |
|---|---|
| `config/strategy.yaml` | Trend-only strategy rules, risk limits, cooldowns, `dry_run` |
| `config/mt5.yaml` | MT5 symbol, magic, lot, filling mode, `allow_live_trading`, `*_env` var names |
| `config/binance.yaml` | Binance symbol, market type, testnet/`allow_live_trading`, leverage, quantity, fee estimate, `*_env` var names |
| `config/backtest.yaml` | Replay input CSV, spread/fee/slippage, execution mode, date range, journal output path |

`strategy.yaml` **fails to load** (raises `ValueError` at startup) if any of the following
hold — this is intentional; these are not safe defaults to silently allow:

- `one_position_only: false`, or `allow_grid` / `allow_martingale` / `allow_averaging_down`
  / `allow_counter_trend: true`
- `daily_max_loss >= 0` (must be a real negative loss limit)
- `max_trades_per_day <= 0`, `tp_cash <= 0`, or `breakeven_trigger_cash <= 0`
- `swing_lookback < 1` or `sl_atr_buffer < 0` (hard stop-loss must always be computable)

**Keep `strategy.yaml`'s `symbol` in sync with whichever broker config you're using** (e.g.
`"EURUSD"` for MT5, `"BTC/USDT"` for Binance) — `run_iteration()` always trades
`StrategyConfig.symbol`; each broker yaml's own `symbol` field is only used for that
adapter's internal bookkeeping/logging. A mismatch trades whatever `strategy.yaml` says
(silently, if it happens to be a symbol the broker also recognizes) or raises a
broker-specific "symbol not found" error (e.g. ccxt's `BadSymbol` for Binance) if it
doesn't — either way, cash values like `tp_cash`/`default_quantity` were tuned for one
instrument's price scale and won't automatically make sense for another (e.g. forex-scale
defaults against BTC/USDT's price will misfire the cost-vs-target safety check).

## CLI usage

`python -m trend_only_scalper.cli <command>` is the primary way to run this project.

### Dry-run usage

Runs the full bot loop against `MockBroker` with synthetic in-memory bars — **no
credentials, no network required**:

```powershell
python -m trend_only_scalper.cli dry-run --config config/strategy.yaml
```

Demonstrates the complete lifecycle: entry on an engineered M1 pullback, breakeven
stop-loss lock, cash take-profit close, cooldown, and safe idling once no signal is
present. Prints the safety report up front and a metrics summary at the end.

### Replay backtest usage

Replays a historical M1 OHLCV CSV through the same `run_iteration()` bot loop via a
`SimulatedBroker`, resampling M1 into M5/M15 itself (no lookahead by construction — see
`backtest/simulated_broker.py`'s docstring for the exact assumptions):

```powershell
python -m trend_only_scalper.cli replay --strategy config/strategy.yaml --backtest config/backtest.yaml
```

Expected CSV format (`config/backtest.yaml`'s `input_csv`) — columns `time, open, high,
low, close, volume`, one row per M1 bar:

```
time,open,high,low,close,volume
2026-01-01T00:00:00Z,1.1000,1.1002,1.0999,1.1001,100.0
2026-01-01T00:01:00Z,1.1001,1.1003,1.1000,1.1002,100.0
```

New entries fill at the **next** M1 bar's open (`execution_mode: next_open`), with
configurable spread + slippage applied to every fill. This is **not a professional
backtesting engine** — see [Known limitations](#known-limitations).

### MT5 demo usage

Requires a running, logged-in MT5 terminal (or `MT5_LOGIN`/`MT5_PASSWORD`/`MT5_SERVER` in
`.env`, using the variable names configured in `mt5.yaml`):

```powershell
python -m trend_only_scalper.cli mt5-demo --strategy config/strategy.yaml --broker config/mt5.yaml
```

Dry-run by default (`config/mt5.yaml`'s `allow_live_trading: false`): fetches real bars,
prices, and account info from MT5, but every order is simulated locally — nothing is ever
sent to the account. **Real demo orders require explicitly setting `allow_live_trading:
true`** in `mt5.yaml` — the CLI then prints an explicit warning and requires pressing
Enter before continuing. Verify in the MT5 terminal itself that the logged-in account is a
demo account before ever enabling it.

### Binance testnet usage

Public market data (OHLCV, tickers) works without API keys; balance/position/order calls
need `BINANCE_API_KEY`/`BINANCE_API_SECRET` in `.env`:

```powershell
python -m trend_only_scalper.cli binance-demo --strategy config/strategy.yaml --broker config/binance.yaml
```

Dry-run and testnet by default (`config/binance.yaml`'s `allow_live_trading: false`,
`testnet: true`): fetches real OHLCV/tickers from Binance testnet, but simulates order
placement locally. **Real testnet orders require explicitly setting `allow_live_trading:
true`** — the CLI warns explicitly and requires pressing Enter, with an extra warning if
`testnet` is also `false` (real funds). Keep `testnet: true` even after enabling live
orders unless you specifically intend to trade mainnet.

### safety-report

Prints the current config's safety settings without running anything:

```powershell
python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml
```

### Legacy scripts

`scripts/run_dry_run.py`, `run_mt5_demo.py`, `run_binance_demo.py`, and
`run_replay_backtest.py` predate the unified CLI and still work identically (directory-
based `--config-dir` flags instead of per-file `--strategy`/`--broker`/`--backtest`); the
CLI is the recommended entry point going forward. `python -m trend_only_scalper.main
--once` is a minimal legacy heartbeat scaffold kept only for backward compatibility.

Logs are written to console and to `logs/trend_only_scalper.log` (rotating, 5MB x 5 backups).

## Journal and metrics output

- **Trade journal** (`journal.py`) — one CSV row per closed trade, appended (never
  overwritten) to `logs/trade_journal.csv` by default (`--journal-path` on `dry-run`/
  `mt5-demo`/`binance-demo`, `output_journal_csv` in `backtest.yaml`). Records entry/exit
  price, initial vs. final stop-loss, realized P&L, the M15/M5/M1 context that justified
  the entry, and running daily-guard counters at the moment of close. Hard-SL closes
  during a replay are journaled too — `SimulatedBroker` decides them autonomously (like a
  real broker's stop order would), and `backtest/replay.py` records the bookkeeping since
  `manage_position()` never decides that case.
- **Decision log** — one structured `logging` line per loop iteration (logger
  `trend_only_scalper.decision`), covering open-position status, daily guard/cooldown/cost
  status, M15/M5/M1 readings, and the action taken — whether or not a trade happened.
- **Metrics** (`metrics.py`) — `calculate_metrics()` reads journal rows back and computes
  win rate, profit factor (safely defined when there are no losses yet), max consecutive
  losses, gross/net P&L, and trades-per-day. `print_safety_report()` prints the anti-pattern
  guard flags, risk limits, and active broker backend — every CLI subcommand prints this.

## Testing

```powershell
pytest
```

All tests run without MT5 or Binance installed/connected — strategy logic is fully
broker-agnostic and testable in isolation. `tests/test_cli.py` additionally checks that
every README CLI example matches an implemented subcommand and that dangerous config
combinations are rejected.

## Known limitations

- **Not a professional backtesting engine.** Bar resolution only (no tick data); TP/
  breakeven checks use the bar's close; a hard-SL hit is checked (and takes priority)
  before TP/breakeven each bar, since real intrabar tick order can't be reconstructed
  from OHLC bars alone.
- **Quantity/price-scale mismatch is on you to tune.** Defaults (`default_quantity: 1.0`,
  `tp_cash: 1.50`) are forex-pair-scale. Against BTC/USDT or any very different price
  scale, the cost-vs-target safety check may misfire, or a position may never realistically
  reach TP. Verified during Phase 8/9 testing — tune `default_quantity`/`tp_cash` to match
  your instrument before drawing conclusions from any run.
- **`strategy.yaml`'s `symbol` and each broker yaml's `symbol` are independent fields** —
  keeping them in sync is a manual step, not enforced.
- **MT5Broker.connect() does not verify demo-vs-live.** It uses whatever account the
  terminal or supplied credentials point at. Verify this yourself, every time.
- **Binance has no per-order "magic number" like MT5.** A position is reported per symbol,
  not per originating order — dedicate the configured symbol to this bot alone.
- **No portfolio/multi-symbol support.** One symbol, one position, one strategy instance
  per run.

## Safety checklist (before enabling any real demo order)

- [ ] Confirm `dry_run` is `false` only intentionally — not left over from copy/pasting a
      config.
- [ ] Confirm `allow_live_trading` is `true` only on a **demo (MT5) or testnet (Binance)**
      account first — verified in the terminal/exchange itself, not assumed.
- [ ] Confirm `symbol` in `strategy.yaml` matches the broker config and is the instrument
      you actually intend to trade.
- [ ] Confirm lot/quantity (`mt5.yaml`'s `lot`, `binance.yaml`'s `quantity`,
      `strategy.yaml`'s `default_quantity`) is minimal for a first run.
- [ ] Confirm `daily_max_loss` is small relative to account size.
- [ ] Confirm `max_trades_per_day` is reasonable, not left at a stress-test value.
- [ ] Confirm the spread/cost filter is active (`min_atr_spread_multiple` > 0, and for
      MT5 `max_spread_points` is set sensibly).
- [ ] Confirm one-position-only is active (`strategy.yaml` will refuse to load otherwise).
- [ ] Confirm no counter-trend is active (`strategy.yaml` will refuse to load otherwise).
- [ ] Confirm no martingale/grid/averaging down (`strategy.yaml` will refuse to load
      otherwise).
- [ ] Confirm the journal is writing — check `logs/trade_journal*.csv` after a short run.
- [ ] Confirm a stop-loss is sent with every order — every broker adapter attaches one
      immediately on entry; verify it in the terminal/exchange UI for your first real order.

Run `python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml`
before any demo run to confirm these flags at a glance.
