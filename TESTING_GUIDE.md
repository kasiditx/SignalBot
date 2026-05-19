# TESTING_GUIDE.md

คู่มือทดสอบระบบ Trading Bot แบบ Dry-run / Paper-Demo

> Test ผ่านไม่ได้แปลว่าพร้อมเงินจริง  
> ระบบยังเป็น dry-run / paper-demo  
> No order was sent.  
> No MT5 order intent was written.  
> Dry-run only.

---

## 1. Overview

เอกสารนี้ใช้เป็นคู่มือรัน test และตรวจ safety guard ของระบบ Trading Bot หลัง Phase 2-9

เป้าหมายของการทดสอบ:

- ยืนยันว่า analysis modules ทำงานแบบ deterministic
- ยืนยันว่า risk manager reject เงื่อนไขเสี่ยง
- ยืนยันว่า execution policy reject หน้างานที่ไม่ปลอดภัย
- ยืนยันว่า journal เขียน audit trail ได้
- ยืนยันว่า dry-run pipeline ไม่ส่ง order จริง
- ยืนยันว่า Windows import ของ `signal_poller.py` ไม่พังจาก `fcntl`
- ยืนยันว่าไม่มี MT5 order intent file ถูกสร้างจาก dry-run

---

## 2. Test Runtime Setup

### Windows PowerShell

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
```

### macOS / Linux

```bash
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
```

`PYTHONPATH=src` ทำให้ Python import package `trading_signal_bot` จาก source tree ได้โดยตรง

---

## 3. Run All Tests

### Windows PowerShell

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
python -m unittest discover -s tests
```

Expected:

```text
Ran 190 tests
OK
```

หมายเหตุ: ถ้าจำนวน test เพิ่มขึ้นในอนาคต จำนวน `Ran` อาจมากกว่า 190 ได้ แต่ต้องจบด้วย `OK`

---

## 4. Run Phase-Specific Tests

