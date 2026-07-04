# Binance Futures Testnet Testing Plan — trend_only_scalper

Practical, step-by-step validation plan for running this bot against Binance Futures
**testnet** via ccxt. Every command, field name, and behavior below is taken directly from
the current codebase (`config/binance.yaml`, `config/strategy.yaml`, `cli.py`,
`brokers/binance_broker.py`) — not generic advice. Several numeric examples were verified
by direct inspection/execution against this project's actual code, not assumed.

> **This plan assumes `config/binance.yaml`'s `allow_live_trading` starts at `false`.**
> Sections 1–8 are dry-run only (no real order can be sent). Do not proceed past section 8
> until every dry-run check passes and testnet is confirmed independently (see §2).

---

## 1. Environment checklist

- [ ] `python -c "import ccxt; print(ccxt.__version__)"` succeeds (`ccxt>=4.3` per `requirements.txt`).
- [ ] Virtual environment activated, dependencies installed:
      `pip install -r requirements.txt && pip install -e .`.
- [ ] `python -m trend_only_scalper.cli --help` runs and lists all 5 subcommands.
- [ ] System clock correct and NTP-synced — `get_today_realized_pnl()` and the daily-stats
      rollover both key off `datetime.now(timezone.utc)`; Binance also rejects requests
      with excessive clock skew (`recvWindow`, default 5000 ms in `binance.yaml`).
- [ ] Stable internet connection to `testnet.binancefuture.com` (test with a browser first).
- [ ] Confirm no other bot/script is trading the same symbol on the same testnet account —
      unlike MT5, Binance has **no magic-number equivalent** (see §14); the whole symbol
      must be dedicated to this bot.

## 2. Binance testnet checklist

- [ ] **Binance Futures testnet is a separate system from real Binance** — sign up
      independently at the Futures testnet portal (`testnet.binancefuture.com`), not your
      regular Binance account. Confirm you're looking at the testnet UI, not binance.com.
- [ ] Testnet account funded with test USDT (the testnet UI provides a faucet/reset button
      for test balance — use it to reset to a known starting balance before a test session).
- [ ] Confirm `config/binance.yaml`'s `testnet: true` — this makes `_build_exchange()` call
      `exchange.set_sandbox_mode(True)`, which redirects ccxt to the testnet endpoints.
      **Verify this actually happened**, don't just trust the config:
  ```powershell
  python -c "
  from trend_only_scalper.config import load_binance_config
  from trend_only_scalper.brokers.binance_broker import BinanceBroker
  cfg = load_binance_config('config/binance.yaml')
  b = BinanceBroker(cfg)
  print('testnet flag:', cfg.testnet)
  print('exchange urls:', b._exchange.urls.get('api'))
  "
  ```
  The printed API URLs should contain `testnet` in the hostname.
- [ ] Confirm `config/binance.yaml`'s `market_type: "futures"` (not `"spot"`) — short
      selling and this project's position model both require futures.

## 3. API key checklist

