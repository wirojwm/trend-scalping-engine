# Progress Note — trend_only_scalper

_(บันทึกสถานะงาน ณ จุดที่โควต้าใกล้หมด — ใช้เพื่อ resume งานต่อในเซสชันถัดไป)_

## กำลังทำอะไรอยู่

โปรเจกต์ trend-only scalping bot (MT5 + Binance, MockBroker, backtest replay) สร้างเสร็จครบ
10 phases ด้วย loop engineering แล้ว จากนั้นได้ทำ **senior code review** ของทั้งโปรเจกต์
(สถาปัตยกรรม, broker abstraction, risk safety, ฯลฯ) และพบปัญหา critical หลายจุด
งานล่าสุดคือ **แก้ critical issues ตาม review** (ทำทีละ batch ตามคำสั่งผู้ใช้ "fix only the
highest-priority critical issues")

## ทำไปถึงขั้นไหนแล้ว

แก้ครบ **4 ใน 4 ของ P0 critical fixes แล้ว**:

| Fix | คำอธิบาย | สถานะ |
|---|---|---|
| P0-2 | Broker ปิด position เอง (hard SL server-side) แต่ bot ไม่รู้ตัว → ไม่บันทึก journal/cooldown/loss counter | ✅ แก้แล้ว |
| P0-3 | ไม่มีการ reset `DailyStats` ข้ามวันใน live/continuous loop → `max_trades_per_day` กลายเป็น lifetime cap | ✅ แก้แล้ว |
| P0-4 | ไม่มี exception handling ใน continuous loop → error เล็กน้อยทำให้ process ตายทั้งตัว | ✅ แก้แล้ว |
| P0-1 | Binance วาง entry order + stop order เป็น 2 ขั้นตอนแยกกัน ไม่มี rollback ถ้าขั้นที่ 2 fail → position ไม่มี SL ป้องกัน | ✅ **แก้แล้ว (batch ล่าสุด)** |

### ไฟล์ที่แก้ไปแล้ว (batch แรก: P0-2, P0-3, P0-4)

- `src/trend_only_scalper/models.py` — เพิ่ม `LoopState.last_known_position`
- `src/trend_only_scalper/main.py` — เพิ่ม `_maybe_reset_daily_stats()` (ใช้ timestamp ของ bar
  ล่าสุดแทน wall-clock) และ logic ตรวจจับ autonomous close ใน `run_iteration()`
- `src/trend_only_scalper/backtest/replay.py` — ลบ `_finalize_autonomous_close()` และ
  day-reset code ที่ซ้ำซ้อนออก (ตอนนี้ `run_iteration()` จัดการเองแบบ generic)
- `src/trend_only_scalper/cli.py` — เพิ่ม try/except + bounded retry
  (`MAX_CONSECUTIVE_ITERATION_ERRORS = 5`) ใน `_run_continuous_loop()`
- `tests/test_bot_loop.py` — เพิ่ม 3 tests (autonomous close, no double-count, day rollover)
- `tests/test_cli.py` — เพิ่ม 2 tests (recovers from transient errors, stops after too many)
- `tests/test_replay_backtest.py` — แก้ test ที่พังจากการลบ `_finalize_autonomous_close`
  และแก้ **bug เดิมที่ซ่อนอยู่**: test เดิมใช้ stop-loss ผิดด้าน (กลายเป็นกำไรแทนที่จะเป็นขาดทุน)
  เลยไม่ได้ทดสอบ `daily_max_loss` จริงๆ

### ไฟล์ที่แก้ไปแล้ว (batch ที่สอง: P0-1)

- `src/trend_only_scalper/brokers/binance_broker.py`:
  - `open_market_order()` — ถ้า `create_order("STOP_MARKET", ...)` fail หลัง entry fill แล้ว
    จะพยายาม compensating close (reduce-only market order ปิด position ทันที) แล้ว raise
    `RuntimeError` เสมอ (ไม่มีทาง return position ที่ไม่มี SL ป้องกันได้) ถ้า compensating close
    เองก็ fail อีก จะ log ระดับ `critical` เพื่อเตือนให้คนเข้าไปแก้เอง
  - `modify_stop_loss()` — สลับลำดับ: วาง stop ใหม่ก่อน แล้วค่อยยกเลิกอันเก่า (เดิมยกเลิกก่อน
    วางใหม่ ถ้าขั้นวางใหม่ fail จะไม่มี stop เหลือเลย) ถ้าวางใหม่ fail จะ raise แต่ stop เก่ายัง
    อยู่ครบ (log แจ้งว่า position ยังปลอดภัยด้วย stop เดิม)
- `tests/test_binance_broker_contract.py` — เพิ่ม 3 tests: entry-fill-then-stop-fails →
  compensating close ถูกสร้าง + raise เสมอ, modify-stop-fails → stop เก่าไม่ถูกยกเลิก,
  modify-stop-success → ยืนยันลำดับ create-ก่อน-cancel

**ผลทดสอบล่าสุด: `pytest -q` → 183 passed** (จาก 175 → 180 → 183)

รายละเอียดเต็มของ fix แต่ละอย่างอยู่ในข้อความสรุปของเทิร์นก่อนหน้าในบทสนทนานี้
(ค้นหาคำว่า "Files changed" ในประวัติแชท ถ้าต้องการรายละเอียด code-level)

## เหลืออะไรที่ยังไม่เสร็จ

จาก senior review (ดูรายละเอียดเต็มในเทิร์นที่ทำ review) — **P0 ทั้งหมดแก้ครบแล้ว** เหลือ:

**P1 (ควรแก้ก่อน demo run ยาวๆ):**
- **VWAP truncation bug**: `run_iteration()` ตัด bar เหลือแค่ `BAR_LOOKBACK=100` ก่อนคำนวณ
  indicator แต่ `add_vwap()` สะสมจากขอบเขตวันปฏิทิน (ต้องการ bar ทั้งวัน) ทำให้ VWAP ที่ใช้จริง
  ไม่ใช่ VWAP ของ session จริง (verify แล้วด้วยตัวเลขจริง: ต่างกันได้ถึง ~2.4%) กระทบ M5
  confirmation filter's `close > vwap` check โดยตรง — ยังไม่ได้แก้
- `safety-report` command แสดงไม่ได้ว่า `allow_live_trading` เป็น true/false เพราะโหลดแค่
  `strategy.yaml` ไม่โหลด `mt5.yaml`/`binance.yaml` — ยังไม่ได้แก้

**P2/P3 (nice-to-have, ไม่เร่งด่วน):** ดูรายละเอียดในเทิร์น review (MT5 commission ไม่รวมใน
unrealized PnL, TOCTOU บน one-position-only, config validation ของ MT5Config/BinanceConfig,
เป็นต้น)

## ขั้นต่อไปที่ควรทำ

P0 ครบแล้วทั้งหมด งานถัดไปคือ **P1 batch**:

1. **VWAP truncation bug** — น่าจะแก้ยากกว่า/เสี่ยงกว่า เพราะกระทบ `run_iteration()`'s
   `BAR_LOOKBACK` logic และ `add_vwap()` โดยตรง ต้องคิดวิธีแก้ที่ไม่กระทบ performance มากเกินไป
   (ทางเลือก: เพิ่ม `BAR_LOOKBACK` ให้ครอบคลุมทั้ง session, หรือคำนวณ VWAP แบบ persistent/
   incremental แยกจาก bar-window แทน) ต้องคิดดีๆ ก่อนแก้ เพราะมีผลกับ M5 confirmation filter
   โดยตรง (safety-critical logic)
2. **safety-report แสดง `allow_live_trading`** — ง่ายกว่า แก้แค่ `cmd_safety_report`/
   `print_safety_report` ให้รับ broker config เพิ่มเติม (optional) แล้วแสดงผล
3. ทำทีละ batch เหมือนเดิม, run `pytest -q` ให้ผ่านทุกครั้งก่อนถือว่าจบ, อย่า refactor
   ทั้งโปรเจกต์ในทีเดียว — สอดคล้องกับกฎที่ user ตั้งไว้ตลอดทั้งงานนี้