ใช้คำสั่งราย phase เมื่อต้องการ debug เฉพาะส่วน โดยไม่ต้องรันทั้ง suite

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
```

จากนั้นเลือกคำสั่งในหัวข้อย่อยด้านล่าง

---

## 5. Phase 3 Analysis Tests

```powershell
python -m unittest tests.test_market_structure tests.test_zone_detector tests.test_candle_confirmation tests.test_no_trade_filter
```

ครอบคลุม:

- Market structure: HH/HL, LH/LL, Sideway, BOS, CHOCH
- Supply / Demand zone detection
- Support / resistance และ price location
- Candle confirmation เช่น engulfing, pin bar, strong close, fakeout
- No-trade filter เช่น mid-zone, RR ต่ำ, no confirmation, TF conflict

Expected:

```text
OK
```

---

## 6. Phase 4 Risk Manager Tests

```powershell
python -m unittest tests.test_risk_manager
```

ครอบคลุม:

- Reject `mode=live`
- Reject เมื่อไม่มี entry / SL / TP / RR
- Reject เมื่อ RR ต่ำกว่า `1.5`
- Reject เมื่อ risk percent เกิน limit
- Reject เมื่อ max daily loss / max trades / consecutive losses ถึง limit
- Reject same-direction stacking
- Position size calculation
- No martingale / no multiplier
- State update หลัง win/loss/cooldown

Expected:

```text
OK
```

---

## 7. Phase 5 Execution Policy Tests

```powershell
python -m unittest tests.test_execution_policy
```

ครอบคลุม:

- Reject `mode=live`
- Reject เมื่อ entry / SL / TP1 / TP2 หาย
- Reject เมื่อ candle ยังไม่ปิด
- Reject เมื่อราคาอยู่ `MID_ZONE`
- Reject เมื่อไล่ราคาเกิน threshold
- Reject เมื่อ spread สูง
- Reject เมื่อ session ไม่อนุญาต
- Reject เมื่อ news filter เปิดและมีข่าวแรงใกล้ประกาศ
- Reject เมื่อ ATR สูงผิดปกติ
- Reject fakeout
- Reject breakout ที่ไม่มี body close
- Approved case สร้าง execution plan ได้

Expected:

```text
OK
```

---

## 8. Phase 6 Journal Tests

```powershell
python -m unittest tests.test_journal
```

ครอบคลุม:

- สร้าง `JournalEvent`
- Validate event type
- Serialize เป็น dict
- เขียน CSV พร้อม header
- เขียน JSONL หนึ่ง event ต่อหนึ่งบรรทัด
- Append หลาย event
- Serialize `reasons` และ `metadata`
- Error handling เมื่อ path หรือ metadata ผิด

Expected:

```text
OK
```

---

## 9. Phase 7 Dry-run Pipeline Tests

```powershell
python -m unittest tests.test_dry_run_pipeline
```

ครอบคลุม:

- Reject `mode=live`
- Reject เมื่อไม่มี M1 candles
- `action=None` จบที่ no-trade
- MID_ZONE reject
- Spread สูง reject
- Candle ยังไม่ปิด reject
- Fakeout reject
- RR ต่ำ reject
- Risk manager reject เมื่อ consecutive losses หรือ same-direction stacking
- Approved path มี execution plan และ risk decision
- Journal มี `EXECUTION_PLAN_APPROVED`
- Journal มี `PAPER_ORDER_INTENT`
- Metadata ต้องมี `order_sent=false`
- Metadata ต้องมี `order_intent_written=false`
- ไม่สร้าง MT5 order intent file จริง

Expected:

```text
OK
```

---

## 10. Phase 8 Adapter / dry_run_main Tests

```powershell
python -m unittest tests.test_pipeline_adapter tests.test_dry_run_main
```

ครอบคลุม:

- Config mapping จาก `SignalConfig`
- Execution policy limits mapping
- Risk limits mapping
- Position sizing input mapping
- Journal config default
- `run_pipeline_from_configs()`
- `dry_run_main` env parsing
- `main()` return code
- ไม่มี import/call `auto_trade.py`
- ไม่มี `process_auto_trade`
- ไม่มี `order_file`
- ไม่สร้าง order intent file จริง

Expected:

```text
OK
```

---

## 11. Phase 9 Signal Poller Windows Lock Tests

```powershell
python -m unittest tests.test_signal_poller
```

ครอบคลุม:

- Import `signal_poller.py` บน Windows ไม่พัง
- ไม่มี `fcntl` error
- ตรวจ stale candle behavior เดิม
- ยืนยัน compatibility ของ module ที่เคยพังตอน `unittest discover`

Expected:

```text
OK
```

---

## 12. Windows Compatibility Check

ใช้ตรวจว่า `signal_poller.py` import ได้บน Windows:

```powershell
$env:PYTHONPATH="src"
python -c "import trading_signal_bot.signal_poller; print('ok')"
```

Expected:

```text
ok
```

ถ้าเจอ:

```text
No module named fcntl
```

แปลว่ายังมี `fcntl` import แบบเก่าหลงอยู่ ต้องหยุดและตรวจ `signal_poller.py`

---

## 13. Safety Source Checks

ตรวจว่า dry-run entry point ไม่ import หรือเรียก auto trade:

```powershell
Select-String -Path src\trading_signal_bot\dry_run_main.py -Pattern "auto_trade|process_auto_trade|order_file"
Select-String -Path src\trading_signal_bot\pipeline_adapter.py -Pattern "auto_trade|process_auto_trade|order_file"
```

Expected:

```text
ไม่ควรเจอผลลัพธ์ที่เป็น import หรือ call ของ auto_trade / process_auto_trade / order_file
```

หมายเหตุ: ถ้าเจอคำใน comment หรือ string ให้ตรวจ context ด้วย แต่ห้ามมี execution path ที่ส่ง order จริง

---

## 14. Order Intent File Check

ตรวจว่า dry-run ไม่สร้าง MT5 order intent file:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

Expected:

```text
False
False
```

ถ้าได้ `True`:

1. หยุดทดสอบทันที
2. ตรวจว่ารัน entry point ถูกต้องหรือไม่
3. ตรวจว่าไม่ได้รัน `main.py` หรือ `auto_trade.py`
4. ตรวจ journal และ console output
5. อย่าลบไฟล์ production ใด ๆ จนกว่าจะมั่นใจว่าเป็น test artifact

---

## 15. Journal Check

ดู audit journal ล่าสุด:

```powershell
Get-Content .\logs\audit_journal.jsonl -Tail 10
```

event ที่คาดว่าจะพบ:

- `SIGNAL_GENERATED`
- `NO_TRADE`
- `EXECUTION_POLICY_REJECT`
- `RISK_MANAGER_REJECT`
- `EXECUTION_PLAN_APPROVED`
- `PAPER_ORDER_INTENT`
- `ERROR`

ถ้าเจอ `PAPER_ORDER_INTENT` ต้องตรวจ metadata:

```json
{
  "order_sent": false,
  "order_intent_written": false
}
```

ถ้า metadata ไม่ชัดเจน ให้หยุดและตรวจ code path ก่อนรันต่อ

---

## 16. Expected Result

ผลรวมปัจจุบันที่คาดหวัง:

```text
Ran 190 tests
OK
```

ถ้า tests เพิ่มในอนาคต:

- จำนวน `Ran` อาจมากกว่า 190
- ต้องไม่มี failure
- ต้องไม่มี error
- ต้องจบด้วย `OK`

---

## 17. Troubleshooting Common Failures

### `ModuleNotFoundError: No module named trading_signal_bot`

ตั้งค่า `PYTHONPATH`:

```powershell
$env:PYTHONPATH="src"
```

### `No module named fcntl`

แปลว่า Windows compatibility fix ของ `signal_poller.py` ไม่ครบ หรือมี code path เก่า:

```powershell
Select-String -Path src\trading_signal_bot\signal_poller.py -Pattern "fcntl"
```

### Test สร้างไฟล์ที่ไม่คาดคิด

ตรวจ order intent:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

ถ้าได้ `True` ให้หยุดทันที

### Journal test fail

ตรวจ permission และ folder:

```powershell
Test-Path .\logs
Get-ChildItem .\logs
```

### Dry-run reject

reject ไม่ได้แปลว่า bug เสมอไป ให้ตรวจ `stage` และ `reasons`:

- `adapter_preflight`: env ไม่ครบ
- `market_data`: candle data ไม่ครบ
- `no_trade_filter`: structure/zone/candle/RR ไม่ผ่าน
- `execution_policy`: spread/session/news/ATR/candle close ไม่ผ่าน
- `risk_manager`: risk limit ไม่ผ่าน

---

## 18. Final Safety Reminder

ระบบนี้ยังเป็น dry-run / paper-demo เท่านั้น

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

Test ผ่านไม่ได้แปลว่าพร้อมเงินจริง

ก่อนพิจารณาขั้นถัดไป ต้องมี:

- Backtest อย่างน้อย 3-6 เดือน
- Demo forward test อย่างน้อย 2-4 สัปดาห์
- Journal review
- Risk review
- Broker reconciliation
- Real position sync
- Order lifecycle tracking
- Manual approval process

ห้ามใช้เงินจริงจนกว่าหลักฐานเหล่านี้ครบและผ่านการตรวจอย่างเป็นระบบ

