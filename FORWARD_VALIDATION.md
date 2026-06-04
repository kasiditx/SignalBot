# Forward Dry-run / Paper Validation

## 1. Overview

This guide explains how to run the forward validation entry point:

```text
python -m trading_signal_bot.forward_validation_main
```

Forward validation is used to test the dry-run / paper pipeline against the latest available market data and save audit records for review. It is designed for observation, validation, journaling, and reporting only.

It does not send real orders.

## 2. Current Status: Forward Dry-run / Paper Validation Only

Current status:

- Forward dry-run / paper validation only.
- No live order execution.
- No MT5 order intent file.
- No real-money trading.

Every successful run should include:

```text
No order was sent.
No MT5 order intent was written.
Forward dry-run only.
```

If those safety lines are missing, stop and inspect the run before continuing.

## 3. How Forward Validation Works

The forward validation flow is:

1. Load environment variables.
2. Load `SignalConfig`.
3. Load latest multi-timeframe candles.
4. Build forward validation input from `FORWARD_*` environment variables.
5. Run the dry-run / paper pipeline.
6. Convert the pipeline result into a forward validation record.
7. Write `forward_records.csv`.
8. Append `forward_records.jsonl`.
9. Rebuild `daily_summary.csv`.
10. Rebuild `weekly_summary.csv`.
11. Print a safety summary to the console.

The JSONL record file is the source used to rebuild daily and weekly summaries.

## 4. What This Does

Forward validation does the following:

- Runs one dry-run / paper validation cycle.
- Records whether the pipeline approved, rejected, or skipped the setup.
- Records stage, reasons, action, entry, stop loss, take profits, and risk/reward.
- Records whether execution plan and risk decision data were present.
- Records journal success/failure status from the pipeline result.
- Writes audit records to CSV and JSONL.
- Updates daily and weekly summary CSV files.
- Enforces live mode rejection.

## 5. What This Does NOT Do

Forward validation does not:

- Send a real order.
- Write an MT5 order intent file.
- Create `trading_signal_order.csv`.
- Call `auto_trade.py`.
- Call `process_auto_trade()`.
- Open or manage broker positions.
- Reconcile broker state.
- Track real order lifecycle.
- Handle partial fills or rejected broker orders.
- Replace demo forward testing.

## 6. Required Environment Setup

Set `PYTHONPATH` so Python can import the package from `src`.

Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
```

macOS / Linux:

```bash
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1
```

Before running forward validation, make sure market data/config files referenced by `SignalConfig` are available.

## 7. Environment Variables

| Env | Required | Default | Meaning |
|---|---:|---|---|
| `FORWARD_VALIDATION_OUTPUT_DIR` | No | `logs/forward_validation` | Directory for forward validation records and summaries. |
| `FORWARD_VALIDATION_MODE` | No | `paper` | Validation mode. Use `paper` or `demo`. `live` is rejected. |
| `FORWARD_ACTION` | No | empty | `BUY`, `SELL`, or empty for no explicit action. |
| `FORWARD_ENTRY` | No | empty | Simulated entry price. |
| `FORWARD_STOP_LOSS` | No | empty | Simulated stop loss. |
| `FORWARD_TP1` | No | empty | Simulated take profit 1. |
| `FORWARD_TP2` | No | empty | Simulated take profit 2. |
| `FORWARD_RISK_REWARD` | No | empty | Simulated risk/reward value, for example `1.5`. |
| `FORWARD_CURRENT_PRICE` | Yes for pipeline run | none | Current market price used by dry-run validation. |
| `FORWARD_SPREAD_POINTS` | No | empty | Current spread in points. |
| `FORWARD_SESSION` | No | empty | Trading session, for example `Asia`, `London`, or `NewYork`. |
| `FORWARD_ATR_VALUE` | No | empty | Current ATR value, if available. |
| `FORWARD_AVERAGE_ATR` | No | empty | Average ATR value used for volatility comparison, if available. |
| `FORWARD_HIGH_IMPACT_NEWS_NEARBY` | No | `false` | News risk flag. Supports `true/false`, `1/0`, `yes/no`, `on/off`. |

## 8. Windows PowerShell Example

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"

$env:FORWARD_VALIDATION_OUTPUT_DIR="logs/forward_validation"
$env:FORWARD_VALIDATION_MODE="paper"
$env:FORWARD_ACTION="BUY"
$env:FORWARD_ENTRY="2400.00"
$env:FORWARD_STOP_LOSS="2398.00"
$env:FORWARD_TP1="2403.00"
$env:FORWARD_TP2="2405.00"
$env:FORWARD_RISK_REWARD="1.5"
$env:FORWARD_CURRENT_PRICE="2400.50"
$env:FORWARD_SPREAD_POINTS="20"
$env:FORWARD_SESSION="London"

python -m trading_signal_bot.forward_validation_main
```

