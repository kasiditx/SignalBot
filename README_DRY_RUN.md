# README_DRY_RUN.md

คู่มือใช้งานระบบ Trading Bot แบบ Paper/Demo Dry-run

> No order was sent.  
> No MT5 order intent was written.  
> Dry-run only.  
> ระบบนี้ยังไม่พร้อมใช้เงินจริง

---

## 1. Overview

ระบบ dry-run นี้เป็น entry point แยกสำหรับทดสอบ pipeline ของ Trading Bot โดยไม่ส่งออเดอร์จริงและไม่เขียน MT5 order intent file

dry-run flow ถูกออกแบบให้ใช้ตรวจสอบว่า trade candidate ผ่านหรือถูก reject เพราะอะไร เช่น:

- Market structure / top-down context
- Supply / demand zone context
- Candle confirmation
- No-trade filter
- Execution policy
- Risk manager
- Journal / audit trail

จุดประสงค์คือช่วยให้ตรวจ logic และ audit decision ได้ก่อนเข้าสู่ demo forward test หรือ integration phase ถัดไป

---

## 2. Current Status: Paper/Demo Dry-run Only

สถานะปัจจุบัน:

- ใช้สำหรับ paper/demo dry-run เท่านั้น
- ไม่ส่งออเดอร์จริง
- ไม่เขียน MT5 order intent file
- ไม่เรียก execution จริง
- ไม่ควรใช้เงินจริง

แม้ test ทั้งหมดผ่านแล้ว ก็ยังไม่ถือว่าพร้อม live trading เพราะยังไม่มี broker reconciliation, live position sync, order lifecycle tracking และ demo forward test ที่เพียงพอ

---

## 3. How Dry-run Works

คำสั่งหลัก:

```powershell
python -m trading_signal_bot.dry_run_main
```

flow โดยย่อ:

1. โหลด `.env` และ config เดิม
2. โหลด `SignalConfig`
3. โหลด `AutoTradeConfig` สำหรับ position sizing เท่านั้น
4. โหลด multi-timeframe candles
5. อ่านค่า `DRY_RUN_*` จาก environment
6. สร้าง dry-run candidate
7. ตรวจ no-trade filter
8. ตรวจ execution policy
9. ตรวจ risk manager
10. เขียน journal CSV/JSONL
11. แสดง summary ทาง console

ถ้า input ไม่ครบ ระบบต้อง reject อย่างปลอดภัย ไม่เดาทิศทาง ไม่เดาราคาเข้า ไม่เดา SL/TP ให้เอง

---

## 4. Required Market Data

dry-run ต้องใช้ candle data ตาม config ของระบบ โดย target flow ปัจจุบันคือ:

| Timeframe | หน้าที่ |
|---|---|
| `H4` / `H1` | HTF bias |
| `M30` / `M15` | Supply / Demand และ zone context |
| `M5` | Momentum confirmation |
| `M1` | Execution trigger |

ต้องมี `M1` candles สำหรับ execution timeframe หากใช้ multi-timeframe mode

ถ้าไม่มีข้อมูล `M1` ระบบควร reject ที่ stage `market_data` และไม่รันต่อแบบเงียบ ๆ

---

## 5. Required Environment Variables

ค่า dry-run หลัก:

| Env | ความหมาย |
|---|---|
| `DRY_RUN_ACTION` | `BUY`, `SELL` หรือปล่อยว่างเพื่อ no-trade reject |
| `DRY_RUN_ENTRY` | ราคา entry จำลอง |
| `DRY_RUN_STOP_LOSS` | stop loss จำลอง |
| `DRY_RUN_TP1` | take profit 1 |
| `DRY_RUN_TP2` | take profit 2 |
| `DRY_RUN_RISK_REWARD` | risk/reward เช่น `1.5` |
| `DRY_RUN_CURRENT_PRICE` | ราคาปัจจุบันสำหรับ execution policy |
| `DRY_RUN_SPREAD_POINTS` | spread ปัจจุบัน |
| `DRY_RUN_ATR_VALUE` | ATR ปัจจุบัน ถ้ามี |
| `DRY_RUN_AVERAGE_ATR` | ATR เฉลี่ย ถ้ามี |
| `DRY_RUN_SESSION` | session เช่น `Asia`, `London`, `NewYork` |
| `DRY_RUN_HIGH_IMPACT_NEWS_NEARBY` | `true` หรือ `false` |
| `DRY_RUN_MODE` | ค่า default คือ `paper`; ห้ามใช้ `live` |