- [ ] Generate an API key **from the testnet portal itself** (testnet keys are separate
      from mainnet keys and won't work against mainnet or vice versa).
- [ ] Enable **Futures trading permission** on the testnet API key (read-only keys will
      fail every order placement call).
- [ ] Do **not** enable withdrawal permission (testnet has no real funds, but keep the
      habit — mainnet keys should never have withdrawal permission for a bot).
- [ ] Put the key/secret in `.env`, **never** in `config/binance.yaml`:
  ```
  BINANCE_API_KEY=<testnet_key>
  BINANCE_API_SECRET=<testnet_secret>
  ```
  (Variable names come from `binance.yaml`'s `api_key_env`/`api_secret_env`, default
  `BINANCE_API_KEY`/`BINANCE_API_SECRET`.)
- [ ] Confirm `.env` is not tracked by git (`git check-ignore -v .env` should print a match).
- [ ] Confirm the key loads correctly (without printing the secret itself):
  ```powershell
  python -c "
  from trend_only_scalper.config import load_binance_config
  cfg = load_binance_config('config/binance.yaml')
  print('api_key set:', bool(cfg.api_key))
  print('api_secret set:', bool(cfg.api_secret))
  "
  ```

## 4. Futures account checklist

- [ ] Confirm the testnet account is **USDT-margined futures** (the standard default) —
      `get_account_equity()` specifically reads `balance["USDT"]["total"]`; a
      COIN-margined or different-collateral account would silently report equity as `0.0`.
- [ ] Confirm sufficient testnet USDT balance for the planned quantity + leverage.
- [ ] Confirm no pre-existing open position on the symbol you intend to test — a leftover
      manual/previous-test position will be picked up by `get_open_position()` as "ours"
      immediately (see §14), confusing every subsequent check.
- [ ] Confirm testnet account permissions allow futures order placement (check for any
      "restricted" banners in the testnet UI).

## 5. Symbol checklist

- [ ] **`config/binance.yaml`'s `symbol` uses ccxt's unified format with a slash**, e.g.
      `"BTC/USDT"` — not Binance's raw `"BTCUSDT"`. Confirm the symbol you choose exists
      on testnet (not every mainnet pair is listed on testnet).
- [ ] **`config/strategy.yaml`'s `symbol` must be set to the exact same string** — these
      are independent fields (see §7); `run_iteration()` always trades
      `StrategyConfig.symbol`, and `binance.yaml`'s copy is only used for that adapter's
      own bookkeeping. **The out-of-the-box default `strategy.yaml` symbol is `"EURUSD"`
      (forex format) — this will not just "trade the wrong thing", it will raise ccxt's
      `BadSymbol` error outright** (verified in this project's own history) since
      `"EURUSD"` isn't a valid ccxt/Binance symbol at all. You must edit `strategy.yaml`.
- [ ] Confirm market info loads for the symbol:
  ```powershell
  python -c "
  from trend_only_scalper.config import load_binance_config
  from trend_only_scalper.brokers.binance_broker import BinanceBroker
  cfg = load_binance_config('config/binance.yaml')
  b = BinanceBroker(cfg)
  b.connect()
  m = b._exchange.market(cfg.symbol)
  print('precision:', m['precision'])
  print('limits:', m['limits'])
  "
  ```
  Note `precision.amount`/`precision.price` and `limits.amount.min`/`limits.cost.min`.

## 6. Leverage and margin checklist

- [ ] `config/binance.yaml`'s `leverage` is set to a conservative value (e.g. `2`) for testing.
- [ ] `connect()` only attempts to set leverage **when `allow_live_trading` is already
      true** and `market_type == "futures"` — it is a **best-effort call wrapped in
      try/except that only logs a warning on failure**, it does not block startup. Confirm
      leverage actually applied by checking the testnet UI's position/leverage display
      after connecting, don't just trust the log line.
- [ ] Confirm sufficient margin is available for `default_quantity` (see §7 — this, not
      `binance.yaml`'s `quantity`, is what actually gets sized) at the chosen leverage.
- [ ] Confirm testnet's margin mode (isolated vs. cross) matches your expectations — this
      bot does not configure margin mode itself; whatever the account/symbol already has
      set is what's used.

## 7. Config checklist

- [ ] **`strategy.yaml`'s `symbol` and `binance.yaml`'s `symbol` are the same string** (§5).
- [ ] **⚠️ `strategy.yaml`'s `default_quantity` — NOT `binance.yaml`'s `quantity` — controls
      the real order size.** Confirmed by inspection: `binance_broker.py` never reads
      `config.quantity` anywhere; `main.py`'s `run_iteration()` always passes
      `cfg.default_quantity` into `open_market_order()`. **This is more dangerous here than
      on MT5**: the default `default_quantity: 1.0` on a symbol like BTC/USDT means **1
      whole BTC** per order. For minimum-quantity testnet testing (§9), set
      `default_quantity` in `strategy.yaml` directly to something tiny (e.g. `0.001`) —
      editing `binance.yaml`'s `quantity` field alone does **nothing**.
- [ ] **⚠️ Check the cost-vs-target-profit dimensional mismatch before testing.**
      `get_trading_cost()` returns `2 * fee_rate_estimate * price` (a price-unit figure,
      independent of quantity), and `run_iteration()` blocks all trading if
      `trading_cost >= tp_cash`. Verified directly:

      | Symbol price | `get_trading_cost()` (fee_rate_estimate=0.0004) | Blocks trading with default `tp_cash: 1.50`? |
      |---|---|---|
      | $100 (small altcoin) | 0.08 | No |
      | $62,500 (BTC/USDT) | 50.00 | **Yes — bot will never trade** |
      | $100,000 (BTC/USDT) | 80.00 | **Yes — bot will never trade** |

      For BTC/USDT-scale symbols, either (a) raise `tp_cash` substantially (and re-check it
      still makes sense against `default_quantity`'s real notional value), or (b) test
      functional behavior first against a much lower-priced testnet symbol, then revisit
      cash targets before trading your actual intended symbol. **This is the single most
      likely reason the bot appears to "do nothing" on testnet** — check
      `trading_cost=too_high` in the decision log before assuming anything else is wrong.
- [ ] Note: `max_cost_ratio_to_tp` and the `estimate_fee_cash()`/`is_cost_too_high_for_target()`
      helper methods on `BinanceBroker` exist but are **not called anywhere in `main.py` or
      `cli.py`** — they have no effect on the live loop currently. Don't rely on them.
- [ ] `allow_live_trading: false` for §8; only `true` starting §9.
- [ ] For the first order-placement test, tighten daily-guard values in `strategy.yaml`,
      e.g. `max_trades_per_day: 3`, `daily_max_loss: -1.0`, `max_consecutive_losses: 2`.
      Restore production values afterward.
- [ ] Validate config loads and passes built-in validation:
  ```powershell
  python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml
  ```
  (Only reports `strategy.yaml`'s flags — check `binance.yaml`'s `allow_live_trading`/`testnet`
  directly per §2's snippet, a known gap in `safety-report`.)

## 8. Dry-run validation (real Binance data, simulated orders only)

`allow_live_trading: false` — no real order can be sent regardless of what happens.

```powershell
python -m trend_only_scalper.cli binance-demo --strategy config/strategy.yaml --broker config/binance.yaml --iterations 20 --loop-interval 5
```

- [ ] Safety report prints, followed by `testnet: True   market_type: futures`.
- [ ] Console shows `allow_live_trading is False -- real Binance data, simulated order placement only.`
- [ ] Log shows `Binance exchange connected (symbol=..., market_type=futures, testnet=True)`.
- [ ] Decision log shows real `m15_trend`/`m5_confirmation` values (not stuck on `n/a`) once
      enough bars have loaded — confirms real OHLCV is flowing from Binance.
- [ ] If `trading_cost=too_high` appears every iteration, revisit §7's cost-vs-`tp_cash` check.
- [ ] **Nothing appears in the testnet UI's positions/orders at all during this run.**
- [ ] Run at least 1–2 hours of dry-run before proceeding.

## 9. Testnet order validation with minimum quantity

Only proceed once §1–8 are fully clean.

- [ ] Re-confirm §2 (testnet, not mainnet) **immediately before this step**.
- [ ] Set `binance.yaml`: `allow_live_trading: true`.
- [ ] Set `strategy.yaml`: `default_quantity` to the symbol's minimum tradable amount
      (check `limits.amount.min` from §5's snippet — commonly `0.001` for BTC/USDT, but
      verify for your symbol).
- [ ] Run the same command as §8. The CLI prints:
  ```
  testnet: True   market_type: futures

  *** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to Binance (TESTNET). ***

  Press Enter to continue, or Ctrl+C to abort...
  ```
  **Stop and re-verify testnet/account before pressing Enter.**
- [ ] On the first real entry: testnet UI shows a new position matching direction and
      quantity; log shows `Binance order response: id=... status=...` for the market entry,
      followed immediately by a second `create_order` call for the `STOP_MARKET` stop.
- [ ] Confirm **both** orders appear in the testnet UI's order history (entry market order,
      then a separate reduce-only STOP_MARKET order) — Binance has no single atomic
      "order + attached SL" call the way MT5 does; this project sends them as two calls.

## 10. Reduce-only close validation

- [ ] Confirm `close_position()`'s flow: it first cancels the tracked stop order
      (`_cancel_stop_order`), **then** sends a `market` order with `reduceOnly: True` in the
      opposite direction for the full held quantity.
- [ ] Confirm the testnet UI shows the closing order tagged as reduce-only, and that it
      only reduces/flattens the existing position — it must never be able to flip into a
      new position in the opposite direction (Binance's `reduceOnly` flag enforces this
      exchange-side).
- [ ] Confirm the log line: `Binance close order response: id=... status=... reason=...`.
- [ ] ⚠️ Note the brief window between cancelling the old stop and the close order filling
      where the position technically has no active stop order — this is intentional
      (we're actively closing right after) but worth knowing: if the close order itself
      failed at that exact moment (network blip), the position would be briefly naked until
      the next iteration's retry. There is currently no automatic retry specifically for a
      failed close within the same call.

## 11. Stop loss validation

- [ ] Confirm `open_market_order()` places the entry, then the `STOP_MARKET` reduce-only
      stop, in that order (two separate API calls — verified by inspection).
- [ ] **Deliberately test the failure path once, safely, on testnet**: temporarily set an
      invalid `stopPrice` scenario (e.g. a stop price violating the exchange's `PERCENT_PRICE`
      filter) to force the second `create_order` call to fail, and confirm:
  - Log shows `Stop-loss order failed to place after entry filled (...) -- closing the
    position immediately to avoid a naked position: ...`.
  - A compensating reduce-only market close is attempted and its outcome logged
    (`Compensating close succeeded -- no naked position remains for ...` or, in the worst
    case, a `CRITICAL`-level `COMPENSATING CLOSE ALSO FAILED` message).
  - `open_market_order()` **always raises** in this scenario — confirm no position is
    left tracked as "successfully opened" in the bot's own state afterward.
- [ ] Confirm on a **normal** (non-forced-failure) entry that the SL price shown in the
      testnet UI matches the bot's log line.
- [ ] When a position eventually closes via its stop hitting naturally, confirm the next
      iteration logs `position=... vanished without an explicit close (broker-side hard
      stop-loss assumed) -- recording HARD_SL`, a journal row is appended with
      `reason_close=HARD_SL`, and cooldown starts (`cooldown_after_sl_bars`).

## 12. Breakeven validation

- [ ] Watch a live position's `pnl=` value in the bot's log.
- [ ] Once `pnl` crosses `breakeven_trigger_cash` (default 0.70), confirm the log shows the
      new-stop-before-cancel-old-stop sequence (verified by inspection of
      `modify_stop_loss()`): a new `STOP_MARKET` order is created first, then the old one
      is cancelled — never the reverse.
- [ ] Confirm the testnet UI shows the updated stop price after the modification.
- [ ] **Deliberately test the failure path once**: force the new-stop `create_order` call
      to fail (e.g. invalid price) and confirm:
  - Log: `Failed to place updated stop-loss for ... -- the previous stop-loss order was
    left in place (not cancelled) and still protects the position: ...`.
  - The **old** stop order is still visible and active in the testnet UI — it must **not**
    have been cancelled.
- [ ] Confirm the SL never regresses backward on subsequent bars once moved to breakeven
      (`_improves_stop_loss` guard in `position_manager.py`).
- [ ] ⚠️ **Same gap as MT5 — do not expect a `BREAKEVEN_SL` row in the journal.** No code
      path in this version ever assigns `CloseReason.BREAKEVEN_SL`; a breakeven-protected
      position that later stops out is recorded as `HARD_SL`. `cooldown_after_be_bars` is
      therefore currently unreachable through real trading.

## 13. Cash TP validation

- [ ] Wait for `pnl` to reach `tp_cash`.
- [ ] Confirm log: `closed position=... reason=TP_CASH pnl=...`.
- [ ] Testnet UI shows the position closed via a reduce-only market order; confirm realized
      P&L is close to `tp_cash` (variance expected from fees/slippage).
- [ ] Journal row appended with `reason_close=TP_CASH`.
- [ ] Cooldown starts (`cooldown_after_tp_bars`, default 1 bar).

## 14. One-position-only validation

- [ ] **Binance has no magic-number equivalent** (confirmed by the module's own docstring)
      — a position is reported per symbol, not per originating order. This adapter
      assumes the whole symbol is dedicated to this bot.
- [ ] While a position is open, confirm across several iterations that no second position
      ever opens even with a fresh valid signal (`open_market_order()` raises `RuntimeError`
      if `get_position_count() > 0`, which the manage-or-scan gate in `run_iteration()`
      should prevent from ever being reached during normal operation).
- [ ] **Do not manually open a position on the same testnet symbol while the bot runs** —
      unlike MT5's magic-number filter, `get_open_position()` here would treat any manual
      position on that symbol as "ours", with an unpredictable interaction (its stop-loss
      wouldn't be tracked in `_stop_loss_by_symbol`, since the bot never opened it).
- [ ] Confirm `Binance reports N open positions for ...; one-position-only expects at most
      1` warning appears (rather than a crash) if this scenario is somehow triggered anyway.

## 15. Daily guard validation

Use the tightened test values from §7 for these checks, then restore production values after.

- [ ] **Max trades/day**: after the configured count of trades close, confirm
      `daily_guard=blocked:max_trades_per_day_reached` and no further entry that day.
- [ ] **Daily max loss**: after cumulative realized loss breaches the threshold, confirm
      `blocked:daily_max_loss_reached`.
- [ ] **Daily profit target**: after cumulative realized profit reaches it, confirm
      `blocked:daily_profit_target_reached`.
- [ ] **Max consecutive losses**: after N losing closes in a row, confirm
      `blocked:max_consecutive_losses_reached`.
- [ ] Confirm an already-open position keeps being managed even while the guard blocks new
      entries (the guard only gates new entries).
- [ ] ⚠️ **Same in-memory-state risk as MT5**: `DailyStats` resets to zero if the process
      restarts mid-day. Avoid restarting during a live test window; cross-check
      `logs/trade_journal_binance.csv` for the day's real trade count if a restart happens.

## 16. Cooldown validation

- [ ] After a TP close (`cooldown_after_tp_bars: 1`), confirm cooldown clears after exactly
      1 tick — watch the decision log's `cooldown=` field iteration by iteration.
- [ ] After a hard-SL close (`cooldown_after_sl_bars: 5`), confirm cooldown blocks entries
      for 5 subsequent iterations.
- [ ] Confirm cooldown never blocks position management.
- [ ] (See §12 — `cooldown_after_be_bars` cannot currently be exercised through real trading.)

## 17. Fee/spread/slippage validation

- [ ] Binance futures spreads are typically very tight; this project doesn't read the
      live order-book spread for Binance at all. `get_trading_cost()` is a **fee-based
      estimate only** (`2 * fee_rate_estimate * price`), not a real bid/ask read — confirm
      you understand this is an approximation, not a live market read (see §7's worked table).
- [ ] Compare `fee_rate_estimate` (default `0.0004`, i.e. taker 0.04%/side) against the
      testnet account's actual fee tier if visible in the UI, and adjust if materially different.
- [ ] Confirm `detect_entry_signal()`'s own ATR-vs-cost check (`min_atr_spread_multiple`,
      default 3.0, using `get_trading_cost()`'s value as the "spread") rejects entries when
      volatility is too low relative to estimated cost — watch for `no_m1_entry_signal`
      during quiet periods.
- [ ] Observe actual fill price vs. the ticker price at the moment of order placement across
      several trades to build a real sense of testnet slippage (there's no configurable
      slippage/deviation guard for Binance the way MT5 has `deviation` points).

## 18. Journal validation

Journal path for this backend: `logs/trade_journal_binance.csv` (override with `--journal-path`).

For every closed trade, cross-check the CSV row against the testnet UI's trade history:

| Column | What to verify |
|---|---|
| `timestamp`, `broker` | `BinanceBroker`, timestamp roughly matches the close time |
| `symbol`, `side`, `quantity` | Matches testnet UI exactly |
| `entry_price`, `exit_price` | Matches testnet UI within slippage tolerance |
| `stop_loss_initial` vs `stop_loss_final` | Different if breakeven fired; same otherwise |
| `realized_pnl` | Close to testnet UI's P&L (variance from fees) |
| `reason_open` | e.g. `m1_pullback_bounce` / `m1_rebound_rejection` |
| `reason_close` | `TP_CASH` or `HARD_SL` only in this version (see §12) |
| `m15_trend`, `m5_confirmation`, `m1_signal` | Non-`n/a`, consistent with the trend at entry |
| `daily_pnl_after_trade`, `consecutive_losses_after_trade`, `trades_today` | Running totals make sense |
| `dry_run` | Matches `strategy.yaml`'s `dry_run` at the time |

- [ ] Confirm `daily_pnl_after_trade` (from `get_today_realized_pnl()`) is plausible —
      this reads Binance's raw `info.realizedPnl` field via `fetch_my_trades()`, which is
      an unofficial passthrough field, not part of ccxt's stable unified trade structure.
      Cross-check it against the testnet UI's own daily P&L display periodically, not just once.
- [ ] Confirm the file is appended to, never overwritten, across a restart.

## 19. API error handling validation

- [ ] Confirm every real order attempt logs a response
      (`Binance order response: id=... status=...` / `Binance close order response: ...`).
- [ ] Trigger a real API error safely on testnet (e.g. temporarily set `default_quantity`
      below the symbol's `limits.amount.min` after bypassing normalization, or set an
      invalid `recv_window`) and confirm the error surfaces as a logged exception rather
      than crashing silently.
- [ ] Confirm `_cancel_stop_order()`'s failures are caught and logged as warnings, not
      raised (cancelling an already-filled/expired stop order is expected to sometimes
      fail harmlessly — verify this doesn't block the rest of `close_position()`/`modify_stop_loss()`).
- [ ] Controlled resilience test: briefly disable network access mid-run and confirm:
  - The loop logs `run_iteration() raised an unexpected error (N/5 consecutive)` and **keeps running**.
  - It recovers automatically once connectivity returns within the 5-failure window.
  - If the outage persists through 5 consecutive failures, the process logs
    `5 consecutive errors -- stopping` and exits (`MAX_CONSECUTIVE_ITERATION_ERRORS = 5`
    in `cli.py`) rather than hammering the API forever.
- [ ] Confirm rate-limit handling: `enableRateLimit: True` is set on the ccxt exchange
      instance by `_build_exchange()` — watch for any `RateLimitExceeded`-style errors
      during a longer test run and confirm the loop's error-resilience path handles them
      the same as any other transient error.

## 20. Shutdown checklist

- [ ] Press Ctrl+C with no open position: confirm
      `Shutdown requested (signal 2). Finishing current iteration...` then a clean exit.
      `disconnect()` calls `exchange.close()` if the ccxt exchange exposes it.
- [ ] Press Ctrl+C **while a position is open**: confirm the position is **not** force-closed
      — the STOP_MARKET order already resting on the exchange remains active and
      independent of the Python process.
- [ ] Restart the bot with the position still open: confirm `get_open_position()` finds it
      correctly and resumes managing it, including recognizing its existing stop
      (`_stop_loss_by_symbol`/`_stop_order_id_by_symbol` are rebuilt fresh in memory on
      restart — see the known-risk note in §24 about what this means for `stop_loss`
      reporting immediately after a restart).
- [ ] ⚠️ Same context-loss note as MT5: `state.open_trade_context` doesn't survive a
      restart — a reopened-and-still-managed position's eventual journal row will show
      `m15_trend=n/a`, `reason_open=""`, etc.

## 21. What logs to capture

- [ ] Full console output per session:
  ```powershell
  python -m trend_only_scalper.cli binance-demo ... 2>&1 | Tee-Object -FilePath logs\binance_testnet_session_YYYYMMDD_HHMM.log
  ```
- [ ] `logs/trend_only_scalper.log` (rotating, 5MB × 5 backups) — archive after each phase.
- [ ] `logs/trade_journal_binance.csv` — archive a timestamped snapshot after each milestone.
- [ ] Decision-log lines only:
  ```powershell
  Select-String -Path logs\trend_only_scalper.log -Pattern "trend_only_scalper.decision" > logs\decisions_only.log
  ```
- [ ] Testnet UI's own order history / trade history export (independent of our logging —
      the exchange-side record).
- [ ] Screenshot equivalents where the UI doesn't export cleanly: account balance before
      each phase, open position details (entry, stop order, quantity), closed trade P&L,
      and the bot's console at each key event (open, breakeven modify, TP close, guard block).

## 22. Criteria to stop testing immediately

- [ ] **Any real order appears against mainnet instead of testnet** — stop, disconnect,
      re-verify `testnet: true` and the API key source before touching anything else.
- [ ] A position opens without a corresponding `STOP_MARKET` order visible in the testnet UI.
- [ ] More than one position open at once on the traded symbol.
- [ ] The bot opens a position against the currently confirmed M15/M5 trend.
- [ ] The process exits via the 5-consecutive-error path (§19) — stop and root-cause first.
- [ ] Any unhandled Python traceback outside the documented graceful paths.
- [ ] The daily guard is breached (per journal/logs) and the bot still opens a new trade after.
- [ ] A `COMPENSATING CLOSE ALSO FAILED` critical log ever appears — this means a real
      unprotected position may exist; resolve manually via the testnet UI immediately.
- [ ] Any doubt at all about whether you're pointed at testnet vs. mainnet.

## 23. Criteria to proceed to the next testnet phase

- [ ] Every checklist item in the current section passed, repeated across multiple
      trades/iterations, not just once.
- [ ] No unexplained errors/warnings beyond the documented/expected ones (§11, §12, §19, §20).
- [ ] Every journal row cross-checked against the testnet UI matches within tolerance (§18).
- [ ] Cooldown and daily-guard behavior matched configured values exactly, every time.
- [ ] The forced-failure tests in §11 and §12 (stop-placement failure paths) were run at
      least once and behaved exactly as documented.
- [ ] Logs and evidence archived per §21.
- [ ] A second person (if available) has reviewed the evidence before moving from
      minimum-quantity testing (§9) toward any larger size or longer unattended run.

## 24. Known risks and how to monitor them

| Risk | Why it matters | How to monitor |
|---|---|---|
| `binance.yaml`'s `quantity` field is unused | `strategy.yaml`'s `default_quantity` controls real order size; the unedited default (`1.0`) is **1 whole BTC** on BTC/USDT | Re-check `default_quantity` before every real-order test, not `binance.yaml`'s `quantity` |
| `tp_cash` vs. `get_trading_cost()` dimensional mismatch | At BTC/USDT price scale, the default `tp_cash: 1.50` makes the bot refuse to trade forever (verified: cost ≈ $50–80 vs. tp_cash $1.50) | Compute `2 * fee_rate_estimate * price` for your symbol and confirm it's below `tp_cash` before testing |
| `max_cost_ratio_to_tp` / `estimate_fee_cash()` / `is_cost_too_high_for_target()` are dead code | These exist on `BinanceBroker` but are never called by `main.py`/`cli.py` | Don't rely on them; the only real cost gate is `get_trading_cost() >= tp_cash` in `run_iteration()` |
| No magic-number equivalent | Any manual position on the traded symbol is indistinguishable from the bot's own | Dedicate the testnet symbol entirely to this bot; never manually trade it during a test |
| Two-step order placement (entry, then STOP_MARKET) | A failure between the two calls used to leave a naked position — now fixed with a compensating close, but still worth watching | Watch for the `Stop-loss order failed to place...` log path on every session, confirm compensating closes succeed when triggered |
| `DailyStats` is in-memory only | A process restart mid-day silently resets `trade_count`/`consecutive_losses` | Avoid restarts during a live window; cross-check the journal CSV if one happens |
| `CloseReason.BREAKEVEN_SL` is unreachable | Breakeven-protected closes are journaled as `HARD_SL`; `cooldown_after_be_bars` never triggers | Expected, not a defect — don't chase it |
| `get_today_realized_pnl()` reads a raw, unofficial `info.realizedPnl` field | Could silently break if Binance/ccxt change the raw response shape | Periodically cross-check `daily_pnl_after_trade` in the journal against the testnet UI's own P&L |
| `get_account_equity()` assumes USDT collateral | Returns `0.0` silently for a non-USDT-margined account | Confirm the account is USDT-margined futures (§4) before relying on equity readings |
| Independent `symbol` fields in `strategy.yaml` vs `binance.yaml` | Mismatch trades the wrong symbol, or (for the shipped default) raises `BadSymbol` outright | Confirm both files agree before every run (§5, §7) |
| `MAX_CONSECUTIVE_ITERATION_ERRORS = 5` | The loop gives up and exits after 5 consecutive failures | Watch for repeated warning-level errors as an early signal |
| No automatic retry on a failed close order | A transient failure closing a position leaves it open until the next loop iteration | Monitor for repeated close attempts in the log if a close doesn't succeed immediately |
