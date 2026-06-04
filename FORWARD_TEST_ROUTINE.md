# Forward Test Routine / Acceptance Criteria

## 1. Overview

This document defines the routine and acceptance criteria for running Forward Dry-run / Paper Validation before considering demo execution.

The goal is to prove process discipline, record quality, safety behavior, and decision consistency over time. This is not a live trading guide.

## 2. Current Status: Paper Validation Only

Current status:

- Paper validation only.
- Not demo execution.
- Not live execution.
- Not real-money trading.
- No real order is sent.
- No MT5 order intent file is written.

Every run must show:

```text
No order was sent.
No MT5 order intent was written.
Forward dry-run only.
```

## 3. Goal of Forward Test Routine

The forward test routine is designed to answer practical questions:

- Does the system produce consistent records?
- Are approve/reject reasons understandable?
- Does the system reject unsafe conditions?
- Do journal and summary files update reliably?
- Does live mode stay blocked?
- Are there repeated data/config failures?
- Is the strategy behavior stable across sessions?

This routine is a gate before demo execution. It is not evidence that the system is ready for real money.

## 4. Recommended Test Duration

Minimum duration:

- At least 10 trading days.

Recommended duration:

- 2-4 weeks.

Recommended evidence size:

- At least 30-60 forward validation records before serious evaluation.

Do not progress to demo execution based on only a few successful runs.

## 5. Daily Routine

Run forward validation manually first.

Recommended daily frequency:

- 3 manual runs per trading day.
- Asia session: 1 run.
- London session: 1 run.
- NewYork session: 1 run.

Before each run:

- Check current price.
- Check spread.
- Check session.
- Check high-impact news risk.
- Set the news flag if high-impact news is nearby.

After each run, the console must show:

```text
No order was sent.
No MT5 order intent was written.
Forward dry-run only.
```

## 6. Session Schedule

Suggested session coverage:

| Session | Runs per day | Purpose |
|---|---:|---|
| Asia | 1 | Observe lower-liquidity behavior and avoid forced trades. |
| London | 1 | Observe high-liquidity trend/reversal behavior. |
| NewYork | 1 | Observe overlap, volatility, and post-news behavior. |

Avoid random over-testing. The point is consistent sampling, not forcing signals.

## 7. Before Each Run Checklist

- [ ] `FORWARD_VALIDATION_MODE` is `paper` or `demo`
- [ ] Current price is correct
- [ ] Spread is reasonable
- [ ] Session is correct
- [ ] High-impact news flag is set if needed
- [ ] Market data/config files are available
- [ ] Tests passed before the daily routine
- [ ] No known unresolved runtime error exists
- [ ] No order intent file exists

Order intent check on Windows:

```powershell
Test-Path .\trading_signal_order.csv
Test-Path .\logs\trading_signal_order.csv
```

Expected:

```text
False
False
```

## 8. After Each Run Checklist

- [ ] Console has `No order was sent.`
- [ ] Console has `No MT5 order intent was written.`
- [ ] Console has `Forward dry-run only.`
- [ ] `forward_records.csv` was updated
- [ ] `forward_records.jsonl` was updated
- [ ] `daily_summary.csv` was updated
- [ ] `weekly_summary.csv` was updated when applicable
- [ ] `order_sent` remains `False`
- [ ] `order_intent_written` remains `False`
- [ ] Approve/reject reasons are understandable

## 9. Weekly Review Routine

At the end of each trading week:

1. Review `weekly_summary.csv`.
2. Review `daily_summary.csv` for each day.
3. Review selected rows from `forward_records.csv`.
4. Check whether approved decisions were based on complete data.
5. Check whether rejects are explainable.
6. Check whether any safety violation occurred.
7. Check whether data/config failures repeated.
8. Compare behavior across Asia, London, and NewYork sessions.
9. Decide whether to continue paper validation, fix issues, or pause.

Weekly review should be manual and conservative.

## 10. Daily Metrics To Track

Track these daily:

- Total runs
- Approved count
- Rejected count
- No-trade count
- Execution reject count
- Risk reject count
- Paper intent count
- Journal failures
- Top reason
- Reason summary
- `order_sent_count`
- `order_intent_written_count`

Required safety values:

- `order_sent_count = 0`
- `order_intent_written_count = 0`

## 11. Weekly Metrics To Track

Track these weekly:

- Total runs
- Approved/rejected ratio
- No-trade frequency
- Execution reject frequency
- Risk reject frequency
- Journal failure count
- Top reason for the week
- Repeated failure patterns
- Session behavior differences
- Safety violation count

The weekly review should focus on consistency and risk control, not only approved count.

## 12. Acceptance Criteria Before Demo Execution

All criteria must pass before considering demo execution:

- Forward test completed for at least 2-4 weeks.
- Full tests passed before daily runs.
- `order_sent_count = 0`.
- `order_intent_written_count = 0`.
- No `trading_signal_order.csv`.
- No `logs/trading_signal_order.csv`.
- Journal / record writes succeeded consistently.
- `daily_summary.csv` was updated.
- `weekly_summary.csv` was updated.
- No repeated config/data errors.
- Reject reasons are explainable.
- Risk reject works according to rules.
- Live mode test rejects as `mode_validation`.
- Manual review confirms signals are not random entry guesses.

Passing these criteria means the system may be considered for a controlled demo execution design. It does not mean it is ready for real money.

## 13. Rejection Criteria

