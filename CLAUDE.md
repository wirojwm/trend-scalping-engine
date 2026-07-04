# CLAUDE.md

Guidance for Claude Code (and any other AI agent) working in this repository.

## Project overview

`trend_only_scalper` — a trend-only M1 scalping trading bot framework. It runs the same
broker-agnostic decision loop (M15 trend filter -> M5 confirmation -> M1 entry -> stop-loss
-> order) against multiple backends: a MockBroker (dry-run/tests), MT5, Binance (via ccxt),
and a historical replay/backtest simulator.

Core safety rules that must always hold, in code and in any change: one position only, no
counter-trend, no grid, no martingale, no averaging down, hard stop-loss on every order,
cash-based take-profit, breakeven lock, a daily risk guard, and a post-close cooldown.

## Main language / stack

- Python >= 3.10
- Key libraries: `pydantic` (typed, validated config), `pandas`/`numpy` (OHLCV + indicators),
  `PyYAML` + `python-dotenv` (config/secrets), `MetaTrader5` (Windows-only, MT5 backend),
  `ccxt` (Binance backend), `pytest` (tests)
- Packaging: standard `setuptools`, package source lives under `src/`

## Build / install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

## Folder structure

```
config/                    strategy.yaml, mt5.yaml, binance.yaml, backtest.yaml (non-secret settings)
data/                       sample_m1.csv fixture used by the replay backtest
docs/                       MT5 demo / Binance testnet manual testing plans
scripts/                    thin runnable wrappers (run_dry_run.py, run_mt5_demo.py, etc.)
src/trend_only_scalper/
  config.py                 Pydantic config loading + validation (StrategyConfig/MT5Config/BinanceConfig)
  models.py                 broker-agnostic dataclasses/enums (Position, Signal, LoopState, ...)
  main.py                   run_iteration() -- the core broker-agnostic decision loop
  cli.py                    the actual entry point: dry-run / replay / mt5-demo / binance-demo / safety-report
  indicators.py              EMA, MACD, ATR, session VWAP (pure DataFrame functions)
  journal.py / metrics.py    CSV trade journal + performance metrics / safety report
  logging_config.py          structured decision logging + rotating file logs
  strategy/                  trend_filter (M15), confirmation_filter (M5), entry_signal (M1), position_manager
  risk/                      risk_manager (stop-loss calc), daily_guard, cooldown
  brokers/                   base.py (Broker ABC), mock_broker, mt5_broker, binance_broker
  backtest/                  data_loader, simulated_broker, replay (historical replay driver)
tests/                       pytest suite, one file per module/concern (183 tests)
```

Architecture rule: only `brokers/mt5_broker.py` imports `MetaTrader5` and only
`brokers/binance_broker.py` imports `ccxt`. Everything else (strategy, risk, `main.py`)
depends solely on the `Broker` ABC in `brokers/base.py` and must stay broker-agnostic.

## How to run tests

```powershell
python -m pytest -q
```

Run a single file/test: `python -m pytest tests/test_position_manager.py -q`

## Other useful commands

```powershell
python -m trend_only_scalper.cli --help
python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml
python -m trend_only_scalper.cli dry-run --config config/strategy.yaml --iterations 20
python -m trend_only_scalper.cli replay --backtest config/backtest.yaml --strategy config/strategy.yaml
python -m compileall src tests scripts   # quick syntax/import sanity check
```

No `ruff`/`mypy` are configured in this project currently — don't assume they're available.

---

## Shared rules (from My-LLM-Wiki/AGENTS.md)
- Write summaries in Thai
- Never commit secrets or API keys
- Work on one small task at a time — write a plan first and wait for approval before making changes
- After finishing each task, append a summary to decision-log.md
  (create it if it doesn't exist) — this file will later be copied into My-LLM-Wiki
