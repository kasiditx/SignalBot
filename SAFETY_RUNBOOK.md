# SAFETY_RUNBOOK.md

คู่มือความปลอดภัยสำหรับ Trading Bot แบบ Dry-run / Paper-Demo

> No order was sent.  
> No MT5 order intent was written.  
> Dry-run only.  
> ห้ามใช้เงินจริงในสถานะนี้

---

## 1. Hard Rule: ห้ามใช้เงินจริงในสถานะนี้

ระบบปัจจุบันยังอยู่ในสถานะ research / paper-demo / dry-run เท่านั้น

กฎหลัก:

- ห้ามใช้เงินจริง
- ห้ามเปิด Auto Trade จริง
- ห้ามส่ง order จริง
- ห้ามเขียน MT5 order intent file
- ห้าม Martingale
- ห้ามเพิ่ม lot เพื่อเอาคืน
- ห้ามถือว่า test ผ่านแล้วแปลว่าพร้อม Live

ถ้ามีข้อสงสัยว่า flow ใดอาจส่ง order ได้ ให้หยุดก่อนและตรวจ code path ทันที

---

## 2. Current Deployment Status

สถานะที่อนุญาต:

- Paper dry-run
- Demo dry-run
- Journal / audit trail review
- Unit test / integration test
- Manual review ของ reject reason และ approved paper decision

สถานะที่ยังไม่อนุญาต:

- Live execution
- MT5 order intent writer
- Fully automated order placement
- Broker-connected execution
- Real-money deployment

ระบบ dry-run มีไว้เพื่อดูว่า candidate ถูก reject หรือผ่านเพราะอะไร ไม่ใช่เพื่อส่งคำสั่งซื้อขายจริง

---

## 3. Before Every Run Checklist

ก่อนรันทุกครั้งให้เช็ก:

- [ ] ใช้บัญชี paper/demo เท่านั้น
- [ ] ไม่ได้ตั้ง `DRY_RUN_MODE=live`
- [ ] summary ต้องมี `No order was sent.`
- [ ] summary ต้องมี `No MT5 order intent was written.`
- [ ] summary ต้องมี `Dry-run only.`
- [ ] ตรวจว่าไม่มี `trading_signal_order.csv`
- [ ] ตรวจว่าไม่มี `logs/trading_signal_order.csv`
- [ ] ตรวจ journal path ว่าชี้ไปที่ audit journal เท่านั้น
- [ ] รัน tests ผ่านล่าสุด
- [ ] ตรวจว่า RR ไม่ต่ำกว่า `1.5`
- [ ] ตรวจว่า risk ต่อไม้ไม่เกิน `1%`
- [ ] ตรวจว่า max daily loss ไม่เกิน `3%`
- [ ] ไม่มีข่าวแรงที่ยังไม่ได้พิจารณา
- [ ] ไม่ฝืนเทรดถ้าราคาอยู่กลางโซนหรือ structure ไม่ชัด

คำสั่งตรวจ order intent:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

Expected:

```text
False
False
```

---

## 4. Before Demo Auto Trade Checklist

ก่อนคิดจะเปิด demo auto trade ใน Phase ถัดไป ต้องผ่านอย่างน้อย:

- [ ] Dry-run pipeline ผ่าน tests ทั้งหมด
- [ ] Full test discover ผ่าน
- [ ] Journal บันทึก entry / reject reason / risk status ครบ
- [ ] ไม่มี path ใดใน dry-run ที่ส่ง order จริง
- [ ] ไม่มี MT5 order intent file ถูกเขียนโดย dry-run
- [ ] Risk manager reject เมื่อ RR ต่ำกว่า `1.5`
- [ ] Risk manager reject เมื่อไม่มี SL
- [ ] Risk manager reject เมื่อเกิน daily loss
- [ ] Risk manager reject เมื่อแพ้ติดกันตาม config
- [ ] Execution policy reject เมื่อ spread สูง
- [ ] Execution policy reject เมื่อ candle ยังไม่ปิด
- [ ] Execution policy reject เมื่อ fakeout
- [ ] มี emergency stop plan
- [ ] ทดสอบด้วย demo account เท่านั้น

ยังไม่ควรเปิด demo auto trade ถ้า journal ยังอธิบายเหตุผลเข้า/ไม่เข้าไม่ครบ

---

## 5. Before Considering Live Checklist

ก่อนพิจารณาเงินจริง ต้องมีหลักฐานขั้นต่ำ:

