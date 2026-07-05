# Progress Note — trend_only_scalper

_(อัปเดตล่าสุด: 2026-07-04 — ใช้เพื่อ resume งานต่อในเซสชันถัดไป)_

## ทำอะไรเสร็จไปแล้วบ้างวันนี้

1. **Commit โปรเจกต์เข้า git ครั้งแรก** — โปรเจกต์ยังไม่เคยเป็น git repo มาก่อน วันนี้ `git init`
   แล้วแบ่ง commit เป็น 11 ก้อนตาม phase (`phase-01` ... `phase-10` + `docs`) พร้อมแก้
   `.gitignore` ให้ยกเว้น `data/sample_m1.csv` (fixture ที่ replay ต้องใช้ แต่เดิมจะโดน `data/`
   บล็อกทั้งโฟลเดอร์) ตรวจแล้วว่าไม่มี secret/API key/`.env` หลุดเข้า repo และ `pytest -q`
   ผ่านครบหลัง commit เสร็จ
2. **เขียนแผนทดสอบ MT5 demo** (`docs/mt5_demo_testing_plan.md`, 22 หัวข้อ) และ **แผนทดสอบ
   Binance Futures testnet** (`docs/binance_futures_testnet_testing_plan.md`, 24 หัวข้อ) —
   ทั้งสองไฟล์ตรวจสอบกับโค้ดจริงก่อนเขียน ไม่ใช่คำแนะนำทั่วไป ระหว่างทางพบ "กับดัก" สำคัญที่
   ไม่เคยบันทึกไว้มาก่อน: `mt5.yaml`'s `lot` และ `binance.yaml`'s `quantity` **ไม่ถูกใช้งานจริง
   เลย** — `strategy.yaml`'s `default_quantity` ต่างหากที่คุมขนาด order จริงในทุก broker
3. **ทำ senior code review รอบใหม่** (ครบ 15 มิติ ตามที่ user ขอ) — พบบั๊ก **ใหม่ ระดับ critical**
   ที่รอบก่อนไม่เคยเจอ: `position_manager._cash_to_price_distance()` (ใช้คำนวณราคาที่จะ modify
   stop-loss ตอน breakeven) สมมติว่า `quantity` เป็นหน่วยเชิงเส้น (contract_size=1) ซึ่งถูกสำหรับ
   Binance/MockBroker แต่ **ผิดสำหรับ MT5** (quantity เป็น lot) ทำให้ breakeven lock บน MT5
   คำนวณราคาผิดจริง — ไม่มี test ไหนจับได้เลยเพราะ MockBroker/SimulatedBroker ไม่เคยจำลอง
   lot-based contract size ต้องต่อ MT5 จริงถึงจะเจอ (ดู "ขั้นต่อไป" ด้านล่าง)
4. **สร้าง `CLAUDE.md`** ที่ root — วิเคราะห์โค้ดจริงก่อนเขียน (build/test commands, โครงสร้าง
   โฟลเดอร์, สถาปัตยกรรม broker-agnostic) และเพิ่ม section "Shared rules" ตามที่ user กำหนด
   ท้ายไฟล์ (สรุปเป็นไทย, ห้าม commit secret, ทำทีละ task เล็กๆ + รอ approval ก่อนแก้จริง,
   บันทึกสรุปทุก task ลง `decision-log.md`)
5. **สร้าง `decision-log.md`** และบันทึกสรุป milestone แรก (P0-1 ถึง P0-4 ครบ) เน้นเหตุผล/
   trade-off ของแต่ละ fix ตามกฎใหม่ใน `CLAUDE.md`

**ไม่มีการแก้โค้ด (`src/`) ในเซสชันนี้** — งานวันนี้เป็นเอกสาร/git history/review ล้วนๆ
`pytest -q` ยังคงอยู่ที่ **183 passed** เท่าเดิม

## ตอนนี้ค้างอยู่ตรงไหน

ไม่มีงานค้างกลางคันในเซสชันนี้ — ทุก task ที่เริ่มวันนี้ทำเสร็จแล้ว
(git commit, 2 testing plans, code review, CLAUDE.md, decision-log.md)

แต่ **การ re-review วันนี้เปลี่ยนลำดับความสำคัญของงานที่ค้างจากรอบก่อน**: บั๊ก breakeven/MT5
lot-size (ข้อ 3 ด้านบน) ควรถือเป็น **P0 ใหม่** (กระทบ breakeven lock ซึ่งเป็นกฎความปลอดภัยหลัก
ข้อหนึ่งโดยตรง บน MT5 จริง) ไม่ใช่แค่ P1 เหมือนที่เข้าใจไว้เดิม — ยังไม่ได้แก้

## ขั้นต่อไปที่ควรทำเมื่อกลับมา

ตามกฎใหม่ใน `CLAUDE.md` (ทำทีละ task เล็กๆ + เขียนแผนรอ approval ก่อนแก้จริง) งานที่ควรทำก่อน
คือ **แก้บั๊ก breakeven/lot-size ก่อนเป็นอันดับแรก** เพราะกระทบความปลอดภัยจริงบน MT5:

1. **[P0-ใหม่] แก้ `_cash_to_price_distance` ให้รองรับ contract size ที่ไม่ใช่ 1** — แนวทางที่คิดไว้:
   เพิ่ม method บน `Broker` ABC (เช่น `cash_per_price_unit(symbol, quantity)` หรือ
   `contract_size(symbol)`) คืนค่า `1.0` สำหรับ Mock/Sim/Binance และดึงจาก
   MT5 `symbol_info().trade_contract_size` จริงสำหรับ MT5 แล้วส่งต่อเข้า
   `position_manager.manage_position()` และ `main.py`'s `_price_from_pnl()` (ที่มีบั๊กเดียวกัน
   กระทบ journal's exit_price column) — ไม่ต้องแตะ strategy/indicator/risk-guard logic เลย
2. **[P0 เดิม, ยังไม่แก้] VWAP truncation bug** — `BAR_LOOKBACK=100` ตัด bar ก่อนคำนวณ VWAP
   กระทบ M5 confirmation filter โดยตรง (ต่างจริงได้ ~2.4%)
3. หลังจากนั้นค่อยไปที่ P1 เดิม: `MT5Config`/`BinanceConfig` ไม่มี validator, `safety-report`
   ไม่โชว์ `allow_live_trading`, dead config fields (`lot`, `quantity`, `max_cost_ratio_to_tp`)
4. รายละเอียดเต็มของ finding ทั้งหมด (B.1–B.3, C, D, E, F, G, H, I, J) อยู่ในเทิร์นที่ทำ
   senior review รอบใหม่ในบทสนทนานี้ — ค้นคำว่า "B.1" หรือ "Prioritized fix list" ถ้าต้องการ
   รายละเอียดเต็ม
5. ทำทีละ batch, เขียนแผนก่อนแล้วรอ approval (กฎใหม่จาก `CLAUDE.md`), run `pytest -q` ให้ผ่าน
   ทุกครั้ง, บันทึกสรุปลง `decision-log.md` หลังจบแต่ละ task, อย่า refactor ทั้งโปรเจกต์ในทีเดียว