## 9. macOS / Linux Example

```bash
export PYTHONPATH=src
export PYTHONDONTWRITEBYTECODE=1

export FORWARD_VALIDATION_OUTPUT_DIR=logs/forward_validation
export FORWARD_VALIDATION_MODE=paper
export FORWARD_ACTION=BUY
export FORWARD_ENTRY=2400.00
export FORWARD_STOP_LOSS=2398.00
export FORWARD_TP1=2403.00
export FORWARD_TP2=2405.00
export FORWARD_RISK_REWARD=1.5
export FORWARD_CURRENT_PRICE=2400.50
export FORWARD_SPREAD_POINTS=20
export FORWARD_SESSION=London

python -m trading_signal_bot.forward_validation_main
```

## 10. Live Mode Reject Behavior

Live mode is intentionally blocked in forward validation.

Windows live reject test:

```powershell
$env:FORWARD_VALIDATION_MODE="live"
python -m trading_signal_bot.forward_validation_main
```

Expected:

- `stage = mode_validation`
- `approved = false`
- `reason = live mode is not allowed`
- `No order was sent.`
- `No MT5 order intent was written.`
- `Forward dry-run only.`

This is expected safety behavior, not a bug.

## 11. Output Files

Default output directory:

```text
logs/forward_validation
```

Output files:

- `logs/forward_validation/forward_records.csv`
- `logs/forward_validation/forward_records.jsonl`
- `logs/forward_validation/daily_summary.csv`
- `logs/forward_validation/weekly_summary.csv`

These files are audit/report files only. They are not MT5 order intent files.

## 12. How To Read forward_records.csv

`forward_records.csv` is intended for quick review in Excel or spreadsheet tools.

Each row represents one forward validation run.

Important fields:

- `timestamp`: When the validation record was created.
- `symbol`: Trading symbol from config.
- `mode`: Validation mode, such as `paper`, `demo`, or rejected `live`.
- `action`: `BUY`, `SELL`, or empty.
- `stage`: Pipeline/validation stage, such as approved, rejected, no-trade, or mode validation.
- `approved`: Whether the dry-run/paper setup was approved.
- `reasons`: Human-readable reasons separated by a delimiter.
- `entry`, `stop_loss`, `tp1`, `tp2`, `risk_reward`: Simulated trade levels.
- `execution_plan_present`: Whether the pipeline produced an execution plan.
- `risk_decision_present`: Whether the pipeline produced a risk decision.
- `order_sent`: Must remain `False`.
- `order_intent_written`: Must remain `False`.
- `journal_success`: Whether journal output was successful.
- `metadata`: Extra market context serialized as JSON text.

If `order_sent` or `order_intent_written` is ever `True`, stop immediately and investigate.

## 13. How To Read forward_records.jsonl

`forward_records.jsonl` is the append-only audit source for forward validation.

Each line is one JSON object.

Use it when:

- You want machine-readable records.
- You want to rebuild daily/weekly summaries.
- You want to inspect metadata as structured JSON.
- You want to compare runs over time.

Reasons are stored as a JSON list. Metadata is stored as a JSON object.

## 14. How To Read daily_summary.csv

`daily_summary.csv` groups forward validation records by date.

Important fields:

- `date`: Calendar date for the summary row.
- `total_runs`: Total forward validation runs that day.
- `approved_count`: Number of approved dry-run/paper decisions.
- `rejected_count`: Number of rejected records.
- `no_trade_count`: Number of no-trade outcomes.
- `execution_reject_count`: Number of execution policy rejects.
- `risk_reject_count`: Number of risk rejects.
- `paper_intent_count`: Number of paper intent stages.
- `journal_failures`: Number of records with journal failure.
- `order_sent_count`: Must remain `0`.
- `order_intent_written_count`: Must remain `0`.
- `top_reason`: Most frequent reason for the day.
- `reason_summary`: JSON string of reason counts.