- [ ] Backtest อย่างน้อย 3-6 เดือน
- [ ] แยกผลตาม session: Asia, London, New York
- [ ] Demo forward test อย่างน้อย 2-4 สัปดาห์
- [ ] มีสถิติ win rate, profit factor, max drawdown, average win/loss
- [ ] มีสถิติ RR เฉลี่ยและ consecutive losses
- [ ] มีการวิเคราะห์เหตุผลที่แพ้
- [ ] มีการวิเคราะห์เหตุผลที่ skip trade
- [ ] มี broker reconciliation
- [ ] มี real position sync
- [ ] มี order lifecycle tracking
- [ ] มี handling สำหรับ rejected order / partial fill
- [ ] มี live spread/news feed ที่เชื่อถือได้
- [ ] มี manual approval process
- [ ] มี kill switch ที่ทดสอบแล้ว

ถ้าขาดข้อใดข้อหนึ่ง ให้ถือว่ายังไม่พร้อมเงินจริง

---

## 6. Risk Limits

ค่า risk ขั้นต่ำที่ต้องยึด:

| Rule | Limit |
|---|---|
| Risk per trade | ไม่เกิน `1%` |
| Max daily loss | ไม่เกิน `3%` |
| Minimum RR | ไม่ต่ำกว่า `1:1.5` |
| Consecutive losses | หยุดตาม config |
| Daily trades | จำกัดด้วย `MAX_TRADES_PER_DAY` |
| Spread | ต้องไม่เกิน `MAX_SPREAD_POINTS` |
| Session | ต้องอยู่ใน `ALLOWED_SESSIONS` |

ถ้า RR ต่ำกว่า `1.5` ต้อง no-trade  
ถ้าไม่มี SL ต้อง no-trade  
ถ้าถึง max daily loss ต้องหยุด  
ถ้าแพ้ติดกันตามเงื่อนไขต้อง cooldown หรือหยุดทั้งวัน

---

## 7. No Martingale / No Recovery Lot Rule

ห้ามใช้:

- Martingale
- Grid recovery
- เพิ่ม lot หลังแพ้
- เพิ่ม risk เพื่อเอาคืน
- เปิด order ซ้อนทิศทางเดียวกันโดยไม่มี confirm ใหม่
- ไล่ราคาเพราะกลัวตกรถ

Lot size ต้องคำนวณจาก:

- Account balance
- Risk percent
- Entry
- Stop loss distance
- Contract size
- Broker volume rules

ถ้าข้อมูลไม่ครบ ให้ reject ไม่ใช่เดา

---

## 8. How To Confirm No Real Order Was Sent

ต้องเห็นข้อความใน console:

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

ตรวจว่าไม่ได้เรียก live execution path:

```powershell
Select-String -Path src\trading_signal_bot\dry_run_main.py -Pattern "auto_trade|process_auto_trade|order_file"
Select-String -Path src\trading_signal_bot\pipeline_adapter.py -Pattern "auto_trade|process_auto_trade|order_file"
```

Expected:

```text
ไม่ควรเจอ import หรือ call ที่ทำให้ dry-run ส่ง order จริง
```

---

## 9. How To Confirm No MT5 Order Intent Was Written

ตรวจไฟล์ order intent:

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

1. หยุดระบบทันที
2. ห้ามรันต่อ
3. ตรวจว่าไฟล์ถูกสร้างจาก process ไหน
4. ตรวจว่าไม่ได้รัน `main.py` หรือ `auto_trade.py` ผิด entry point
5. ตรวจ journal และ console output
6. ลบหรือ archive ไฟล์เฉพาะเมื่อมั่นใจว่าไม่ใช่ไฟล์ production สำคัญ

---

## 10. Journal Review Procedure

ดู journal ล่าสุด:

```powershell
Get-Content .\logs\audit_journal.jsonl -Tail 10
```

ตรวจ field สำคัญ:

- [ ] `event_type`
- [ ] `symbol`
- [ ] `timeframe`
- [ ] `action`
- [ ] `mode`
- [ ] `htf_bias`
- [ ] `execution_trend`
- [ ] `structure_label`
- [ ] `price_location`
- [ ] `candle_confirmation_summary`
- [ ] `entry`
- [ ] `stop_loss`
- [ ] `tp1`
- [ ] `tp2`
- [ ] `risk_reward`
- [ ] `approved`
- [ ] `reasons`
- [ ] `metadata`

ถ้าเจอ `PAPER_ORDER_INTENT` ต้องตรวจ metadata:

```json
{
  "order_sent": false,
  "order_intent_written": false
}
```

ถ้า metadata ไม่ชัดเจน ให้หยุดและตรวจ flow ก่อน

---

## 11. Emergency Stop Procedure

ถ้าพบ behavior ผิดปกติ:

