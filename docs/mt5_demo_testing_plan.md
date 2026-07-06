# MT5 Demo Testing Plan — trend_only_scalper

Practical, step-by-step validation plan for running this bot against a real MT5 terminal
on a **demo account**. Every command and field name below is taken directly from the
current codebase (`config/mt5.yaml`, `config/strategy.yaml`, `cli.py`,
`brokers/mt5_broker.py`) — not generic advice.

> **This plan assumes `config/mt5.yaml`'s `allow_live_trading` starts at `false`.**
> Sections 1–6 are dry-run only (no real orders possible). Do not proceed past section 6
> until every dry-run check passes and you have manually re-confirmed the account is a
> demo account.

---

## 1. Environment checklist

- [ ] OS is Windows (the `MetaTrader5` package only installs/works on Windows).
- [ ] Virtual environment activated: `.venv\Scripts\activate`.
- [ ] Dependencies installed: `pip install -r requirements.txt && pip install -e .`.
- [ ] `python -m trend_only_scalper.cli --help` runs and lists all 5 subcommands.
- [ ] `python -c "import MetaTrader5; print(MetaTrader5.__version__)"` succeeds.
- [ ] System clock is correct and NTP-synced. Daily-stats rollover and `get_today_realized_pnl()`
      both key off wall-clock UTC date (`datetime.now(timezone.utc)`) — a wrong clock
      silently breaks the daily guard's "today" boundary.
- [ ] Stable internet connection to the broker's trade servers (test with a ping/tracert
      to the server host if unsure).
- [ ] Confirm no other EA, script, or bot is already trading on the same MT5 terminal/account
      that could interfere or share the same magic number.

## 2. MT5 terminal checklist

- [ ] MT5 terminal installed; note the build number (`Help → About`, or
      `python -c "import MetaTrader5 as mt5; mt5.initialize(); print(mt5.version())"`).
