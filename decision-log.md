# Decision Log

_(บันทึกสรุปงานแต่ละ task — ไฟล์นี้จะถูกคัดลอกไปยัง My-LLM-Wiki ภายหลัง)_

---

## 2026-07-04 — P0 milestone complete (P0-1 to P0-4)

**สิ่งที่ทำ (สรุปรวม 4 items):** แก้ critical issues ทั้งหมดจาก senior code review ครบทั้ง 4 จุด:
ตรวจจับการปิด position ที่ broker ทำเองฝั่งเดียว (hard SL server-side) ที่ bot ไม่เคยรู้ตัวมาก่อน,
reset สถิติรายวันข้ามวันให้ถูกต้องทั้งใน live loop และ backtest, ป้องกัน process ตายทั้งตัวจาก error
ชั่วคราวใน continuous loop, และปิดช่องโหว่ที่ Binance อาจเปิด position โดยไม่มี stop-loss ป้องกัน
ถ้าขั้นตอนวาง order ที่สองล้มเหลว (รายละเอียด technical เต็มอยู่ใน progress-note.md แล้ว)

**เหตุผลที่เลือกวิธีนี้ (การชั่งน้ำหนัก/trade-off):**

- **Autonomous close detection** — เลือกทำ detection แบบ generic ไว้ที่เดียวใน `run_iteration()`
  (ผ่าน `last_known_position`) แทนที่จะเขียนซ้ำในแต่ละ broker adapter หรือใน backtest replay
  แยกต่างหาก เพราะจะได้ broker-agnostic จริง ใช้ได้ทั้ง MT5/Binance/backtest โดยไม่ต้องดูแลโค้ด
  ซ้ำซ้อนหลายที่ — แลกมาด้วยต้อง return ทันทีหลัง detect (ไม่ tick cooldown ซ้ำ) เพื่อให้พฤติกรรม
  cooldown เหมือนกับ close ที่ bot ตัดสินใจเองทุกประการ
- **Daily stats reset** — เลือกอิง timestamp ของ bar ล่าสุดแทน wall-clock เพราะต้องใช้ mechanism
  เดียวกันได้ทั้ง live trading (bar ล่าสุด = "ตอนนี้") และ backtest replay (bar ล่าสุด = จุดใดจุดหนึ่ง
  ในอดีตที่กำลัง replay อยู่) ถ้าใช้ wall-clock จะพังทันทีตอน backtest
- **Exception handling ใน continuous loop** — เลือก bounded retry (fail 5 ครั้งติดต่อกันแล้วค่อยหยุด)
  แทนที่จะไม่ดักเลย (process ตายจาก error เล็กน้อย) หรือ retry ไม่จำกัด (วนซ้ำเงียบๆ ตลอดไปเมื่อ
  connection พังจริง) เป็นจุดสมดุลระหว่างทนต่อ error ชั่วคราวกับไม่ปิดบัง failure ที่เกิดขึ้นถาวร
- **Binance order/stop atomicity** — เลือก "compensating close แล้ว raise เสมอ" เมื่อวาง stop
  ไม่สำเร็จหลัง entry fill แล้ว (ไม่ปล่อยให้ position ไม่มี SL ป้องกันอยู่เงียบๆ) และเปลี่ยนลำดับ
  `modify_stop_loss` เป็น "วางใหม่ก่อน ค่อยยกเลิกอันเก่า" (จากเดิมยกเลิกก่อน) เพราะยึดหลักว่า
  position ต้องมี stop-loss ป้องกันอยู่เสมอทุก state — ยอมให้ error ดังกว่าเงียบ

หลักการรวมที่ใช้ตัดสินใจทุกจุด: แก้เฉพาะจุดวิกฤต ไม่ refactor สถาปัตยกรรมเดิม ไม่แตะ
broker-agnostic strategy logic และคงกฎความปลอดภัยทั้งหมด (one-position-only, no counter-trend,
no grid/martingale/averaging-down, hard SL, cash TP, breakeven lock, daily guard, cooldown) ไว้ครบ

**ไฟล์ที่แก้:** `models.py`, `main.py`, `backtest/replay.py`, `cli.py`,
`brokers/binance_broker.py` และ test ที่เกี่ยวข้อง (`test_bot_loop.py`, `test_cli.py`,
`test_replay_backtest.py`, `test_binance_broker_contract.py`)

**ผลทดสอบ:** `pytest -q` → **183 passed**, ไม่มี regression

---

## 2026-07-12 — แก้ test default symbol ให้ตรงกับ config ที่เตรียม Binance testnet

**สิ่งที่ทำ:** `config/strategy.yaml` ถูกเปลี่ยน symbol เป็น `BTC/USDT` ตั้งแต่ commit `d83594e`
(เตรียม Binance testnet) แต่ `tests/test_config.py::test_load_strategy_config_defaults` ยังคง
assert ค่าเดิม `EURUSD` อยู่ ทำให้ test แดง — เป็นแค่ test ที่ตกยุคตาม config ไม่ใช่ runtime defect
แก้เฉพาะบรรทัด assertion ให้ตรงกับ symbol ปัจจุบัน ไม่แตะ production logic ใดๆ