1. หยุด process ทันที
2. ปิด terminal ที่รัน bot
3. ตรวจว่าไม่มี order intent file
4. ตรวจ journal ล่าสุด
5. ตรวจ console summary
6. รัน tests
7. ห้ามกลับมารันใหม่จนกว่าจะรู้สาเหตุ

คำสั่งตรวจ:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
Get-Content .\logs\audit_journal.jsonl -Tail 20
python -m unittest discover -s tests
```

ถ้าเกี่ยวข้องกับ MT5 ให้ตรวจใน MT5 terminal ด้วยว่ามี position/order จริงหรือไม่

---

## 12. What To Do When Journal Write Fails

ถ้า journal เขียนไม่ได้:

- [ ] หยุด dry-run
- [ ] ตรวจ permission ของ `logs/`
- [ ] ตรวจว่า disk ไม่เต็ม
- [ ] ตรวจว่าไฟล์ไม่ได้ถูก lock โดยโปรแกรมอื่น
- [ ] ตรวจ error message ใน console
- [ ] ห้ามใช้ผล dry-run ที่ไม่มี audit trail

คำสั่งตรวจ folder:

```powershell
Test-Path .\logs
Get-ChildItem .\logs
```

ถ้าไม่มี journal ห้ามสรุปว่าระบบผ่าน เพราะไม่มีหลักฐาน decision trail

---

## 13. What To Do When Spread / News / Session Rejects

ถ้า reject เพราะ spread:

- [ ] ตรวจ `DRY_RUN_SPREAD_POINTS`
- [ ] ตรวจ `MAX_SPREAD_POINTS`
- [ ] ไม่เพิ่ม spread limit โดยไม่มีเหตุผล
- [ ] หลีกเลี่ยงช่วง liquidity ต่ำ

ถ้า reject เพราะ news:

- [ ] ตรวจ `DRY_RUN_HIGH_IMPACT_NEWS_NEARBY`
- [ ] ตรวจ news calendar
- [ ] ห้ามเทรดข่าวแรงโดยไม่ลด risk หรือมีแผนชัดเจน

ถ้า reject เพราะ session:

- [ ] ตรวจ `DRY_RUN_SESSION`
- [ ] ตรวจ `ALLOWED_SESSIONS`
- [ ] ไม่บังคับเทรดนอก session ที่ระบบออกแบบไว้

reject เหล่านี้เป็น safety feature ไม่ใช่ bug โดยอัตโนมัติ

---

## 14. What To Do When Tests Fail

ถ้า tests fail:

1. หยุดพัฒนา feature ใหม่
2. อ่าน failure message
3. แยกว่าเป็น test data issue หรือ production bug
4. ถ้าเป็น bug ให้เขียน/รักษา test ที่ reproduce ได้
5. แก้ทีละไฟล์ตาม scope
6. รัน tests ซ้ำ
7. ห้ามใช้ dry-run result จนกว่า tests จะผ่าน

คำสั่งหลัก:

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
python -m unittest discover -s tests
```

Expected:

```text
OK
```

---

## 15. Explicit Live Trading Blockers

ห้าม live trading ถ้ายังมีข้อใดข้อหนึ่ง:

- [ ] ยังไม่มี broker reconciliation
- [ ] ยังไม่มี real position sync
- [ ] ยังไม่มี order lifecycle tracking
- [ ] ยังไม่มี partial fill / rejected order handling
- [ ] ยังไม่มี persistent trade state
- [ ] ยังไม่มี reliable live news filter
- [ ] ยังไม่มี emergency kill switch ที่ทดสอบแล้ว
- [ ] ยังไม่มี backtest 3-6 เดือน
- [ ] ยังไม่มี demo forward test 2-4 สัปดาห์
- [ ] ยังไม่มี manual approval process
- [ ] ยังไม่มีหลักฐานว่า max drawdown อยู่ในระดับรับได้

ถ้ามี blocker เหล่านี้ ให้ใช้ได้เฉพาะ dry-run / paper-demo เท่านั้น

---

## 16. Final Safety Statement

ระบบนี้ยังเป็น Dry-run / Paper-Demo เท่านั้น

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

คำเตือน:

- ห้ามใช้เงินจริง
- ห้ามเปิด Auto Trade จริง
- ห้าม Martingale
- RR ต้องไม่ต่ำกว่า `1.5`
- Risk ต่อไม้ไม่เกิน `1%`
- Max daily loss ไม่เกิน `3%`
- ต้อง Demo Forward Test อย่างน้อย 2-4 สัปดาห์ก่อนพิจารณาขั้นต่อไป

การทดสอบผ่านไม่ได้แปลว่าระบบพร้อมเงินจริง ต้องมีหลักฐานจาก backtest, forward test, journal review และ risk review ก่อนเสมอ

