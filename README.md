# Trading Bot

## เป้าหมายของบอท

โปรเจกต์นี้มีเป้าหมายเพื่อพัฒนา trading bot ที่สามารถรันแบบจำลองหรือ paper/demo ได้ก่อนนำไปใช้กับเงินจริง

สถานะปัจจุบัน: ยังไม่มี implementation ใน repository นี้ จึงยังไม่สามารถยืนยันได้ว่า bot รองรับตลาดใด exchange ใด timeframe ใด หรือ strategy ใด

เป้าหมายที่ควรกำหนดให้ชัดเจนก่อนเริ่มพัฒนา:

- ตลาดและสินทรัพย์ที่ต้องการเทรด เช่น crypto, stock, forex หรือ futures
- exchange หรือ broker ที่ต้องเชื่อมต่อ
- รูปแบบการรัน เช่น backtest, paper trading, live trading
- ขอบเขตการตัดสินใจของบอท เช่น signal-only, auto-entry, auto-exit หรือ full automation
- ข้อจำกัดด้าน risk, capital และ maximum drawdown

## สมมติฐาน

เอกสารนี้ตั้งอยู่บนสมมติฐานขั้นต่ำเท่านั้น เพราะ repository ยังไม่มีโค้ดหรือไฟล์ config ให้ตรวจสอบ

- ต้องเริ่มจาก paper/demo trading ก่อนเสมอ
- ห้ามฝัง API key, secret, token หรือ credential ไว้ใน source code
- strategy, risk rule และ order execution ต้องแยก responsibility ออกจากกัน
- ทุก order ที่จะส่งออกไปต้องผ่าน validation และ risk check ก่อน
- การเชื่อมต่อ exchange หรือ broker ต้องรองรับ error, retry limit, timeout และ rate limit

## ข้อจำกัดและความเสี่ยง

ข้อจำกัดปัจจุบัน:

- ยังไม่มี source code
- ยังไม่มี dependency manifest เช่น `package.json`, `requirements.txt`, `pyproject.toml` หรือ `go.mod`
- ยังไม่มี test suite
- ยังไม่มี config schema
- ยังไม่มีรายละเอียด exchange, broker, strategy หรือ data source

ความเสี่ยงหลักของ trading bot:

- Market risk: strategy อาจขาดทุนเมื่อ market regime เปลี่ยน
- Execution risk: slippage, partial fill, rejected order หรือ latency
- Data risk: ราคาผิด, candle ขาด, timestamp ไม่ตรง หรือ data delay
- Integration risk: API เปลี่ยน, rate limit, network timeout หรือ credential หมดอายุ
- Operational risk: process crash, duplicate order, state ไม่ sync กับ exchange
- Security risk: credential leak หรือ permission ของ API key กว้างเกินจำเป็น

## โครงสร้างโปรเจกต์

โครงสร้างปัจจุบัน:

```text
.
└── README.md
```

โครงสร้างที่ควรพิจารณาเมื่อเริ่ม implementation:

```text
.
├── README.md
├── config/
│   └── example.env
├── src/
│   ├── data/
│   ├── execution/
│   ├── risk/
│   ├── strategy/
│   └── main.*
└── tests/
```

หมายเหตุ: โครงสร้างด้านบนเป็นข้อเสนอเบื้องต้น ไม่ใช่โครงสร้างที่มีอยู่จริงใน repository ตอนนี้

## Strategy Logic

strategy ปัจจุบันใช้แนวคิดจากคู่มือ price action / market structure โดยเน้นให้บอทรอ setup ที่ยืนยันแล้ว ไม่ใช่ signal จาก indicator อย่างเดียว

ชื่อ strategy:

```text
Pro MTF Price Action Structure
```

กฎหลัก:

- ใช้ market structure และแนวรับ/แนวต้านจากแท่งก่อนหน้าเป็นตัวตัดสินหลัก
- ต้องมี body close ข้ามแนวรับ/แนวต้านสำคัญก่อน จึงพิจารณา `BUY` หรือ `SELL`
- ถ้ามีแต่ wick ทะลุแล้วปิดกลับเข้า range จะถือเป็น liquidity sweep และให้ `WAIT`
- ใช้แท่งล่าสุด 4-7 แท่งเพื่ออ่าน momentum ปัจจุบัน
- ถ้าเห็น momentum exhaustion แบบ large-medium-small จะไม่เข้า
- ถ้าแท่งปิดชิดปลายทางมากเกินไปแบบ no continuation wick จะไม่ไล่ราคา
- ใช้ EMA, RSI และ ATR เป็น confirmation/risk context ไม่ใช่ blind signal
- body breakout ขั้นต่ำตั้งค่าได้ผ่าน `SIGNAL_BODY_BREAK_ATR_RATIO` ค่า default ปัจจุบันคือ `0.20`
- ใช้ `M5` เป็น execution timeframe แล้วกรองด้วย `D1/H4/H1/M30/M15` ก่อนปล่อยสัญญาณ
- `30M` และ `15M` ต้องไปทางเดียวกับสัญญาณทั้งคู่ เพื่อลด breakout ที่ยังไม่มีแรงหนุนระยะกลาง
- `1D/4H/1H` ต้องไม่มี timeframe ที่สวนทางกับสัญญาณ ถ้ามีฝั่งตรงข้ามปนอยู่ให้ `WAIT`
- Stop Loss ใช้ wick protection บริเวณปลายไส้เทียนก่อนหน้า
- Risk/Reward ตั้งค่าผ่าน `SIGNAL_RISK_REWARD` ค่า default ปัจจุบันคือ `0.7` เพื่อให้ backtest ล่าสุดเข้าใกล้ win rate มากกว่า 70%
- หมายเหตุ: `0.7R` คือ TP สั้นกว่า SL จึงต้องติดตาม Profit Factor, drawdown และ slippage ใกล้ชิดกว่าระบบที่ใช้ RR มากกว่า 1
- ถ้าโครงสร้างยัง sideways/mixed ให้ `WAIT`

เงื่อนไข `BUY`:

- ราคาปิดเหนือ resistance เดิมด้วย body ที่ชัดเจน
- ไม่ใช่ wick sweep
- ไม่ใช่ exhaustion candle
- direction filter ไม่ขัดกับ EMA context
- HTF bias เป็น bullish และ `M30/M15` เป็น bullish พร้อมกัน
- ไม่มี `D1/H4/H1` timeframe ใดเป็น bearish

เงื่อนไข `SELL`:

- ราคาปิดใต้ support เดิมด้วย body ที่ชัดเจน
- ไม่ใช่ wick sweep
- ไม่ใช่ exhaustion candle
- direction filter ไม่ขัดกับ EMA context
- HTF bias เป็น bearish และ `M30/M15` เป็น bearish พร้อมกัน
- ไม่มี `D1/H4/H1` timeframe ใดเป็น bullish

กรณี `WAIT`:

- ยังไม่มี body close break
- เป็น liquidity sweep
- โครงสร้างยัง mixed/sideways
- RSI อยู่สุดโต่งเกินไป
- multi-timeframe filter ยังไม่ผ่าน เช่น `30M/15M` ไม่ไปทางเดียวกัน หรือ `1D/4H/1H` มี timeframe สวนทางกับสัญญาณ
- risk/reward หรือ clean momentum ยังไม่ชัด

## Risk Management

ยังไม่มี risk module ใน repository นี้

กฎ risk management ขั้นต่ำที่ควรมีก่อนรัน live:

- จำกัด position size ต่อ trade
- จำกัดจำนวน order ต่อช่วงเวลา
- จำกัด maximum daily loss
- จำกัด maximum drawdown
- ตรวจ available balance ก่อนส่ง order
- ป้องกัน duplicate order
- ตรวจว่ามี open position หรือ pending order อยู่แล้วหรือไม่
- มี kill switch เพื่อหยุด bot เมื่อเกิด error ต่อเนื่องหรือขาดทุนเกิน limit

ทุกคำสั่งซื้อขายควรผ่าน flow ลักษณะนี้:

```text
market data -> strategy signal -> risk validation -> order creation -> execution -> reconciliation
```

## Code

มี implementation เบื้องต้นสำหรับ signal-only Telegram bot แล้ว โดยยังไม่ส่งคำสั่งซื้อขายจริง

โครงสร้างหลัก:

- `src/trading_signal_bot/data.py`: อ่านและ validate OHLCV จาก CSV
- `src/trading_signal_bot/indicators.py`: คำนวณ EMA, RSI และ ATR
- `src/trading_signal_bot/strategy.py`: สร้างสัญญาณ `BUY`, `SELL` หรือ `WAIT`
- `src/trading_signal_bot/message.py`: จัดรูปแบบข้อความสำหรับ Telegram
- `src/trading_signal_bot/telegram.py`: ส่งข้อความผ่าน Telegram Bot API
- `src/trading_signal_bot/main.py`: entrypoint สำหรับรันบอท
- `src/trading_signal_bot/webhook_server.py`: รับ TradingView webhook แล้วส่งต่อ Telegram
- `src/trading_signal_bot/mt5_export.py`: export OHLCV จาก MT5 เป็น CSV สำหรับใช้กับ signal bot
- `src/trading_signal_bot/signal_poller.py`: อ่าน CSV ซ้ำ ๆ แล้วส่ง Telegram เมื่อ signal เปลี่ยน
- `mql5/Experts/TradingSignalCsvExporter.mq5`: EA สำหรับ MT5 ที่ export OHLCV เป็น CSV โดยไม่ส่ง order

บอทนี้เป็น signal bot เท่านั้น ไม่เชื่อม broker และไม่ส่ง order จริง

## วิธีติดตั้ง

ต้องมี Python 3.11 ขึ้นไป

โปรเจกต์นี้ใช้เฉพาะ Python standard library สำหรับ runtime ปัจจุบัน

```bash
python3 --version
cp .env.example .env
```

จากนั้นตั้งค่าใน `.env`:

```text
TELEGRAM_BOT_TOKEN=ใส่ token จาก BotFather
TELEGRAM_CHAT_ID=ใส่ chat id ที่ต้องการส่งข้อความ
SIGNAL_DRY_RUN=true
```

ห้าม commit `.env` เพราะมีข้อมูลลับ

### ตั้งค่า TradingView webhook

เพิ่มค่าเหล่านี้ใน `.env`:

```text
TRADINGVIEW_WEBHOOK_SECRET=ตั้งเป็นข้อความลับที่เดายาก
TRADINGVIEW_WEBHOOK_HOST=127.0.0.1
TRADINGVIEW_WEBHOOK_PORT=8080
TRADINGVIEW_WEBHOOK_PATH=/webhook
TRADINGVIEW_WEBHOOK_DRY_RUN=true
```

ถ้าจะให้ TradingView เรียกจากอินเทอร์เน็ต ต้องมี public HTTPS endpoint เช่น reverse proxy, tunnel หรือ server ที่เปิด HTTPS เอง ห้ามเปิด endpoint สาธารณะโดยไม่มี secret

### ตั้งค่า MT5 exporter

บน macOS ถ้า Python package `MetaTrader5` ใช้ไม่ได้ ให้ใช้ EA export CSV แทน วิธีนี้ฟรีและไม่ต้องใช้ TradingView webhook

```text
MT5_SYMBOL=XAUUSD
MT5_TIMEFRAME=H1
MT5_BARS=300
MT5_OUTPUT_CSV=data/mt5_ohlcv.csv
SIGNAL_CSV_PATH=data/mt5_ohlcv.csv
```

### ใช้ MT5 EA export CSV

1. Copy `mql5/Experts/TradingSignalCsvExporter.mq5` ไปที่ `MQL5/Experts`
2. เปิด MT5 แล้วเปิด MetaEditor
3. Compile `TradingSignalCsvExporter.mq5`
4. ถ้าเคยลาก EA ตัวเก่าไว้แล้ว ให้ลบ EA ออกจาก chart ก่อน แล้วลากตัวใหม่ใส่ chart อีกครั้ง
5. กลับไป MT5 แล้วลาก EA ไปใส่ chart ที่ต้องการ เช่น XAUUSD M5
6. ตั้งค่า:
   - `InpBars`: อย่างน้อย 300
   - `InpIntervalSeconds`: เช่น 5
   - `InpExportD1/H4/H1/M30/M15/M5`: `true`

EA นี้ไม่ส่งคำสั่งซื้อขาย ทำหน้าที่ export CSV หลาย timeframe เท่านั้น:

- `MQL5/Files/mt5_ohlcv_D1.csv`
- `MQL5/Files/mt5_ohlcv_H4.csv`
- `MQL5/Files/mt5_ohlcv_H1.csv`
- `MQL5/Files/mt5_ohlcv_M30.csv`
- `MQL5/Files/mt5_ohlcv_M15.csv`
- `MQL5/Files/mt5_ohlcv_M5.csv`
- `MQL5/Files/mt5_ohlcv.csv` alias ของ M5

บอทใช้ `M5` เป็น execution timeframe และใช้ `D1/4H/1H/30M/15M` เป็น trend/context filter ก่อนส่งสัญญาณ

## วิธีรันแบบ Paper/Demo

รันแบบ dry-run เพื่อดูข้อความก่อนส่งจริง:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.main
```

เมื่อเช็กข้อความแล้วและต้องการส่งเข้า Telegram ให้ตั้งค่า:

```text
SIGNAL_DRY_RUN=false
```

แล้วรัน:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.main
```

ข้อมูลราคาที่ใช้ต้องเป็น CSV รูปแบบนี้:

```text
timestamp,open,high,low,close,volume
2026-05-01 00:00,100.00,100.90,99.70,100.60,1200
```

ตั้ง path ของไฟล์ข้อมูลด้วย `SIGNAL_CSV_PATH`

### รัน TradingView webhook แบบ dry-run

เริ่ม server:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.webhook_server
```

ตัวอย่าง payload ที่ใส่ใน TradingView alert webhook message:

```json
{
  "secret": "change-me",
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "timeframe": "{{interval}}",
  "price": "{{close}}",
  "entry": "{{close}}",
  "stop_loss": "",
  "take_profit": "",
  "risk_reward": "",
  "confidence": "Medium",
  "reason": "TradingView alert condition triggered",
  "invalidation": "Cancel the plan if price closes back through the invalidation level"
}
```

ถ้าเป็น Pine Script indicator alert ที่กำหนดข้อความเอง ให้ส่ง `action` เป็น `BUY`, `SELL` หรือ `WAIT` เท่านั้น

ทดสอบ webhook ในเครื่อง:

```bash
curl -X POST http://127.0.0.1:8080/webhook \
  -H 'Content-Type: application/json' \
  --data @samples/tradingview_webhook_payload.json
```

เมื่อตรวจ dry-run แล้วจึงตั้ง:

```text
TRADINGVIEW_WEBHOOK_DRY_RUN=false
```

### Export ข้อมูลจาก MT5 เป็น CSV

คำเตือน: ส่วนนี้ดึงข้อมูลราคาเท่านั้น ไม่ส่ง order

```bash
PYTHONPATH=src python3 -m trading_signal_bot.mt5_export
PYTHONPATH=src python3 -m trading_signal_bot.main
```

ถ้าใช้ EA export CSV จาก MT5 ให้รัน poller:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.signal_poller
```

ค่า `SIGNAL_POLL_SECONDS` ใช้กำหนดว่าจะเช็ก CSV ทุกกี่วินาที และ `SIGNAL_DRY_RUN=true` จะทำให้พิมพ์ข้อความอย่างเดียว ยังไม่ส่ง Telegram จริง

### Auto Trade ผ่าน MT5

คำเตือน: โค้ดนี้มีความเสี่ยงหากใช้กับบัญชีเงินจริง ควรทดสอบกับบัญชี Demo หรือ Paper Trading ก่อน และต้องตรวจสอบเงื่อนไขคำสั่งซื้อขายกับโบรกเกอร์ของคุณ

ระบบ auto trade แยกเป็น 2 ชั้น:

- Python bot สร้างสัญญาณจาก strategy เดียวกับ backtest แล้วเขียน order intent
- MT5 Expert Advisor `mql5/Experts/TradingSignalAutoTrader.mq5` อ่าน order intent จาก `MQL5/Files/trading_signal_order.csv` และส่ง market order พร้อม SL/TP

ค่าเริ่มต้นใน `.env` ปิด auto trade ไว้:

```text
AUTO_TRADE_ENABLED=false
AUTO_TRADE_MODE=paper
AUTO_TRADE_RISK_PERCENT=0.5
AUTO_TRADE_MAX_VOLUME=0.01
```

ลำดับที่แนะนำ:

1. รัน backtest ให้ผ่านก่อน
2. เปิด paper mode:

```text
AUTO_TRADE_ENABLED=true
AUTO_TRADE_MODE=paper
```