Do not progress to demo execution if any of these occur:

- An order intent file is found.
- `order_sent=True` is found.
- `order_intent_written=True` is found.
- Tests fail and runs continue anyway.
- Journal/summary writes fail repeatedly.
- Missing candle/config data occurs frequently.
- Approved decisions happen with incomplete data.
- Low-RR setups pass when they should be rejected.
- Execution policy fails to reject unsafe spread/news/session conditions.
- The routine is not stable yet.
- Review has not covered the full 2-4 week period.

One serious safety violation is enough to stop progression.

## 14. Manual Review Checklist

- [ ] รัน tests ผ่าน
- [ ] ใช้ paper/demo mode
- [ ] current price ถูกต้อง
- [ ] spread สมเหตุสมผล
- [ ] session ถูกต้อง
- [ ] ไม่มีข่าวแรงใกล้ประกาศ หรือใส่ news flag แล้ว
- [ ] console มี safety text ครบ
- [ ] forward record ถูกเขียน
- [ ] daily summary ถูกอัปเดต
- [ ] weekly summary ถูกอัปเดตเมื่อถึงรอบ review
- [ ] ไม่มี order intent file
- [ ] เหตุผล approve/reject อ่านแล้วสมเหตุสมผล

## 15. Incident / Safety Violation Checklist

- [ ] ถ้าเจอ `trading_signal_order.csv` ให้หยุดทันที
- [ ] ถ้า `order_sent_count > 0` ให้หยุดทันที
- [ ] ถ้า `order_intent_written_count > 0` ให้หยุดทันที
- [ ] ถ้า live mode ไม่ reject ให้หยุดทันที
- [ ] ถ้า summary ไม่ถูกเขียน ให้ตรวจ `forward_records.jsonl` ก่อนรันต่อ
- [ ] ถ้า tests fail ให้ห้าม forward validation รอบถัดไปจนกว่าแก้เสร็จ

Incident handling:

1. Stop running validation.
2. Preserve logs and records.
3. Identify the exact file/record/time.
4. Fix the issue in paper mode only.
5. Re-run tests.
6. Restart the forward validation window if the issue affects prior evidence.

## 16. How To Review forward_records.csv

Use `forward_records.csv` for row-by-row inspection.

Review:

- `timestamp`
- `symbol`
- `mode`
- `action`
- `stage`
- `approved`
- `reasons`
- `entry`
- `stop_loss`
- `tp1`
- `tp2`
- `risk_reward`
- `execution_plan_present`
- `risk_decision_present`
- `order_sent`
- `order_intent_written`
- `journal_success`

Important checks:

- `order_sent` must always be `False`.
- `order_intent_written` must always be `False`.
- Approved records must have sensible reasons and complete enough context.
- Rejected records must have understandable reasons.

## 17. How To Review daily_summary.csv

Use `daily_summary.csv` to check daily process quality.

Review:

- `total_runs`
- `approved_count`
- `rejected_count`
- `no_trade_count`
- `execution_reject_count`
- `risk_reject_count`
- `paper_intent_count`
- `journal_failures`
- `order_sent_count`
- `order_intent_written_count`
- `top_reason`
- `reason_summary`

Daily pass condition:

- No safety violation.
- Records were written successfully.
- Reasons are explainable.
- No repeated runtime/data issue.

## 18. How To Review weekly_summary.csv

Use `weekly_summary.csv` to evaluate consistency across multiple days.

Review:

- `week_start`
- `week_end`
- `iso_year`
- `iso_week`
- `total_runs`
- `approved_count`
- `rejected_count`
- `no_trade_count`
- `execution_reject_count`
- `risk_reject_count`
- `journal_failures`
- `order_sent_count`
- `order_intent_written_count`
- `top_reason`
- `reason_summary`

Weekly pass condition:

- No safety violation.
- No repeated critical failure.
- No unexplained approvals.
- Reject behavior is consistent with rules.
- Data/config reliability is acceptable.

## 19. Minimum Evidence Required Before Demo

Minimum evidence package:

- 2-4 weeks of forward validation.
- At least 30-60 records.
- Daily summaries for each testing day.
- Weekly summaries for each testing week.
- Manual review notes.
- Safety checks showing no order intent files.
- Full tests passing.
- Live mode reject evidence.
- Explanation of top reject reasons.
- Confirmation that approved setups were not random or incomplete.

Do not move to demo execution without this evidence.

## 20. What This Still Does NOT Prove

Forward dry-run / paper validation does not prove:

- Real broker execution quality.
- Real slippage behavior.
- Real spread behavior during volatile periods.
- Real order fill behavior.
- Partial fill handling.
- Rejected order handling.
- Broker position synchronization.
- Strategy profitability.
- Long-term robustness.

It only validates process, records, safety behavior, and dry-run decision consistency.

## 21. Not Ready For Real Money

This system is not ready for real-money trading.

It still lacks:

- Broker reconciliation
- Real position sync
- Order lifecycle tracking
- Partial fill handling
- Rejected order handling
- Live spread/news validation
- Emergency execution stop tested with a real broker
- Demo forward test that has actually passed

Even after this paper validation routine, the next step should only be controlled demo execution design, not real money.

This document is not personal financial advice. The user is responsible for all final decisions and risk.

## 22. Final Safety Statement

No order was sent.

No MT5 order intent was written.

Forward dry-run only.

Paper validation is not demo execution.

Demo execution is not real-money readiness.