**ผลทดสอบ:** `python -m compileall src tests scripts` ผ่าน, `pytest -q` → **211 passed**

**Commit:** `e9e8243` — "test update strategy default symbol for binance prep" (pushed to
`origin/master`)

---

## 2026-07-12 — เตรียม Binance testnet §9 + พบ secret leak ใน pytest repr + แก้ด้วย SecretStr

**สิ่งที่ทำ:** ระหว่างเตรียม real-order testing ตาม `docs/binance_futures_testnet_testing_plan.md`
§9 (ยืนยัน `BINANCE_API_KEY`/`BINANCE_API_SECRET` มีอยู่ใน `.env`, ตั้ง `allow_live_trading: true`
ชั่วคราวเพื่อทดสอบ) พบว่า test แดง 3 ตัวไม่ใช่ test ตกยุค แต่เป็น **safety guardrail ที่ตั้งใจไว้**
คอย detect ไม่ให้ config ที่ commit เข้า repo แอบเปิด live trading โดยไม่ตั้งใจ — จึง revert
`config/binance.yaml` กลับเป็น `allow_live_trading: false` ทันทีโดยไม่ commit ค่า `true` เลย

ระหว่างรัน `pytest -q` เพื่อตรวจสอบ ผล assertion error ของ test เหล่านั้นดัน print
`repr(BinanceConfig(...))` เต็มๆ ออกมาใน terminal ซึ่งมี `api_key`/`api_secret` เป็น plaintext
อยู่ในนั้น (สาเหตุ: `Field(exclude=True)` ใน Pydantic v2 กันไม่ให้ค่าหลุดตอน serialize เท่านั้น
ไม่ได้กันตอน `repr()`) — ทำให้ secret หลุดเข้ามาอยู่ใน conversation transcript ของ session นี้

ตรวจสอบตามคำขอ user แบบ read-only ทั้ง 5 ข้อ (ไม่แก้โค้ด ไม่ commit ไม่ push ไม่ส่ง order):
`.env` อยู่ใน `.gitignore` และไม่เคยถูก track, key/secret โหลดผ่าน loader ปกติ (boolean check
เท่านั้น), ค้นหา fragment ของ key/secret ทั้งใน `git grep` บน working tree, `git rev-list --all`
ทั้ง history, และไฟล์ทุกไฟล์ใน repo tree (นอก `.git`/`.venv`) — เจอแค่ใน `.env` เอง (ที่ควรอยู่)
ไม่มีที่อื่นเลย, ทดสอบ authenticated `fetch_balance()` แบบ read-only ผ่าน (key ใช้งานได้จริงบน
testnet), และ `allow_live_trading` ยังเป็น `false` ตลอด — **สรุปว่าไม่มีหลักฐานว่า key หลุดไปที่ไหน
นอกเหนือจาก transcript ของ session นี้ จึงไม่ rotate key ตามที่ user สั่ง**

แก้ต้นเหตุของการหลุดโดยเปลี่ยน `BinanceConfig.api_key`/`api_secret` จาก `str | None` เป็น
`SecretStr | None` (Pydantic) — ทำให้ `repr()`/`str()`/log ใดๆ เห็นแค่ `SecretStr('**********')`
เสมอ ต้องเรียก `.get_secret_value()` เท่านั้นถึงจะได้ plaintext แก้จุดเดียวที่ใช้ค่านี้จริงคือ
`binance_broker.py`'s `_build_exchange()` ให้เรียก `.get_secret_value()` ตอนส่งให้ ccxt

**เหตุผลที่เลือกวิธีนี้:** ไม่แก้ 3 test ที่แดงให้ผ่านง่ายๆ เพราะมันคือ guardrail ที่ตั้งใจไว้ตาม
`senior_review_action_plan.md` ไม่ใช่ test ตกยุคแบบ symbol — การแก้ test ให้ผ่านจะทำลาย safety net
นั้นทิ้ง เลือก revert ค่า config แทน ส่วนเรื่อง secret leak เลือก `SecretStr` เพราะแก้ที่ต้นเหตุ
(repr) ทีเดียว ครอบคลุมทุกจุดที่อาจ print/log object นี้ในอนาคต โดยไม่ต้องแก้โค้ดที่เรียกใช้ทุกจุด

**ไฟล์ที่แก้:** `config.py`, `brokers/binance_broker.py` (ไม่แตะ `config/binance.yaml` —
revert กลับสถานะเดิมแล้ว ไม่ commit)