ค่า config สำคัญที่ควรตรวจ:

| Env | ความหมาย |
|---|---|
| `SIGNAL_EXECUTION_TIMEFRAME` | default ควรเป็น `M1` |
| `SIGNAL_MOMENTUM_TIMEFRAME` | default ควรเป็น `M5` |
| `SIGNAL_ZONE_TIMEFRAMES` | default `M30,M15` |
| `SIGNAL_HTF_TIMEFRAMES` | default `H4,H1` |
| `SIGNAL_RISK_REWARD` | ต้องไม่ต่ำกว่า `1.5` |
| `RISK_PER_TRADE` | default ไม่เกิน `1.0` |
| `MAX_DAILY_LOSS` | default `3.0` |
| `MAX_SPREAD_POINTS` | spread limit |

---

## 6. Windows PowerShell Example

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"

$env:DRY_RUN_ACTION="BUY"
$env:DRY_RUN_ENTRY="2400.00"
$env:DRY_RUN_STOP_LOSS="2398.00"
$env:DRY_RUN_TP1="2403.00"
$env:DRY_RUN_TP2="2405.00"
$env:DRY_RUN_RISK_REWARD="1.5"
$env:DRY_RUN_CURRENT_PRICE="2400.00"
$env:DRY_RUN_SPREAD_POINTS="250"
$env:DRY_RUN_ATR_VALUE="1.2"
$env:DRY_RUN_AVERAGE_ATR="1.0"
$env:DRY_RUN_SESSION="London"
$env:DRY_RUN_HIGH_IMPACT_NEWS_NEARBY="false"
$env:DRY_RUN_MODE="paper"

python -m trading_signal_bot.dry_run_main
```

ตัวอย่างให้ระบบ reject เพราะข้อมูลไม่ครบ:

```powershell
$env:PYTHONPATH="src"
$env:DRY_RUN_ACTION=""
python -m trading_signal_bot.dry_run_main
```

ระบบไม่ควรเดา trade เอง และควรแสดง reject reason อย่างชัดเจน

---

## 7. macOS/Linux Example

```bash
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1

export DRY_RUN_ACTION=BUY
export DRY_RUN_ENTRY=2400.00
export DRY_RUN_STOP_LOSS=2398.00
export DRY_RUN_TP1=2403.00
export DRY_RUN_TP2=2405.00
export DRY_RUN_RISK_REWARD=1.5
export DRY_RUN_CURRENT_PRICE=2400.00
export DRY_RUN_SPREAD_POINTS=250
export DRY_RUN_ATR_VALUE=1.2
export DRY_RUN_AVERAGE_ATR=1.0
export DRY_RUN_SESSION=London
export DRY_RUN_HIGH_IMPACT_NEWS_NEARBY=false
export DRY_RUN_MODE=paper