Use this file to review daily forward validation quality and safety.

## 15. How To Read weekly_summary.csv

`weekly_summary.csv` groups forward validation records by ISO week.

Important fields:

- `week_start`: Monday of the ISO week.
- `week_end`: Sunday of the ISO week.
- `iso_year`: ISO calendar year.
- `iso_week`: ISO week number.
- `total_runs`: Total forward validation runs in that week.
- `approved_count`: Number of approved dry-run/paper decisions.
- `rejected_count`: Number of rejected records.
- `no_trade_count`: Number of no-trade outcomes.
- `execution_reject_count`: Number of execution policy rejects.
- `risk_reject_count`: Number of risk rejects.
- `journal_failures`: Number of journal failures.
- `order_sent_count`: Must remain `0`.
- `order_intent_written_count`: Must remain `0`.
- `top_reason`: Most frequent reason for the week.
- `reason_summary`: JSON string of reason counts.

Use this file to evaluate consistency across multiple forward testing days.

## 16. Safety Checklist Before Every Run

- [ ] ใช้ `FORWARD_VALIDATION_MODE=paper` หรือ `demo`
- [ ] ถ้าทดสอบ `live` ต้องถูก reject เป็น `mode_validation`
- [ ] Console มี `No order was sent.`
- [ ] Console มี `No MT5 order intent was written.`
- [ ] Console มี `Forward dry-run only.`
- [ ] ตรวจว่าไม่มี `trading_signal_order.csv`
- [ ] ตรวจว่าไม่มี `logs/trading_signal_order.csv`
- [ ] ตรวจ `forward_records.jsonl`
- [ ] ตรวจ `daily_summary.csv`
- [ ] ตรวจ `weekly_summary.csv`
- [ ] รัน tests ผ่านก่อนใช้งาน

Windows check for order intent files:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

Expected:

```text
False
False
```

## 17. Troubleshooting

### FORWARD_CURRENT_PRICE missing

If the run fails because `FORWARD_CURRENT_PRICE` is required, set:

```powershell
$env:FORWARD_CURRENT_PRICE="2400.50"
```

Forward validation needs a current price for the dry-run market input.

### invalid FORWARD_ACTION

Valid values:

- `BUY`
- `SELL`
- empty

Invalid values such as `WAIT`, `LONG`, or `SHORT` should be corrected before running.

### invalid boolean env

For `FORWARD_HIGH_IMPACT_NEWS_NEARBY`, use one of:

- true values: `true`, `1`, `yes`, `y`, `on`
- false values: `false`, `0`, `no`, `n`, `off`

### missing candle/config data

If candle or config loading fails:

- Confirm `PYTHONPATH=src`.
- Confirm config paths exist.
- Confirm required timeframe candles exist.
- Confirm `SignalConfig` points to available data.
- Run tests before using forward validation again.

### summary not written

Daily and weekly summaries are written after `forward_records.jsonl` is successfully updated.

If summaries are missing:

- Check whether `forward_records.jsonl` exists.
- Check console error output.
- Check output directory permissions.
- Re-run after fixing the record write problem.

### live mode rejected

This is expected.

`FORWARD_VALIDATION_MODE=live` should create a `mode_validation` reject record and should not run order execution.

### tests failing

If tests fail, stop forward validation until the issue is understood.

Recommended command:

```powershell
$env:PYTHONPATH="src"
$env:PYTHONDONTWRITEBYTECODE="1"
python -m unittest discover -s tests
```

Expected result should end with:

```text
OK
```

## 18. Not Ready For Real Money

This system is not ready for real-money trading.

It still lacks:

- Broker reconciliation
- Real position sync
- Order lifecycle tracking
- Partial fill handling
- Rejected order handling
- Real spread/news validation
- Demo forward test for at least 2-4 weeks

Passing tests and producing forward validation reports does not mean the system is ready for live trading.

Forward validation is for research, audit, paper validation, and process discipline only.

## 19. Final Safety Statement

No order was sent.

No MT5 order intent was written.

Forward dry-run only.

Do not use real money with this system in its current state.
