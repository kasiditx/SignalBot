# RUN_COMMANDS.md

คู่มือคำสั่งรันระบบ Trading Bot แบบ Dry-run / Paper-Demo

> สถานะปัจจุบัน: ระบบนี้ยังเป็น Dry-run / Paper-Demo เท่านั้น  
> ไม่มีการส่งออเดอร์จริง  
> ไม่มีการเขียน MT5 order intent file  
> ยังไม่ควรใช้เงินจริง

---

## 1. เตรียม Environment ก่อนรัน

### Windows PowerShell

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
```

ใช้สำหรับบอก Python ให้มองเห็นโค้ดในโฟลเดอร์ `src`

### macOS / Linux

```bash
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
```

ใช้สำหรับบอก Python ให้มองเห็นโค้ดในโฟลเดอร์ `src`

---

## 2. รัน Dry-run Bot

คำสั่งนี้ใช้ทดสอบ pipeline แบบ paper/demo เท่านั้น  
ระบบจะวิเคราะห์ -> filter -> risk check -> journal  
แต่จะไม่ส่งออเดอร์จริง

### Windows PowerShell

```powershell
$env:PYTHONPATH="src"

$env:DRY_RUN_ACTION="BUY"
$env:DRY_RUN_ENTRY="2400.00"
$env:DRY_RUN_STOP_LOSS="2398.00"
$env:DRY_RUN_TP1="2403.00"
$env:DRY_RUN_TP2="2405.00"
$env:DRY_RUN_RISK_REWARD="1.5"
$env:DRY_RUN_CURRENT_PRICE="2400.00"
$env:DRY_RUN_SPREAD_POINTS="250"
$env:DRY_RUN_SESSION="London"

python -m trading_signal_bot.dry_run_main
```

### macOS / Linux

```bash
export PYTHONPATH=src

export DRY_RUN_ACTION=BUY
export DRY_RUN_ENTRY=2400.00
export DRY_RUN_STOP_LOSS=2398.00
export DRY_RUN_TP1=2403.00
export DRY_RUN_TP2=2405.00
export DRY_RUN_RISK_REWARD=1.5
export DRY_RUN_CURRENT_PRICE=2400.00
export DRY_RUN_SPREAD_POINTS=250
export DRY_RUN_SESSION=London

python -m trading_signal_bot.dry_run_main
```

---

## 3. ความหมายของ DRY_RUN_* แต่ละตัว

| Env | ความหมาย |
|---|---|
| `DRY_RUN_ACTION` | ฝั่งที่ต้องการทดสอบ เช่น `BUY`, `SELL` หรือปล่อยว่างเพื่อให้ระบบ reject แบบ no-trade |
| `DRY_RUN_ENTRY` | ราคาเข้าออเดอร์จำลอง |
| `DRY_RUN_STOP_LOSS` | จุด Stop Loss จำลอง |
| `DRY_RUN_TP1` | Take Profit 1 |
| `DRY_RUN_TP2` | Take Profit 2 |
| `DRY_RUN_RISK_REWARD` | ค่า RR เช่น `1.5` |
| `DRY_RUN_CURRENT_PRICE` | ราคาปัจจุบันที่ใช้ตรวจว่าไล่ราคาเกินไปหรือไม่ |
| `DRY_RUN_SPREAD_POINTS` | ค่า spread ปัจจุบัน |
| `DRY_RUN_ATR_VALUE` | ค่า ATR ปัจจุบัน ถ้ามี |
| `DRY_RUN_AVERAGE_ATR` | ค่า ATR เฉลี่ย ใช้ตรวจ volatility ผิดปกติ |
| `DRY_RUN_SESSION` | session เช่น `Asia`, `London`, `NewYork` |
| `DRY_RUN_HIGH_IMPACT_NEWS_NEARBY` | มีข่าวแรงใกล้ประกาศหรือไม่ เช่น `true` / `false` |
| `DRY_RUN_MODE` | ค่า default คือ `paper` ห้ามใช้ `live` |

---

## 4. ตัวอย่างรันแบบให้ระบบ Reject เพราะข้อมูลไม่ครบ

```powershell
$env:PYTHONPATH="src"
$env:DRY_RUN_ACTION=""
python -m trading_signal_bot.dry_run_main
```

ใช้ทดสอบว่า Bot จะไม่เดาเข้าออเดอร์เอง  
ถ้าไม่มี action / entry / SL / TP ระบบควร reject อย่างปลอดภัย

---

## 5. ผลลัพธ์ที่ควรเห็น

หลังรัน `dry_run_main` ต้องมีข้อความแนวนี้เสมอ:

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

ถ้าไม่มีข้อความนี้ ให้หยุดตรวจทันที

---

## 6. Journal Output อยู่ที่ไหน

ระบบจะเขียน audit journal ที่:

```text
logs/audit_journal.csv
logs/audit_journal.jsonl
```

CSV เหมาะสำหรับเปิดใน Excel:

```text
logs/audit_journal.csv
```

JSONL เหมาะสำหรับอ่านด้วยระบบหรือใช้ตรวจย้อนหลัง:

```text
logs/audit_journal.jsonl
```

---

## 7. รัน Test ทั้งหมด

### Windows PowerShell

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
python -m unittest discover -s tests
```

Expected result:

```text
Ran 190 tests
OK
```

ถ้าตัวเลข test เพิ่มในอนาคต จำนวนอาจมากกว่า 190 ได้ แต่ต้องจบด้วย `OK`

---

## 8. รัน Test ราย Phase

### Phase 3: Analysis Modules

```powershell
python -m unittest tests.test_market_structure tests.test_zone_detector tests.test_candle_confirmation tests.test_no_trade_filter
```