- [ ] Terminal is **open and logged in** before starting the bot (or valid
      `MT5_LOGIN`/`MT5_PASSWORD`/`MT5_SERVER` are set in `.env` so `MT5Broker.connect()`
      can log in itself — see `config/mt5.yaml`'s `login_env`/`password_env`/`server_env`).
- [ ] **AutoTrading / "Algo Trading" button is enabled (green)** in the terminal toolbar —
      MT5 rejects `order_send()` while this is off, regardless of what the Python side does.
- [ ] Logged in with a **trade-enabled password**, not an investor/read-only password.
- [ ] No blocking dialogs (update prompts, license renewal, disconnect banners) covering the terminal.
- [ ] Terminal → Tools → Options → Expert Advisors: "Allow automated trading" is checked.

## 3. Broker/account checklist

- [ ] **Confirm the connected account is a DEMO account — do not assume, verify:**
  ```powershell
  python -c "import MetaTrader5 as mt5; mt5.initialize(); info = mt5.account_info(); print(info)"
  ```
  Check `trade_mode` in the output: `0` = demo, `2` = real. Also check `server` — a
  server name containing "-Live" or similar is a red flag. **`MT5Broker.connect()` does
  not check this for you; this manual step is the only safeguard.**
- [ ] Record: account number, server name, account currency, leverage, starting balance/equity.
- [ ] Confirm `trade_allowed` is `True` in `account_info()`.
- [ ] Confirm demo balance is sufficient for the planned lot size (margin required for even
      a minimum lot on the chosen symbol).
- [ ] Confirm account currency matches the assumption behind cash values (`tp_cash`,
      `breakeven_trigger_cash`, `daily_max_loss`, etc. are all plain cash amounts in the
      account's currency — a JPY or non-USD demo account changes what "$1.50" means in practice).

## 4. Symbol checklist

- [ ] The symbol in `config/mt5.yaml` **and** `config/strategy.yaml` (they are independent
      fields — see §5) is visible in Market Watch and its data is streaming (right-click →
      Show All if hidden).
- [ ] Check `symbol_info()` for the symbol:
  ```powershell
  python -c "import MetaTrader5 as mt5; mt5.initialize(); mt5.symbol_select('EURUSD', True); print(mt5.symbol_info('EURUSD'))"
  ```
  Note `digits`, `volume_min`, `volume_step`, `volume_max`, and `trade_stops_level` (the
  broker's minimum distance between price and stop-loss, in points).
- [ ] Confirm the swing-based stop distance the bot will compute (`swing_lookback` +
      `sl_atr_buffer` from `strategy.yaml`) is comfortably larger than `trade_stops_level` —
      if not, the broker will reject the order outright.
- [ ] Confirm current spread is reasonable and below `max_spread_points` in `config/mt5.yaml`.
- [ ] Avoid testing right at session rollover/close (spread widens dramatically; not
      representative of normal conditions).

## 5. Config checklist

- [ ] **`config/strategy.yaml`'s `symbol` and `config/mt5.yaml`'s `symbol` are the SAME
      string.** These are independent fields — `run_iteration()` always trades
      `StrategyConfig.symbol`; `mt5.yaml`'s `symbol` is only used for that adapter's own
      internal bookkeeping/logging. A mismatch won't error, it will just quietly trade
      whatever `strategy.yaml` says.
- [ ] **`strategy.yaml`'s `default_quantity` is what actually controls the lot size sent to
      MT5 — NOT `mt5.yaml`'s `lot` field.** Confirmed by inspection: `mt5_broker.py` never
      reads `config.lot` anywhere; `main.py`'s `run_iteration()` always passes
      `cfg.default_quantity` (from `strategy.yaml`) into `open_market_order()`. **For
      minimum-lot testing (§7), set `default_quantity: 0.01` in `strategy.yaml` — editing
      `mt5.yaml`'s `lot` alone does nothing.** The value is still clamped by
      `_normalize_volume()` to the symbol's `volume_min`/`volume_step`/`volume_max`, but it
      will NOT be clamped down to a "safe" minimum on its own — the default
      `default_quantity: 1.0` would place a full 1.0-lot order if left unchanged.
- [ ] `magic` in `mt5.yaml` is unique — not shared with any other EA/script on the account.
- [ ] For the **first** live-order test, temporarily tighten daily-guard values in
      `strategy.yaml` so they can be exercised quickly and safely, e.g.:
      `max_trades_per_day: 3`, `daily_max_loss: -1.0`, `max_consecutive_losses: 2`. Restore
      production values afterward.
- [ ] `allow_live_trading: false` in `mt5.yaml` for §6; only set `true` starting §7.
- [ ] `dry_run: true` in `strategy.yaml` throughout initial testing.
- [ ] Sanity-check cash values against the symbol's price scale: e.g. for EURUSD at
      `default_quantity: 0.01` (1,000 units), 1 pip (0.0001) ≈ $0.10, so `tp_cash: 1.50`
      needs roughly a 15-pip favorable move — reasonable; recompute this for whatever
      symbol/quantity you actually use.
- [ ] Validate config loads and passes the built-in safety validation:
  ```powershell
  python -m trend_only_scalper.cli safety-report --strategy config/strategy.yaml
  ```
  Note: this only reports `strategy.yaml`'s flags, **not** `mt5.yaml`'s `allow_live_trading`
  (a known gap). Check that one directly:
  ```powershell
  python -c "from trend_only_scalper.config import load_mt5_config; c = load_mt5_config('config/mt5.yaml'); print('allow_live_trading:', c.allow_live_trading, ' magic:', c.magic, ' deviation:', c.deviation)"
  ```

## 6. Dry-run validation (real MT5 data, simulated orders only)

`allow_live_trading: false` — no real order can be sent regardless of what happens.

```powershell
python -m trend_only_scalper.cli mt5-demo --strategy config/strategy.yaml --broker config/mt5.yaml --iterations 20 --loop-interval 5
```

- [ ] Safety report prints at startup.
- [ ] Console/log shows `allow_live_trading is False -- real MT5 data, simulated order placement only.`
- [ ] Log shows `MT5 terminal connected (symbol=..., magic=...)`.
- [ ] After enough iterations, decision log lines show real `m15_trend=up/down/none` and
      `m5_confirmation=...` (not stuck on `n/a`) — confirms real bars are flowing from MT5.
- [ ] **MT5 terminal's Trade tab shows nothing new at all during the entire run** — this is
      the whole point of this phase.
- [ ] If a simulated entry fires, confirm `logs/trade_journal_mt5.csv` gets a row with
      populated `m15_trend`/`m5_confirmation`/`m1_signal`/`reason_open`.
- [ ] Run at least 1–2 hours of dry-run during active market hours before proceeding.

## 7. Demo order validation with minimum lot

Only proceed once §1–6 are fully clean.

- [ ] Re-verify §3 (demo account) **immediately before this step**, not just earlier today.
- [ ] Set `mt5.yaml`: `allow_live_trading: true`.
- [ ] Set `strategy.yaml`: `default_quantity: 0.01` (see §5 — this is the field that matters).
- [ ] Run the same command as §6. The CLI will print:
  ```
  *** allow_live_trading is TRUE -- REAL ORDERS WILL BE SENT to the connected
  MT5 account (magic=987001). Confirm this is a DEMO account first. ***

  Press Enter to continue, or Ctrl+C to abort...
  ```
  **Stop here and manually re-check the account before pressing Enter.**
- [ ] On the first real entry: MT5 terminal's Trade tab shows a new position with matching
      direction, volume (0.01), and an SL price populated (not empty).
- [ ] Order comment shows `trend_only_scalper` and the position's magic number matches `mt5.yaml`.
- [ ] Entry price is close to the market price at that moment (slippage within `deviation` points).
- [ ] Log line: `order_send filling=IOC retcode=10009 comment=...` (10009 = `TRADE_RETCODE_DONE`).

## 8. Stop loss validation

- [ ] Confirm the SL shown in the MT5 terminal matches the bot's log line
      (`opened ... sl=...`) for the entry.
- [ ] Confirm SL is on the correct side: below entry for BUY, above entry for SELL.
- [ ] When the position eventually closes via its stop (naturally, or by choosing a
      deliberately tight `sl_atr_buffer` for one controlled test), confirm:
  - Position disappears from the MT5 terminal.
  - Bot's **next** iteration logs:
    `position=... vanished without an explicit close (broker-side hard stop-loss assumed) -- recording HARD_SL`.
  - A journal row is appended with `reason_close=HARD_SL`.
  - Cooldown starts (`cooldown_after_sl_bars`, default 5 M1 bars) — confirm the decision
    log shows `cooldown=active:N` for the next several iterations.

## 9. Breakeven validation

- [ ] Watch a live position's `pnl=` value in the bot's log as price moves favorably.
- [ ] Once `pnl` crosses `breakeven_trigger_cash` (default 0.70), confirm log:
      `modified stop loss position=... new_sl=...`.
- [ ] Confirm the MT5 terminal's SL for that position updates to the new value
      (`order_send` with `TRADE_ACTION_SLTP`, retcode 10009).
- [ ] Confirm new SL ≈ entry_price ± (`breakeven_lock_cash` / `default_quantity`) — a small
      guaranteed profit, not exactly break-even.
- [ ] Watch several more iterations and confirm the SL **never regresses backward** once
      moved to breakeven (`_improves_stop_loss` guard in `position_manager.py`).
- [ ] **Known gap — do not expect a `BREAKEVEN_SL` row in the journal.** Verified by
      inspection: no code path in this version ever assigns `CloseReason.BREAKEVEN_SL` to a
      closed trade. `manage_position()` only ever returns `TP_CASH` (on close) or
      `MODIFY_SL` (breakeven just moves the stop, it doesn't close). If a
      breakeven-protected position is later stopped out, it's recorded as `HARD_SL`, even
      though the stop itself sits at the improved breakeven level. `cooldown_after_be_bars`
      is therefore currently unreachable through real trading — it only exists in the
      cooldown module's own unit tests. This is expected, not a bug to chase.

## 10. Cash TP validation

- [ ] Wait for `pnl` to reach `tp_cash` (default 1.50).
- [ ] Confirm log: `closed position=... reason=TP_CASH pnl=...`.
- [ ] MT5 terminal shows the position closed (moves to History tab); realized P&L should be
      close to `tp_cash` (small variance from spread/slippage/commission is expected).
- [ ] Journal row appended with `reason_close=TP_CASH`.
- [ ] Cooldown starts (`cooldown_after_tp_bars`, default 1 bar).

## 11. One-position-only validation

- [ ] While a position is open, confirm across several iterations that no second position
      ever opens even if a fresh, valid opposite-direction signal appears.
- [ ] Confirm code-level enforcement holds: `MT5Broker.open_market_order()` raises
      `RuntimeError` if `get_position_count() > 0` — this should never surface as a user-visible
      error during normal operation because `run_iteration()`'s manage-or-scan gate prevents
      reaching `open_market_order()` at all while a position is open.
- [ ] Edge case: if a **manual** position with the **same magic number** somehow appears on
      the account while the bot also has one open, confirm the log shows the warning
      `MT5 reports N open positions with magic=...; one-position-only expects at most 1 --
      using the first and leaving the rest untouched` rather than crashing or double-managing.

## 12. Daily guard validation

Use the tightened test values from §5 (`max_trades_per_day: 3`, `daily_max_loss: -1.0`,
`max_consecutive_losses: 2`) for these checks, then restore production values afterward.

- [ ] **Max trades/day**: after the configured number of trades close, confirm the decision
      log shows `daily_guard=blocked:max_trades_per_day_reached` and no further entry occurs
      that day.
- [ ] **Daily max loss**: after cumulative realized loss breaches the threshold, confirm
      `blocked:daily_max_loss_reached` and no further entry occurs.
- [ ] **Daily profit target**: after cumulative realized profit reaches the threshold,
      confirm `blocked:daily_profit_target_reached`.
- [ ] **Max consecutive losses**: after N losing closes in a row, confirm
      `blocked:max_consecutive_losses_reached`.
- [ ] Confirm in every case above that **an already-open position keeps being managed**
      (TP/breakeven checks continue) even while the guard blocks new entries — the guard
      only gates new entries, never position management.
- [ ] **Day-rollover check**: let the bot run across a UTC midnight boundary (or, in a
      controlled non-demo-affecting test only, verify via unit tests instead) and confirm
      `trade_count`/`consecutive_losses` reset for the new day while any in-progress
      position continues to be managed correctly.
- [ ] ⚠️ **Operational risk to know about**: `DailyStats` is in-memory only — restarting the
      bot process resets `trade_count`/`consecutive_losses` to zero even mid-day. Avoid
      casually restarting the process during a live test window; if a restart is
      unavoidable, manually cross-check the day's trade count from
      `logs/trade_journal_mt5.csv` before assuming the guard's state is trustworthy.

## 13. Cooldown validation

- [ ] After a TP close (`cooldown_after_tp_bars: 1`), confirm the cooldown clears after
      exactly 1 tick — check the decision log's `cooldown=` field iteration by iteration.
- [ ] After a hard-SL close (`cooldown_after_sl_bars: 5`), confirm cooldown blocks entries
      for 5 subsequent iterations, then allows scanning again.
- [ ] Confirm cooldown **never blocks position management** — if a position happens to be
      open during what would otherwise be a cooldown window (shouldn't normally happen
      given one-position-only, but verify), management still proceeds.
- [ ] (See §9 — `cooldown_after_be_bars` cannot currently be exercised through real trading.)

## 14. Spread filter validation

- [ ] Confirm `get_trading_cost()` (MT5's live `ask - bid`) is checked against `cfg.tp_cash`
      in `run_iteration()` — if spread ever exceeds `tp_cash`, confirm the decision log shows
      `trading_cost=too_high` and `no_trade_reason=trading_cost_too_high`.
- [ ] Separately, confirm `detect_entry_signal()`'s own ATR-vs-spread check
      (`min_atr_spread_multiple`, default 3.0) rejects entries when ATR is too small relative
      to spread — watch for `no_m1_entry_signal` during low-volatility/high-spread periods
      (e.g. right after a news release or at session open).
- [ ] Try widening the spread artificially impossible in real MT5 — instead, test this
      indirectly by watching behavior during a naturally wide-spread period (session
      rollover) and confirming the bot correctly stays out.

## 15. Journal validation

Journal path for this backend: `logs/trade_journal_mt5.csv` (override with `--journal-path`).

For every closed trade, cross-check the CSV row against the MT5 terminal's History tab:

| Column | What to verify |
|---|---|
| `timestamp`, `broker` | `MT5Broker`, timestamp roughly matches the close time |
| `symbol`, `side`, `quantity` | Matches the terminal exactly |
| `entry_price`, `exit_price` | Matches terminal within slippage tolerance |
| `stop_loss_initial` vs `stop_loss_final` | Different if breakeven fired; same otherwise |
| `realized_pnl` | Close to terminal's P&L (small variance from spread/commission) |
| `reason_open` | e.g. `m1_pullback_bounce` / `m1_rebound_rejection` |
| `reason_close` | `TP_CASH` or `HARD_SL` only in this version (see §9) |
| `m15_trend`, `m5_confirmation`, `m1_signal` | Non-`n/a`/non-empty, consistent with the trend at entry time |
| `daily_pnl_after_trade`, `consecutive_losses_after_trade`, `trades_today` | Running totals make sense against your own tally |
| `dry_run` | `True`/`False` matches `strategy.yaml`'s `dry_run` at the time |

- [ ] Confirm the file is **appended to, never overwritten** — restart the bot and confirm
      old rows are still present with a header written only once.

## 16. Retcode/error handling validation

- [ ] Every order attempt logs `order_send filling=<mode> retcode=<code> comment=<text>` —
      confirm `retcode=10009` (`TRADE_RETCODE_DONE`) on successful orders.
- [ ] If a fill is rejected, confirm the warning
      `order_send rejected with filling=<mode> (retcode=<code>) -- trying next filling mode`
      appears, and the adapter tries the next mode (`IOC → FOK → RETURN`, skipping whichever
      is already configured as primary in `mt5.yaml`).
- [ ] If **all** filling modes fail, confirm a clear `RuntimeError` is raised
      (`MT5Broker: <action> failed after trying all filling modes ...`) rather than a silent failure.
- [ ] Controlled resilience test: briefly disable the network adapter or pause the MT5
      terminal mid-run, and confirm:
  - The loop logs `run_iteration() raised an unexpected error (N/5 consecutive)` and **keeps running**.
  - After reconnecting within the 5-failure window, it recovers and resumes normally
    (`consecutive_errors` resets to 0 on the next successful call).
  - If the outage lasts through 5 consecutive failures, the process logs
    `5 consecutive errors -- stopping` and exits (`MAX_CONSECUTIVE_ITERATION_ERRORS = 5`
    in `cli.py`) rather than looping forever against a dead connection.

## 17. Shutdown checklist

- [ ] Press Ctrl+C during a run with no open position: confirm
      `Shutdown requested (signal 2). Finishing current iteration...` then a clean exit and
      `MT5 terminal disconnected`.
- [ ] Press Ctrl+C **while a position is open**: confirm the position is **not** force-closed
      by the shutdown — the hard SL already on the MT5 platform remains active and
      independent of the Python process.
- [ ] Restart the bot with the same position still open on the account: confirm
      `get_open_position()` finds it correctly via magic number and resumes managing it.
- [ ] ⚠️ Note: on restart, `state.open_trade_context` is lost (fresh in-memory `LoopState`) —
      if this reopened-and-still-managed position eventually closes, its journal row will
      show `m15_trend=n/a`, `m5_confirmation=n/a`, `m1_signal=n/a`, `reason_open=""` (the
      entry-time context wasn't carried over the restart). This is expected given the
      current in-memory-only state design — not a crash, just an audit-trail gap to know about.

## 18. What logs to capture

- [ ] Full console output, redirected to a per-session file:
  ```powershell
  python -m trend_only_scalper.cli mt5-demo ... 2>&1 | Tee-Object -FilePath logs\mt5_demo_session_YYYYMMDD_HHMM.log
  ```
- [ ] `logs/trend_only_scalper.log` (rotating, 5MB × 5 backups) — copy/archive it after
      each test phase since it rotates and old content can be lost.
- [ ] `logs/trade_journal_mt5.csv` — copy a timestamped snapshot after each milestone.
- [ ] Decision-log lines only, extracted for quick review:
  ```powershell
  Select-String -Path logs\trend_only_scalper.log -Pattern "trend_only_scalper.decision" > logs\decisions_only.log
  ```
- [ ] MT5 terminal's own **Journal** and **Experts** tabs (View → Toolbox) — the broker-side
      record of order placement, independent of our Python logging. Export or screenshot these.

## 19. What screenshots to capture

- [ ] Account balance/equity **before** starting each real-order test phase.
- [ ] Terminal login dialog / account properties showing the **demo** server name and account number.
- [ ] AutoTrading toggle in the "on" (green) state.
- [ ] Symbol specification dialog (volume_min/step/max, digits, trade_stops_level).
- [ ] Trade tab showing an open position (entry, SL, volume, symbol, comment/magic).
- [ ] History tab showing a closed trade (P&L, close time).
- [ ] Journal/Experts tab entries around each order's placement time.
- [ ] The bot's console output at the moment of each key event: open, breakeven modify, TP
      close, guard block, cooldown block.

## 20. Criteria to stop testing immediately

- [ ] **Any real order appears on a non-demo account** — stop, disconnect, investigate the
      account/login configuration before touching anything else.
- [ ] A position opens **without** a stop-loss attached in the MT5 terminal.
- [ ] More than one position open at once under the bot's magic number.
- [ ] The bot opens a position **against** the currently confirmed M15/M5 trend.
- [ ] The process exits via the 5-consecutive-error path (§16) — stop and root-cause before restarting.
- [ ] Any unhandled Python traceback that isn't the documented graceful paths above.
- [ ] The daily guard is breached (per journal/logs) and the bot **still** opens a new trade afterward.
- [ ] Slippage/deviation on a fill is wildly beyond the configured `deviation` points.
- [ ] Any doubt at all about whether the connected account is a demo account.

## 21. Criteria to proceed to the next demo phase

- [ ] Every checklist item in the current section passed, **repeated across multiple
      trades/iterations**, not just once.
- [ ] No unexplained errors or warnings beyond the documented/expected ones (§16, §17, §9).
- [ ] Every journal row cross-checked against the MT5 terminal matches within tolerance (§15).
- [ ] Cooldown and daily-guard behavior matched the configured values exactly, every time.
- [ ] Screenshots and logs for the phase have been archived (§18–19).
- [ ] A second person (if available) has reviewed the evidence before moving from
      minimum-lot demo trading (§7) toward any larger lot size or longer unattended run.

## 22. Known risks and how to monitor them

| Risk | Why it matters | How to monitor |
|---|---|---|
| `mt5.yaml`'s `lot` field is unused | Editing it does nothing; `strategy.yaml`'s `default_quantity` controls real order size | Re-check `default_quantity` before every real-order test, not `lot` |
| `DailyStats` is in-memory only | A process restart mid-day silently resets `trade_count`/`consecutive_losses`, weakening the daily guard | Avoid restarts during a live window; cross-check the journal CSV's daily totals if a restart happens |
| `CloseReason.BREAKEVEN_SL` is unreachable | Breakeven-protected closes are journaled as `HARD_SL`, not `BREAKEVEN_SL`; `cooldown_after_be_bars` never actually triggers | Don't expect `BREAKEVEN_SL` rows; this is expected, not a defect |
| No broker-side TP order | If the bot process/terminal is down while in profit, the cash TP won't fire — only the hard SL protects the account offline | Don't leave a position open unattended for extended periods without the bot running |
| `MT5Broker.connect()` doesn't verify demo vs. live | A misconfigured login/server could silently point at a real account | Manual account verification (§3) before every session, no exceptions |
| Independent `symbol` fields in `strategy.yaml` vs `mt5.yaml` | A mismatch trades the "wrong" (but still valid) symbol silently, no error | Confirm both files agree before every run (§5) |
| `MAX_CONSECUTIVE_ITERATION_ERRORS = 5` | The loop gives up and exits after 5 consecutive failures | Watch for repeated warning-level errors as an early signal before the hard stop |
| Filling-mode fallback (IOC→FOK→RETURN) | A broker accepting a later-tried mode with different fill semantics could behave unexpectedly | Check the `order_send filling=...` log line on every trade for the first several sessions |