**ผลทดสอบ:** `python -m compileall src tests scripts` ผ่าน, `pytest -q` → **211 passed**
(กลับมาผ่านครบหลัง revert `allow_live_trading`), ยืนยัน `repr()` มาสก์ค่าแล้วแต่
`.get_secret_value()` ยังใช้ได้จริง และ `fetch_balance()` ยังทำงานถูกต้องหลังแก้

**Commit:** `12da84a` — "fix binance config: mask api_key/api_secret with SecretStr to prevent
repr leaks" (pushed to `origin/master`)

---

## 2026-07-18 — §9 minimum-quantity testnet order test: INCONCLUSIVE (no entry signal)

**สิ่งที่ทำ:** ทำ §9 ตาม `docs/binance_futures_testnet_testing_plan.md` แบบ bounded live-test
ครบทุกขั้นตอนความปลอดภัยตามแผน — Phase 0 ตรวจ checkpoint สะอาด (working tree clean, `.env`
ถูก ignore, `allow_live_trading: false`, `testnet: true`, zero positions/orders), Phase 1
ตรวจ auth/endpoint/market metadata แบบ read-only เท่านั้น (ไม่วาง order ใดๆ), Phase 2 เสนอ diff
config ชั่วคราวให้ user อนุมัติก่อน, Phase 3 รัน bounded window 45 นาที (`--iterations 90
--loop-interval 30`) บน Binance Futures **testnet**, Phase 4 revert config ทันทีหลังจบ

ระหว่างคำนวณ quantity ขั้นต่ำที่ผ่านทั้ง `min_qty` และ `min_notional` พบ bug เล็กน้อยในสคริปต์
ตรวจสอบของตัวเอง — คำนวณด้วย float ธรรมดาได้ `0.0007` ซึ่งจริง ๆ แล้ว notional ต่ำกว่า
`min_notional` (44.80 < 50) ยังไม่ผ่านเกณฑ์ แก้ด้วย Decimal/tick-exact arithmetic ได้ค่าที่ถูกต้อง
คือ `0.0008` (notional ≈ 51.19) ก่อนนำไปใช้จริงในการทดสอบ

รันไป 70/90 iterations (~36 นาที) แล้ว process ถูก kill จากภายนอก (ไม่ใช่ exception/crash — ไม่มี
traceback ท้าย log) แต่ตลอดการรันไม่มี M1 entry signal เกิดขึ้นเลยสักครั้ง (`action=no_trade` ทุก
iteration, ส่วนใหญ่เพราะ M15/M5 trend ไม่ตรงกันหรือเป็น NONE) — จึงไม่มี order ใดถูกส่งไป exchange
เลยตลอดการทดสอบ ตรวจสอบ account หลังจบยืนยัน zero positions, zero open orders, balance
USDT ไม่เปลี่ยนแปลง (5000.0 คงเดิม)

ตามเกณฑ์ที่ user กำหนดไว้ล่วงหน้า ("ถ้าไม่มี entry signal เกิดขึ้นในช่วงเวลาที่กำหนด ให้ถือว่า §9
เป็น INCONCLUSIVE แล้วหยุดอย่างปลอดภัย") — สรุปผล §9 = **INCONCLUSIVE** (ไม่ใช่ PASS หรือ FAIL
เพราะไม่มีโอกาสได้ทดสอบ entry/stop-loss/close path จริงเลย)

**เหตุผลที่เลือกวิธีนี้:** ไม่ retry หรือขยายหน้าต่างเวลาเองโดยไม่ถาม user เพราะกฎ safety ระบุชัดว่า
"no automatic retry, no second trade" และ INCONCLUSIVE ต้อง "stop safely" ทันที ไม่ใช่พยายามอีกรอบ
ส่วนเรื่อง float bug ในสคริปต์ตรวจสอบ — เลือกแก้และรายงานให้ user เห็นความคลาดเคลื่อนตรง ๆ
แทนที่จะปิดบัง เพราะถ้าใช้ค่าที่ผิด (`0.0007`) จริงในการเทรดจะทำให้ order ถูก exchange reject

**Config หลังจบ:** revert กลับสถานะเดิมครบ — `allow_live_trading: false`,
`default_quantity: 0.01`, `daily_max_loss: -30.0`, `max_consecutive_losses: 3`,
`max_trades_per_day: 150` (ยืนยันด้วย `git diff` ว่างเปล่าทั้ง 2 ไฟล์) ไม่มีการ commit ค่า
ชั่วคราวใด ๆ เข้า repo

**ผลทดสอบ:** `python -m compileall src tests scripts` ผ่าน, `pytest -q` → **211 passed**

**§10–§16:** ยังไม่เริ่ม เพราะ §9 ไม่ใช่ PASS — ต้องรอ user ตัดสินใจว่าจะลอง §9 ใหม่อีกครั้ง
(ขยายเวลา/เปลี่ยนช่วงเวลาตลาด) หรือดำเนินการอย่างไรต่อ

**Commit:** ยังไม่ commit — รอ user อนุมัติก่อน (ตามกฎ "wait for approval before commit/push")