แล้วรัน:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.signal_poller
```

ระบบจะบันทึก order จำลองที่:

```text
logs/auto_trade_journal.csv
```

3. ถ้าต้องการให้ MT5 EA อ่านคำสั่ง ให้เปลี่ยนเป็น:

```text
AUTO_TRADE_ENABLED=true
AUTO_TRADE_MODE=mt5_file
AUTO_TRADE_ORDER_FILE=/Users/kasidit/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files/trading_signal_order.csv
```

4. คัดลอกหรือเปิด `mql5/Experts/TradingSignalAutoTrader.mq5` ใน MetaEditor แล้ว Compile
5. ลาก EA `TradingSignalAutoTrader` ลงกราฟ `XAUUSD.iux` หรือ symbol เดียวกับ `.env`
6. เริ่มจาก `InpDryRun=true` ก่อนเสมอ เพื่อให้ EA แค่ print ว่ารับคำสั่งได้ แต่ยังไม่ส่ง order จริง
7. เมื่อทดสอบบน Demo แล้วเท่านั้น ค่อยพิจารณา `InpDryRun=false`

Risk sizing ปัจจุบันคำนวณแบบประมาณจาก:

```text
lot = account_balance * risk_percent / 100 / (abs(entry - stop_loss) * contract_size)
```

สำหรับ XAUUSD หลาย broker ใช้ contract size ประมาณ `100` แต่ต้องตรวจจาก MT5 symbol specification ของ broker จริงก่อนใช้เงินจริง

## วิธีทดสอบ

รัน unit test:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

test ปัจจุบันครอบคลุม:

- สร้างสัญญาณ `BUY` จาก uptrend ที่มี pullback
- สร้างสัญญาณ `SELL` จาก downtrend ที่มี pullback
- reject ข้อมูล candle ที่ไม่พอ
- parse TradingView webhook payload
- reject webhook secret ที่ไม่ถูกต้อง
- reject action ที่ไม่ใช่ `BUY`, `SELL` หรือ `WAIT`

### Backtest กลยุทธ์

ใช้ข้อมูล CSV จาก MT5 ทั้ง 6 timeframe แล้วรัน strategy เดียวกับ live signal:

```bash
PYTHONPATH=src python3 -m trading_signal_bot.backtest
```

ถ้าต้องการ backtest ล่าสุด 3 เดือน ให้ตั้งค่า:

```text
BACKTEST_LOOKBACK_DAYS=90
```

หรือรันแบบ override เฉพาะครั้ง:

```bash
BACKTEST_LOOKBACK_DAYS=90 PYTHONPATH=src python3 -m trading_signal_bot.backtest
```

ผลลัพธ์จะแสดง:

- ช่วงเวลาที่ backtest
- จำนวน trade
- จำนวน win/loss ที่ปิดแล้ว
- trade ที่ยังค้างอยู่ท้ายข้อมูล (`OPEN_AT_END`)
- win rate ของ trade ที่ปิดแล้ว
- net R
- profit factor
- max drawdown เป็นหน่วย R
- initial/final balance เป็น USD
- net profit เป็น USD และ %
- max money drawdown เป็น USD และ %
- max actual risk per trade จาก lot จริง

รายละเอียด trade จะถูกเขียนที่:

```text
logs/backtest_trades.csv
```

สำหรับบัญชีทดลองทุน `30 USD` ใช้ค่าประมาณนี้:

```text
BACKTEST_INITIAL_BALANCE=30
BACKTEST_RISK_PERCENT=0.5
BACKTEST_CONTRACT_SIZE=100
BACKTEST_MIN_VOLUME=0.01
BACKTEST_MAX_VOLUME=0.01
BACKTEST_VOLUME_STEP=0.01
BACKTEST_COMPOUND=true
```

หมายเหตุสำคัญ: ถ้า broker บังคับ lot ต่ำสุด `0.01` บน XAUUSD บัญชี `30 USD` อาจเสี่ยงจริงต่อไม้สูงกว่า `0.5%` เพราะไม่สามารถลด lot ให้เล็กกว่านี้ได้ ต้องดูบรรทัด `Max actual risk per trade` ทุกครั้ง

ใน live/paper auto trade ฝั่ง Python จะปฏิเสธ order หาก lot ที่คำนวณได้ต่ำกว่า lot ขั้นต่ำของ broker เพราะการบังคับใช้ lot ขั้นต่ำจะทำให้ risk เกินค่าที่ตั้งไว้

หมายเหตุ: backtest นี้จำลองจากแท่ง M5 และถือว่าเมื่อ SL/TP ถูกแตะในแท่งเดียวกันให้ถือว่า SL เกิดก่อนแบบ conservative

## สิ่งที่ต้องตรวจสอบก่อนใช้เงินจริง

ก่อนเปิดใช้งานเงินจริง ต้องตรวจสอบอย่างน้อย:

- Strategy ผ่าน backtest และ paper/demo trading ในช่วงเวลาที่เพียงพอ
- Risk limit ถูก config และทดสอบแล้ว
- API key ใช้ least privilege และไม่เปิด permission เกินจำเป็น
- Secret ไม่ถูก commit ลง repository
- Bot ไม่ส่ง duplicate order เมื่อ process restart
- Bot handle partial fill, rejected order และ network timeout ได้
- State ของ order, position และ balance sync กับ exchange หรือ broker ได้
- มี kill switch และ alert เมื่อเกิด error หรือ loss เกิน limit
- มี log ที่เพียงพอสำหรับ audit แต่ไม่เปิดเผยข้อมูลอ่อนไหว
- มีขั้นตอน rollback หรือหยุดระบบเมื่อเกิด incident

## Template วิเคราะห์ตลาด

ใช้ template นี้สำหรับสรุปมุมมองก่อนเข้า trade หรือก่อนให้ bot ตัดสินใจ โดยต้องอิงจากข้อมูลกราฟและ market data จริงเท่านั้น ห้ามเติมราคา, bias หรือ confidence โดยไม่มีหลักฐานจากข้อมูล

### สรุปภาพรวม

ระบุสินทรัพย์, timeframe, สภาวะตลาดโดยรวม และบริบทสำคัญที่กระทบแผน trade

### แนวโน้มหลัก

ระบุแนวโน้มหลัก เช่น uptrend, downtrend, sideways หรือ range-bound พร้อมเหตุผลจาก price action หรือ indicator ที่ตรวจสอบแล้ว

### Market Structure

อธิบายโครงสร้างตลาด เช่น higher high, higher low, lower high, lower low, break of structure, change of character หรือ consolidation

### แนวรับ / แนวต้าน

| ประเภท | โซนราคา | ความสำคัญ | เหตุผล |
|---|---:|---|---|
| แนวรับ | | | |
| แนวต้าน | | | |

### วิเคราะห์แท่งเทียน

ระบุ candle pattern หรือ price rejection ที่เกี่ยวข้อง เช่น pin bar, engulfing, inside bar, strong close, wick rejection หรือ indecision candle

### Indicator Confirmation

ระบุ indicator ที่ใช้ยืนยันแผน เช่น moving average, RSI, MACD, volume, VWAP หรือ ATR พร้อมเงื่อนไขที่ชัดเจน

### แผนฝั่ง Buy

| รายการ | เงื่อนไข |
|---|---|
| Entry | |
| Stop Loss | |
| Take Profit | |
| Risk/Reward | |
| เงื่อนไขยกเลิกแผน | |

### แผนฝั่ง Sell

| รายการ | เงื่อนไข |
|---|---|
| Entry | |
| Stop Loss | |
| Take Profit | |
| Risk/Reward | |
| เงื่อนไขยกเลิกแผน | |

### จุดที่ควรรอ / ห้ามเข้า

ระบุเงื่อนไขที่ควรรอ confirmation เพิ่ม หรือสภาวะที่ไม่ควรเข้า trade เช่น ราคาอยู่กลาง range, spread กว้าง, volatility ผิดปกติ, ข่าวแรงใกล้ออก หรือ risk/reward ไม่คุ้ม

### ความเสี่ยงที่ต้องระวัง

ระบุความเสี่ยงเฉพาะของ setup เช่น false breakout, liquidity sweep, slippage, funding fee, low volume หรือ high-impact news

### สรุปมุมมอง

- Bias:
- Confidence:
- Best Action:
- Key Risk:

หมายเหตุ: นี่ไม่ใช่คำแนะนำทางการเงินส่วนบุคคล การลงทุนและการเทรดมีความเสี่ยง ผู้ใช้ต้องตัดสินใจด้วยตนเอง