python -m trading_signal_bot.dry_run_main
```

---

## 8. Output Meaning

ตัวอย่าง output:

```text
Dry-run pipeline summary
approved=False
stage=no_trade_filter
reasons=Price is in the middle of the zone
No order was sent. No MT5 order intent was written.
No order was sent. No MT5 order intent was written. Dry-run only.
```

ความหมาย field หลัก:

| Field | ความหมาย |
|---|---|
| `approved` | `True` ถ้าผ่านทุก gate, `False` ถ้า reject |
| `stage` | จุดที่จบ เช่น `adapter_preflight`, `market_data`, `no_trade_filter`, `execution_policy`, `risk_manager`, `approved` |
| `reasons` | เหตุผลที่ reject หรือ `none` |
| `execution_plan` | แผน entry/SL/TP เฉพาะกรณีผ่าน execution policy |
| `risk_decision` | ผล risk check และ volume เฉพาะกรณีถึง risk manager |

แม้ `approved=True` ก็ยังเป็นเพียง paper/demo decision เท่านั้น ไม่ใช่คำสั่งซื้อขายจริง

---

## 9. Journal Output

ตำแหน่ง journal default:

```text
logs/audit_journal.csv
logs/audit_journal.jsonl
```

ดู JSONL ล่าสุดบน Windows:

```powershell
Get-Content .\logs\audit_journal.jsonl -Tail 10
```

event ที่อาจพบ:

- `SIGNAL_GENERATED`
- `NO_TRADE`
- `EXECUTION_POLICY_REJECT`
- `RISK_MANAGER_REJECT`
- `EXECUTION_PLAN_APPROVED`
- `PAPER_ORDER_INTENT`
- `ERROR`

หมายเหตุ: `PAPER_ORDER_INTENT` ใน dry-run ต้องมี metadata ประมาณนี้:

```json
{
  "order_sent": false,
  "order_intent_written": false
}
```

---

## 10. Common Reject Stages

| Stage | ความหมาย |
|---|---|
| `adapter_preflight` | env dry-run ไม่ครบ เช่น ไม่มี action, entry, SL, TP หรือ RR |
| `mode_validation` | mode ไม่ใช่ paper/demo หรือพยายามใช้ live |
| `market_data` | ไม่มี candle data ที่จำเป็น เช่นไม่มี M1 |
| `no_trade_filter` | ไม่ผ่านเงื่อนไข no-trade เช่น mid-zone, no confirmation, sideway, RR ต่ำ |
| `execution_policy` | ไม่ผ่านหน้างาน เช่น spread สูง, candle ยังไม่ปิด, fakeout, ATR ผิดปกติ |
| `risk_manager` | ไม่ผ่าน risk เช่น daily loss, consecutive losses, same-direction stacking |
| `approved` | ผ่านทุก gate แต่ยังไม่ส่ง order จริง |

---

## 11. Safety Statement

ก่อนเชื่อผลลัพธ์ใด ๆ ต้องเห็นข้อความนี้:

```text
No order was sent.
No MT5 order intent was written.
Dry-run only.
```

ถ้าไม่มีข้อความนี้ ให้หยุดตรวจทันที

---

## 12. What This Does NOT Do

ระบบ dry-run นี้ไม่ทำสิ่งต่อไปนี้:

- ไม่ส่งคำสั่งซื้อขายจริง
- ไม่เขียน MT5 order intent file
- ไม่เรียก MQL5 EA
- ไม่ sync position จริงจาก broker
- ไม่จัดการ partial fill / rejection จริงจาก broker
- ไม่รับประกันกำไร
- ไม่สร้างสัญญาณ BUY/SELL เองถ้าไม่ได้ส่ง input มา
- ไม่ควรใช้เป็น live execution bot

---

## 13. Not Ready for Live Trading

ระบบยังไม่พร้อมเงินจริง เพราะยังขาด:

- Broker reconciliation
- Real position sync
- Order lifecycle tracking
- Partial fill / rejected order handling
- Live spread/news feed ที่เชื่อถือได้
- Persistent trade state
- Backtest อย่างน้อย 3-6 เดือน แยก session
- Demo forward test อย่างน้อย 2-4 สัปดาห์
- Manual approval process ก่อนเปิด execution จริง

คำเตือน: โค้ดนี้มีความเสี่ยงหากใช้กับบัญชีเงินจริง ควรทดสอบกับบัญชี Demo หรือ Paper Trading ก่อน และต้องตรวจสอบเงื่อนไขคำสั่งซื้อขายกับโบรกเกอร์ของคุณ