ใช้ทดสอบ:

- Market Structure
- Supply / Demand Zone
- Candle Confirmation
- No-trade Filter

### Phase 4: Risk Manager

```powershell
python -m unittest tests.test_risk_manager
```

ใช้ทดสอบ:

- RR ต่ำต้อง reject
- ไม่มี SL ต้อง reject
- consecutive loss ต้องหยุด
- lot size calculation

### Phase 5: Execution Policy

```powershell
python -m unittest tests.test_execution_policy
```

ใช้ทดสอบ:

- `candle_closed=False` ต้อง reject
- spread สูงต้อง reject
- fakeout ต้อง reject
- breakout ต้องมี body close

### Phase 6: Journal / Audit Trail

```powershell
python -m unittest tests.test_journal
```

ใช้ทดสอบ:

- CSV journal
- JSONL journal
- event type
- metadata serialize

### Phase 7: Dry-run Pipeline

```powershell
python -m unittest tests.test_dry_run_pipeline
```

ใช้ทดสอบ:

- analysis -> no_trade_filter -> execution_policy -> risk_manager -> journal
- approved path
- reject path
- ไม่สร้าง order intent

### Phase 8: Adapter + Dry-run Main

```powershell
python -m unittest tests.test_pipeline_adapter tests.test_dry_run_main
```

ใช้ทดสอบ:

- config mapping
- dry-run entry point
- ไม่มี auto_trade import
- ไม่มี order intent write

### Phase 9: Signal Poller Windows Lock

```powershell
python -m unittest tests.test_signal_poller
```

ใช้ทดสอบ:

- import บน Windows ไม่พัง
- ไม่มี fcntl error
- atomic lock ใช้งานได้
- lock ซ้ำไม่รัน poller ซ้ำ

---

## 9. เช็ก Windows Compatibility

```powershell
$env:PYTHONPATH="src"
python -c "import trading_signal_bot.signal_poller; print('ok')"
```

Expected:

```text
ok
```

ถ้าขึ้น error `No module named fcntl` แปลว่ายังมีโค้ดเก่าหลงอยู่

---

## 10. เช็กว่าไม่มี auto_trade ถูกเรียกใน Dry-run

### Windows PowerShell

```powershell
Select-String -Path src\trading_signal_bot\dry_run_main.py -Pattern "auto_trade|process_auto_trade|order_file"
Select-String -Path src\trading_signal_bot\pipeline_adapter.py -Pattern "auto_trade|process_auto_trade|order_file"
```

Expected:

```text
ไม่ควรเจอผลลัพธ์ที่เป็นการ import หรือเรียกใช้งาน auto_trade
```

---

## 11. เช็กว่าไม่มี MT5 Order Intent File ถูกสร้าง

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

Expected:

```text
False
False
```

ถ้าได้ `True` ให้หยุดทันที เพราะ dry-run ไม่ควรสร้างไฟล์ order intent

---

## 12. เช็ก Journal หลังรัน

```powershell
Get-Content .\logs\audit_journal.jsonl -Tail 10
```

ใช้ดู event ล่าสุด เช่น:

- `SIGNAL_GENERATED`
- `NO_TRADE`
- `EXECUTION_POLICY_REJECT`
- `RISK_MANAGER_REJECT`
- `EXECUTION_PLAN_APPROVED`
- `PAPER_ORDER_INTENT`
- `ERROR`

---

## 13. คำสั่งล้าง Journal ทดสอบ

ใช้เมื่อต้องการเริ่ม test ใหม่:

```powershell
Remove-Item .\logs\audit_journal.csv -ErrorAction SilentlyContinue
Remove-Item .\logs\audit_journal.jsonl -ErrorAction SilentlyContinue
```

ห้ามลบไฟล์ order หรือไฟล์ MT5 ถ้ายังไม่แน่ใจว่าเป็นไฟล์อะไร

---

## 14. สถานะที่ถือว่าปลอดภัย

ก่อนรันทุกครั้งควรเช็กว่า output มี:

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

และเช็กว่า:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

ต้องได้:

```text
False
False
```

---

## 15. สิ่งที่ยังไม่พร้อมสำหรับเงินจริง

ระบบยังไม่พร้อมเงินจริง เพราะยังไม่มี:

- Broker reconciliation
- Real position sync
- Order lifecycle tracking
- Partial fill / rejected order handling
- Live spread/news feed ที่เชื่อถือได้
- Persistent trade state
- Forward test demo อย่างน้อย 2-4 สัปดาห์
- Backtest 3-6 เดือนแยก session
- Manual approval process ก่อนเปิด execution จริง

---

## 16. สรุปคำสั่งที่ใช้บ่อย

### รัน Dry-run

```powershell
$env:PYTHONPATH="src"
python -m trading_signal_bot.dry_run_main
```

### รัน Test ทั้งหมด

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

### เช็ก signal_poller import

```powershell
$env:PYTHONPATH="src"
python -c "import trading_signal_bot.signal_poller; print('ok')"
```

### เช็กว่าไม่มี order intent

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

### ดู Journal ล่าสุด

```powershell
Get-Content .\logs\audit_journal.jsonl -Tail 10
```

---

## 17. คำเตือนสำคัญ

ระบบนี้ยังเป็น Dry-run / Paper-Demo เท่านั้น

- ห้ามใช้เงินจริง
- ห้ามเปิด Auto Trade จริง
- ห้ามเชื่อว่า test ผ่านแล้วแปลว่าพร้อม Live
- ต้อง Demo Forward Test ก่อนอย่างน้อย 2-4 สัปดาห์
- ต้องมี Risk Limit และ Manual Review ก่อนทุกครั้ง

