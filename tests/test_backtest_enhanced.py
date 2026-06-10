from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading_signal_bot.backtest import (
    BacktestCandidate,
    BacktestDailyRiskState,
    BacktestDecision,
    BacktestMetrics,
    BacktestReport,
    BacktestRealismConfig,
    BacktestTradeResult,
    BacktestRange,
    apply_backtest_money_result,
    backtest_risk_skip_decision,
    build_backtest_report_from_decisions,
    backtest_candidate_from_signal,
    calculate_backtest_position_size,
    calculate_backtest_cost_summary,
    calculate_backtest_metrics,
    calculate_session_metrics,
    calculate_backtest_trade_costs,
    calculate_session_pnl_summary,
    capture_backtest_decision_and_candidate,
    capture_backtest_decision,
    classify_session,
    evaluate_backtest_daily_risk_state,
    execution_timeframe_for_backtest,
    export_backtest_decisions_csv,
    export_backtest_report,
    export_backtest_session_summary_csv,
    export_backtest_summary_json,
    export_backtest_trades_csv,
    has_required_snapshot_candles,
    reset_backtest_daily_risk_state,
    reset_daily_risk_state_if_new_day,
    run_backtest,
    run_backtest_decision_capture,
    run_enhanced_backtest_report_with_simulation,
    run_enhanced_backtest_report_with_realism,
    run_enhanced_backtest_report,
    simulate_enhanced_trade,
    summarize_balance_performance,
    summarize_reject_reasons,
    summarize_skip_reasons,
    summarize_risk_skips,
    summarize_trade_performance,
    update_backtest_daily_risk_state_after_trade,
)
from trading_signal_bot.models import Candle, Confidence, Signal, SignalAction, SignalConfig, TradeLevels
from trading_signal_bot.multitimeframe import EXECUTION_TIMEFRAME


class BacktestEnhancedTest(unittest.TestCase):
    def test_creates_backtest_decision_with_all_fields(self) -> None:
        decision = _decision()

        self.assertEqual(decision.timestamp, "2026-05-18T07:00:00Z")
        self.assertEqual(decision.session, "London")
        self.assertEqual(decision.symbol, "XAUUSD")
        self.assertEqual(decision.timeframe, "M1")
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.stage, "execution_policy")
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reasons, ("Spread is above maximum allowed",))
        self.assertEqual(decision.htf_bias, "BULLISH")
        self.assertEqual(decision.execution_trend, "BULLISH")
        self.assertEqual(decision.price_location, "NEAR_DEMAND")
        self.assertEqual(decision.candle_confirmation_summary, "strong close")
        self.assertEqual(decision.risk_reward, 1.5)

    def test_creates_backtest_trade_result_with_all_fields(self) -> None:
        trade = _trade()

        self.assertEqual(trade.action, SignalAction.BUY)
        self.assertEqual(trade.session, "London")
        self.assertEqual(trade.entry_time, "2026-05-18T07:00:00Z")
        self.assertEqual(trade.exit_time, "2026-05-18T07:05:00Z")
        self.assertEqual(trade.entry, 100.0)
        self.assertEqual(trade.stop_loss, 99.0)
        self.assertEqual(trade.tp1, 101.0)
        self.assertEqual(trade.tp2, 102.0)
        self.assertEqual(trade.result, "WIN")
        self.assertEqual(trade.r_multiple, 1.5)
        self.assertEqual(trade.risk_reward, 1.5)
        self.assertEqual(trade.volume, 0.1)
        self.assertEqual(trade.pnl, 15.0)
        self.assertEqual(trade.balance_after, 1015.0)
        self.assertIsNone(trade.loss_reason)
        self.assertEqual(trade.reject_reasons_before_entry, ())

    def test_creates_backtest_metrics_with_all_fields(self) -> None:
        metrics = _metrics()

        self.assertEqual(metrics.total_trades, 3)
        self.assertEqual(metrics.approved_trades, 2)
        self.assertEqual(metrics.rejected_trades, 1)
        self.assertEqual(metrics.skipped_trades, 1)
        self.assertEqual(metrics.win_rate, 50.0)
        self.assertEqual(metrics.loss_rate, 50.0)
        self.assertEqual(metrics.profit_factor, 1.5)
        self.assertEqual(metrics.max_drawdown, 1.0)
        self.assertEqual(metrics.average_win, 1.5)
        self.assertEqual(metrics.average_loss, -1.0)
        self.assertEqual(metrics.average_rr, 1.5)
        self.assertEqual(metrics.max_consecutive_losses, 2)
        self.assertEqual(metrics.net_r, 0.5)

    def test_creates_backtest_report_with_all_fields(self) -> None:
        metrics = _metrics()
        report = BacktestReport(
            trades=(_trade(),),
            decisions=(_decision(),),
            metrics=metrics,
            session_metrics={"London": metrics},
            reject_reason_summary={"Spread is above maximum allowed": 1},
            skip_reason_summary={"insufficient candles": 1},
            stopped_reason=None,
        )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(len(report.decisions), 1)
        self.assertEqual(report.metrics, metrics)
        self.assertIn("London", report.session_metrics)
        self.assertEqual(report.reject_reason_summary["Spread is above maximum allowed"], 1)
        self.assertEqual(report.skip_reason_summary["insufficient candles"], 1)
        self.assertIsNone(report.stopped_reason)

    def test_classify_session_asia(self) -> None:
        self.assertEqual(classify_session(datetime(2026, 5, 18, 0, 0, tzinfo=UTC)), "Asia")
        self.assertEqual(classify_session(datetime(2026, 5, 18, 6, 59, tzinfo=UTC)), "Asia")

    def test_classify_session_london(self) -> None:
        self.assertEqual(classify_session(datetime(2026, 5, 18, 7, 0, tzinfo=UTC)), "London")
        self.assertEqual(classify_session(datetime(2026, 5, 18, 12, 59, tzinfo=UTC)), "London")

    def test_classify_session_new_york(self) -> None:
        self.assertEqual(classify_session(datetime(2026, 5, 18, 13, 0, tzinfo=UTC)), "NewYork")
        self.assertEqual(classify_session(datetime(2026, 5, 18, 20, 59, tzinfo=UTC)), "NewYork")

    def test_classify_session_other(self) -> None:
        self.assertEqual(classify_session(datetime(2026, 5, 18, 21, 0, tzinfo=UTC)), "Other")
        self.assertEqual(classify_session(datetime(2026, 5, 18, 23, 59, tzinfo=UTC)), "Other")

    def test_calculate_metrics_empty_inputs_are_zero(self) -> None:
        metrics = calculate_backtest_metrics([], [])

        self.assertEqual(metrics.total_trades, 0)
        self.assertEqual(metrics.approved_trades, 0)
        self.assertEqual(metrics.rejected_trades, 0)
        self.assertEqual(metrics.skipped_trades, 0)
        self.assertEqual(metrics.win_rate, 0.0)
        self.assertEqual(metrics.loss_rate, 0.0)
        self.assertEqual(metrics.profit_factor, 0.0)
        self.assertEqual(metrics.max_drawdown, 0.0)
        self.assertEqual(metrics.average_win, 0.0)
        self.assertEqual(metrics.average_loss, 0.0)
        self.assertEqual(metrics.average_rr, 0.0)
        self.assertEqual(metrics.max_consecutive_losses, 0)
        self.assertEqual(metrics.net_r, 0.0)

    def test_calculate_metrics_total_trades(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics.total_trades, 4)

    def test_calculate_metrics_approved_trades(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics.approved_trades, 2)

    def test_calculate_metrics_rejected_trades(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics.rejected_trades, 2)

    def test_calculate_metrics_skipped_trades(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics.skipped_trades, 1)

    def test_calculate_metrics_win_rate(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.win_rate, 50.0)

    def test_calculate_metrics_loss_rate(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.loss_rate, 50.0)

    def test_calculate_metrics_profit_factor(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.profit_factor, 1.75)

    def test_calculate_metrics_profit_factor_without_losses_is_safe(self) -> None:
        metrics = calculate_backtest_metrics([_trade(result="WIN", r_multiple=1.5)], [])

        self.assertEqual(metrics.profit_factor, 1.5)

    def test_calculate_metrics_max_drawdown_from_balance_after(self) -> None:
        metrics = calculate_backtest_metrics(
            [
                _trade(result="WIN", r_multiple=1.0, balance_after=1100.0),
                _trade(result="LOSS", r_multiple=-1.0, balance_after=1040.0),
                _trade(result="WIN", r_multiple=1.0, balance_after=1120.0),
            ],
            [],
        )

        self.assertEqual(metrics.max_drawdown, 60.0)

    def test_calculate_metrics_max_drawdown_falls_back_to_cumulative_r(self) -> None:
        metrics = calculate_backtest_metrics(
            [
                _trade(result="WIN", r_multiple=1.0, balance_after=None),
                _trade(result="LOSS", r_multiple=-1.0, balance_after=None),
                _trade(result="LOSS", r_multiple=-1.0, balance_after=None),
            ],
            [],
        )

        self.assertEqual(metrics.max_drawdown, 2.0)

    def test_calculate_metrics_average_win_and_loss(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.average_win, 1.75)
        self.assertAlmostEqual(metrics.average_loss, -1.0)

    def test_calculate_metrics_average_rr(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.average_rr, 1.5)

    def test_calculate_metrics_max_consecutive_losses(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics.max_consecutive_losses, 2)

    def test_calculate_metrics_net_r(self) -> None:
        metrics = calculate_backtest_metrics(_sample_trades(), _sample_decisions())

        self.assertAlmostEqual(metrics.net_r, 1.5)

    def test_summarize_reject_reasons_counts_rejects(self) -> None:
        summary = summarize_reject_reasons(_sample_decisions())

        self.assertEqual(summary["Spread is above maximum allowed"], 2)
        self.assertEqual(summary["Risk/reward is below minimum"], 1)
        self.assertNotIn("insufficient candles", summary)

    def test_summarize_skip_reasons_counts_skips(self) -> None:
        summary = summarize_skip_reasons(_sample_decisions())

        self.assertEqual(summary["insufficient candles"], 1)
        self.assertNotIn("Spread is above maximum allowed", summary)

    def test_calculate_session_metrics_splits_sessions(self) -> None:
        metrics_by_session = calculate_session_metrics(_sample_trades(), _sample_decisions())

        self.assertEqual(metrics_by_session["Asia"].total_trades, 1)
        self.assertEqual(metrics_by_session["London"].total_trades, 2)
        self.assertEqual(metrics_by_session["NewYork"].total_trades, 1)
        self.assertEqual(metrics_by_session["Other"].total_trades, 0)
        self.assertEqual(metrics_by_session["London"].rejected_trades, 2)

    def test_legacy_run_backtest_import_is_callable(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_does_not_create_mt5_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            calculate_backtest_metrics(_sample_trades(), _sample_decisions())

            self.assertFalse((base / "trading_signal_order.csv").exists())
            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_export_backtest_trades_csv_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_trades.csv"

            export_backtest_trades_csv(_sample_report(), path)

            self.assertTrue(path.exists())

    def test_backtest_trades_csv_has_expected_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_trades.csv"

            export_backtest_trades_csv(_sample_report(), path)

            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines()[0],
                "entry_time,exit_time,session,action,entry,stop_loss,tp1,tp2,result,r_multiple,"
                "risk_reward,volume,pnl,balance_after,loss_reason,reject_reasons_before_entry",
            )

    def test_backtest_trades_csv_has_trade_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_trades.csv"

            export_backtest_trades_csv(_sample_report(), path)

            row = _csv_rows(path)[0]
            self.assertEqual(row["entry_time"], "2026-05-18T07:00:00Z")
            self.assertEqual(row["session"], "Asia")
            self.assertEqual(row["action"], "BUY")

    def test_trade_reject_reasons_before_entry_serializes_readably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_trades.csv"
            report = BacktestReport(
                trades=(
                    _trade_with_reject_reasons(
                        reject_reasons_before_entry=("reason1", "reason2"),
                    ),
                ),
                decisions=(),
                metrics=calculate_backtest_metrics([], []),
                session_metrics={},
                reject_reason_summary={},
                skip_reason_summary={},
                stopped_reason=None,
            )

            export_backtest_trades_csv(report, path)

            self.assertEqual(_csv_rows(path)[0]["reject_reasons_before_entry"], "reason1 | reason2")

    def test_export_backtest_decisions_csv_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_decisions.csv"

            export_backtest_decisions_csv(_sample_report(), path)

            self.assertTrue(path.exists())

    def test_backtest_decisions_csv_has_expected_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_decisions.csv"

            export_backtest_decisions_csv(_sample_report(), path)

            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines()[0],
                "timestamp,session,symbol,timeframe,action,stage,approved,reasons,htf_bias,"
                "execution_trend,price_location,candle_confirmation_summary,risk_reward",
            )

    def test_backtest_decisions_csv_has_decision_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_decisions.csv"

            export_backtest_decisions_csv(_sample_report(), path)

            row = _csv_rows(path)[0]
            self.assertEqual(row["timestamp"], "2026-05-18T07:00:00Z")
            self.assertEqual(row["stage"], "approved")
            self.assertEqual(row["approved"], "True")

    def test_decision_reasons_serialize_readably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_decisions.csv"
            report = BacktestReport(
                trades=(),
                decisions=(_decision(reasons=("reason1", "reason2")),),
                metrics=calculate_backtest_metrics([], []),
                session_metrics={},
                reject_reason_summary={},
                skip_reason_summary={},
                stopped_reason=None,
            )

            export_backtest_decisions_csv(report, path)

            self.assertEqual(_csv_rows(path)[0]["reasons"], "reason1 | reason2")

    def test_export_backtest_session_summary_csv_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_session_summary.csv"

            export_backtest_session_summary_csv(_sample_report(), path)

            self.assertTrue(path.exists())

    def test_backtest_session_summary_csv_has_expected_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_session_summary.csv"

            export_backtest_session_summary_csv(_sample_report(), path)

            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines()[0],
                "session,total_trades,approved_trades,rejected_trades,skipped_trades,win_rate,loss_rate,"
                "profit_factor,max_drawdown,average_win,average_loss,average_rr,max_consecutive_losses,net_r",
            )

    def test_backtest_session_summary_csv_contains_sessions_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_session_summary.csv"

            export_backtest_session_summary_csv(_sample_report(), path)

            sessions = {row["session"] for row in _csv_rows(path)}
            self.assertEqual(sessions, {"Asia", "London", "NewYork", "Other"})

    def test_export_backtest_summary_json_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_summary.json"

            export_backtest_summary_json(_sample_report(), path)

            self.assertTrue(path.exists())

    def test_backtest_summary_json_parses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_summary.json"

            export_backtest_summary_json(_sample_report(), path)

            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_backtest_summary_json_has_metrics(self) -> None:
        payload = _summary_payload()

        self.assertIn("metrics", payload)
        self.assertEqual(payload["metrics"]["total_trades"], 4)

    def test_backtest_summary_json_has_session_metrics(self) -> None:
        payload = _summary_payload()

        self.assertIn("session_metrics", payload)
        self.assertIn("London", payload["session_metrics"])

    def test_backtest_summary_json_has_reject_reason_summary(self) -> None:
        payload = _summary_payload()

        self.assertIn("reject_reason_summary", payload)
        self.assertEqual(payload["reject_reason_summary"]["Spread is above maximum allowed"], 2)

    def test_backtest_summary_json_has_skip_reason_summary(self) -> None:
        payload = _summary_payload()

        self.assertIn("skip_reason_summary", payload)
        self.assertEqual(payload["skip_reason_summary"]["insufficient candles"], 1)

    def test_backtest_summary_json_has_stopped_reason(self) -> None:
        payload = _summary_payload()

        self.assertIn("stopped_reason", payload)
        self.assertIsNone(payload["stopped_reason"])

    def test_export_backtest_report_writes_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "nested" / "report"

            export_backtest_report(_sample_report(), output_dir)

            self.assertTrue((output_dir / "backtest_trades.csv").exists())
            self.assertTrue((output_dir / "backtest_decisions.csv").exists())
            self.assertTrue((output_dir / "backtest_session_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_summary.json").exists())

    def test_empty_backtest_report_exports_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "empty"

            export_backtest_report(_empty_report(), output_dir)

            self.assertTrue((output_dir / "backtest_trades.csv").exists())
            self.assertTrue((output_dir / "backtest_decisions.csv").exists())
            self.assertTrue((output_dir / "backtest_session_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_summary.json").exists())

    def test_export_creates_missing_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing" / "parents" / "backtest_summary.json"

            export_backtest_summary_json(_sample_report(), path)

            self.assertTrue(path.exists())

    def test_export_helpers_do_not_create_mt5_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "report"

            export_backtest_report(_sample_report(), output_dir)

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())
            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())

    def test_legacy_run_backtest_still_imports_after_export_helpers(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_execution_timeframe_for_backtest_uses_config_execution_timeframe(self) -> None:
        self.assertEqual(execution_timeframe_for_backtest(_signal_config()), "M1")

    def test_execution_timeframe_for_backtest_falls_back_to_legacy_constant(self) -> None:
        self.assertEqual(execution_timeframe_for_backtest(SimpleNamespace()), EXECUTION_TIMEFRAME)

    def test_has_required_snapshot_candles_false_without_execution_timeframe(self) -> None:
        self.assertFalse(has_required_snapshot_candles({"M5": _candles(5)}, _signal_config(min_candles=3)))

    def test_has_required_snapshot_candles_false_when_candles_insufficient(self) -> None:
        self.assertFalse(has_required_snapshot_candles({"M1": _candles(2)}, _signal_config(min_candles=3)))

    def test_has_required_snapshot_candles_true_when_candles_sufficient(self) -> None:
        self.assertTrue(has_required_snapshot_candles({"M1": _candles(3)}, _signal_config(min_candles=3)))

    def test_capture_decision_missing_m1_returns_market_data_stage(self) -> None:
        decision = capture_backtest_decision({"M5": _candles(5)}, _signal_config(), _dt(7))

        self.assertEqual(decision.stage, "market_data")
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reasons, ("missing execution timeframe candles",))

    def test_capture_decision_insufficient_candles(self) -> None:
        decision = capture_backtest_decision({"M1": _candles(2)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.stage, "insufficient_candles")
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reasons, ("insufficient candles",))

    def test_capture_decision_signal_error(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=ValueError("bad signal")):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.stage, "signal_error")
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reasons, ("bad signal",))

    def test_capture_decision_wait_signal_is_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.WAIT)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.stage, "skip")
        self.assertFalse(decision.approved)

    def test_wait_decision_uses_no_trade_reason(self) -> None:
        signal = _signal(SignalAction.WAIT, no_trade_reason="price is in the middle of the zone")
        with patch("trading_signal_bot.backtest.generate_signal", return_value=signal):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.reasons, ("price is in the middle of the zone",))

    def test_wait_decision_falls_back_when_reason_missing(self) -> None:
        signal = _signal(SignalAction.WAIT, no_trade_reason="")
        with patch("trading_signal_bot.backtest.generate_signal", return_value=signal):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.reasons, ("signal action is WAIT",))

    def test_capture_decision_buy_without_complete_levels_is_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY, entry=None)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.stage, "skip")
        self.assertFalse(decision.approved)

    def test_buy_without_complete_levels_reason_is_missing_trade_levels(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY, entry=None)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.reasons, ("missing trade levels",))

    def test_capture_decision_buy_with_complete_levels_is_signal_candidate(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertEqual(decision.stage, "signal_candidate")

    def test_signal_candidate_is_approved(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(7))

        self.assertTrue(decision.approved)
        self.assertEqual(decision.reasons, ())

    def test_backtest_decision_from_signal_maps_action_risk_session_and_timeframe(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.SELL)):
            decision = capture_backtest_decision({"M1": _candles(3)}, _signal_config(min_candles=3), _dt(13))

        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.risk_reward, 1.5)
        self.assertEqual(decision.session, "NewYork")
        self.assertEqual(decision.timeframe, "M1")

    def test_run_backtest_decision_capture_uses_m1_execution_timeframe(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decisions = run_backtest_decision_capture({"M1": _candles(4), "M5": _candles(4)}, _signal_config(min_candles=3))

        self.assertTrue(decisions)
        self.assertTrue(all(decision.timeframe == "M1" for decision in decisions))

    def test_run_backtest_decision_capture_skips_out_of_range_candles(self) -> None:
        backtest_range = BacktestRange(start=_dt(0, minute=2), end=_dt(0, minute=3), label="test range")
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decisions = run_backtest_decision_capture({"M1": _candles(5)}, _signal_config(min_candles=1), backtest_range)

        self.assertEqual(len(decisions), 2)

    def test_run_backtest_decision_capture_returns_tuple(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decisions = run_backtest_decision_capture({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertIsInstance(decisions, tuple)
        self.assertTrue(all(isinstance(decision, BacktestDecision) for decision in decisions))

    def test_run_backtest_decision_capture_does_not_create_trade_results(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decisions = run_backtest_decision_capture({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertTrue(decisions)
        self.assertFalse(any(isinstance(decision, BacktestTradeResult) for decision in decisions))

    def test_legacy_run_backtest_still_callable_after_decision_capture_helpers(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_decision_capture_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_backtest_decision_capture({"M1": _candles(3)}, _signal_config(min_candles=1))

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_decision_capture_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_backtest_decision_capture({"M1": _candles(3)}, _signal_config(min_candles=1))

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_build_report_from_decisions_returns_backtest_report(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertIsInstance(report, BacktestReport)

    def test_build_report_keeps_decisions(self) -> None:
        decisions = tuple(_sample_decisions())

        report = build_backtest_report_from_decisions(decisions)

        self.assertEqual(report.decisions, decisions)

    def test_build_report_defaults_trades_to_empty_tuple(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.trades, ())

    def test_build_report_keeps_provided_trades(self) -> None:
        trades = tuple(_sample_trades())

        report = build_backtest_report_from_decisions(tuple(_sample_decisions()), trades=trades)

        self.assertEqual(report.trades, trades)

    def test_build_report_default_stopped_reason_is_none(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertIsNone(report.stopped_reason)

    def test_build_report_keeps_stopped_reason(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()), stopped_reason="daily loss reached")

        self.assertEqual(report.stopped_reason, "daily loss reached")

    def test_build_report_metrics_count_approved_from_decisions(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.metrics.approved_trades, 2)

    def test_build_report_metrics_count_rejected_from_decisions(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.metrics.rejected_trades, 2)

    def test_build_report_metrics_count_skipped_from_decisions(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.metrics.skipped_trades, 1)

    def test_build_report_session_metrics_split_sessions(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.session_metrics["Asia"].approved_trades, 1)
        self.assertEqual(report.session_metrics["London"].rejected_trades, 2)
        self.assertEqual(report.session_metrics["NewYork"].skipped_trades, 1)

    def test_build_report_reject_reason_summary(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.reject_reason_summary["Spread is above maximum allowed"], 2)
        self.assertEqual(report.reject_reason_summary["Risk/reward is below minimum"], 1)

    def test_build_report_skip_reason_summary(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()))

        self.assertEqual(report.skip_reason_summary["insufficient candles"], 1)

    def test_build_report_accepts_empty_decisions(self) -> None:
        report = build_backtest_report_from_decisions(())

        self.assertEqual(report.decisions, ())
        self.assertEqual(report.metrics.approved_trades, 0)

    def test_build_report_accepts_empty_trades(self) -> None:
        report = build_backtest_report_from_decisions(tuple(_sample_decisions()), trades=())

        self.assertEqual(report.trades, ())
        self.assertEqual(report.metrics.total_trades, 0)

    def test_run_enhanced_backtest_report_returns_report(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
            report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertIsInstance(report, BacktestReport)

    def test_run_enhanced_backtest_report_uses_decision_capture(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())) as mocked:
            run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertEqual(mocked.call_count, 1)

    def test_run_enhanced_backtest_report_does_not_create_trade_results(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
            report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertEqual(report.trades, ())
        self.assertFalse(any(isinstance(item, BacktestTradeResult) for item in report.decisions))

    def test_run_enhanced_report_can_export_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "report"
            with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
                report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

            export_backtest_report(report, output_dir)

            self.assertTrue((output_dir / "backtest_trades.csv").exists())
            self.assertTrue((output_dir / "backtest_decisions.csv").exists())
            self.assertTrue((output_dir / "backtest_session_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_summary.json").exists())

    def test_legacy_run_backtest_still_callable_after_report_assembly(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_report_assembly_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            build_backtest_report_from_decisions(tuple(_sample_decisions()))

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_report_assembly_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            build_backtest_report_from_decisions(tuple(_sample_decisions()))

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_creates_backtest_candidate_with_all_fields(self) -> None:
        decision = _decision()
        candidate = BacktestCandidate(
            decision=decision,
            action=SignalAction.BUY,
            entry=100.0,
            stop_loss=99.0,
            tp1=None,
            tp2=101.5,
            risk_reward=1.5,
            signal_index=12,
        )

        self.assertEqual(candidate.decision, decision)
        self.assertEqual(candidate.action, SignalAction.BUY)
        self.assertEqual(candidate.entry, 100.0)
        self.assertEqual(candidate.stop_loss, 99.0)
        self.assertIsNone(candidate.tp1)
        self.assertEqual(candidate.tp2, 101.5)
        self.assertEqual(candidate.risk_reward, 1.5)
        self.assertEqual(candidate.signal_index, 12)

    def test_candidate_from_buy_signal(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY), _decision(), signal_index=5)

        self.assertEqual(candidate.action, SignalAction.BUY)
        self.assertEqual(candidate.entry, 100.0)
        self.assertEqual(candidate.stop_loss, 99.0)

    def test_candidate_from_sell_signal(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.SELL), _decision(), signal_index=5)

        self.assertEqual(candidate.action, SignalAction.SELL)

    def test_candidate_from_wait_signal_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "BUY or SELL"):
            backtest_candidate_from_signal(_signal(SignalAction.WAIT), _decision(), signal_index=5)

    def test_candidate_from_invalid_action_raises(self) -> None:
        signal = SimpleNamespace(action="HOLD", levels=TradeLevels(100.0, 99.0, 101.5, 1.5))

        with self.assertRaisesRegex(ValueError, "BUY or SELL"):
            backtest_candidate_from_signal(signal, _decision(), signal_index=5)

    def test_candidate_without_levels_raises(self) -> None:
        signal = SimpleNamespace(action=SignalAction.BUY, levels=None)

        with self.assertRaisesRegex(ValueError, "Signal levels are required"):
            backtest_candidate_from_signal(signal, _decision(), signal_index=5)

    def test_candidate_without_entry_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry is required"):
            backtest_candidate_from_signal(_signal(SignalAction.BUY, entry=None), _decision(), signal_index=5)

    def test_candidate_without_stop_loss_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop loss is required"):
            backtest_candidate_from_signal(_signal(SignalAction.BUY, stop_loss=None), _decision(), signal_index=5)

    def test_candidate_without_take_profit_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "take profit is required"):
            backtest_candidate_from_signal(_signal(SignalAction.BUY, take_profit=None), _decision(), signal_index=5)

    def test_candidate_maps_take_profit_to_tp2(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY, take_profit=102.5), _decision(), signal_index=5)

        self.assertEqual(candidate.tp2, 102.5)

    def test_candidate_tp1_is_none_for_legacy_signal(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY), _decision(), signal_index=5)

        self.assertIsNone(candidate.tp1)

    def test_candidate_h4_retest_uses_aggressive_breakeven_trigger_as_tp1(self) -> None:
        candidate = backtest_candidate_from_signal(
            _signal(SignalAction.BUY, strategy_name="H4 Zone Breakout Retest XAUUSD"),
            _decision(),
            signal_index=5,
        )

        self.assertEqual(candidate.tp1, 100.6)

    def test_candidate_uses_signal_level_risk_reward(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY, risk_reward=2.0), _decision(), signal_index=5)

        self.assertEqual(candidate.risk_reward, 2.0)

    def test_candidate_falls_back_to_decision_risk_reward(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY, risk_reward=None), _decision(), signal_index=5)

        self.assertEqual(candidate.risk_reward, 1.5)

    def test_candidate_keeps_signal_index(self) -> None:
        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY), _decision(), signal_index=42)

        self.assertEqual(candidate.signal_index, 42)

    def test_candidate_keeps_decision(self) -> None:
        decision = _decision()

        candidate = backtest_candidate_from_signal(_signal(SignalAction.BUY), decision, signal_index=5)

        self.assertEqual(candidate.decision, decision)

    def test_candidate_helper_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            backtest_candidate_from_signal(_signal(SignalAction.BUY), _decision(), signal_index=5)

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_candidate_helper_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            backtest_candidate_from_signal(_signal(SignalAction.BUY), _decision(), signal_index=5)

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_legacy_run_backtest_still_callable_after_candidate_schema(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_simulate_buy_hits_tp_returns_win(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 100, 101)])

        self.assertEqual(trade.result, "WIN")

    def test_simulate_buy_hits_sl_returns_loss(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.5, 98.5, 99)])

        self.assertEqual(trade.result, "LOSS")

    def test_simulate_buy_hits_tp_and_sl_same_candle_returns_loss_both_hit(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 98.5, 100)])

        self.assertEqual(trade.result, "LOSS_BOTH_HIT")

    def test_simulate_buy_open_at_end(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.8, 99.2, 100.5)])

        self.assertEqual(trade.result, "OPEN_AT_END")

    def test_simulate_sell_hits_tp_returns_win(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.SELL), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100, 98, 99)])

        self.assertEqual(trade.result, "WIN")

    def test_simulate_sell_hits_sl_returns_loss(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.SELL), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 101.5, 99.5, 101)])

        self.assertEqual(trade.result, "LOSS")

    def test_simulate_sell_hits_tp_and_sl_same_candle_returns_loss_both_hit(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.SELL), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 101.5, 98, 100)])

        self.assertEqual(trade.result, "LOSS_BOTH_HIT")

    def test_simulate_sell_open_at_end(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.SELL), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.8, 99.2, 99.5)])

        self.assertEqual(trade.result, "OPEN_AT_END")

    def test_simulation_prefers_tp2_when_available(self) -> None:
        candidate = _candidate(SignalAction.BUY, tp1=101.0, tp2=102.0)

        trade = simulate_enhanced_trade(candidate, [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 101.5, 99.5, 101)])

        self.assertEqual(trade.result, "OPEN_AT_END")
        self.assertEqual(trade.tp2, 102.0)

    def test_simulation_moves_to_breakeven_when_tp1_then_entry_hit(self) -> None:
        candidate = _candidate(SignalAction.BUY, tp1=100.6, tp2=102.0)

        trade = simulate_enhanced_trade(
            candidate,
            [
                _price_candle(0, 100, 100.5, 99.5, 100),
                _price_candle(1, 100, 100.8, 100.1, 100.7),
                _price_candle(2, 101, 101.1, 100.0, 100.2),
            ],
        )

        self.assertEqual(trade.result, "BREAKEVEN")
        self.assertEqual(trade.r_multiple, 0.0)

    def test_simulation_uses_tp1_when_tp2_missing(self) -> None:
        candidate = _candidate(SignalAction.BUY, tp1=101.0, tp2=None)

        trade = simulate_enhanced_trade(candidate, [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 101.2, 99.5, 101)])

        self.assertEqual(trade.result, "WIN")
        self.assertEqual(trade.tp2, 101.0)

    def test_simulation_without_tp1_and_tp2_raises(self) -> None:
        candidate = BacktestCandidate(
            decision=_decision(stage="signal_candidate", approved=True, reasons=()),
            action=SignalAction.BUY,
            entry=100.0,
            stop_loss=99.0,
            tp1=None,
            tp2=None,
            risk_reward=None,
            signal_index=0,
        )

        with self.assertRaisesRegex(ValueError, "Take profit target is required"):
            simulate_enhanced_trade(candidate, [_price_candle(0, 100, 101, 99, 100)])

    def test_simulation_empty_execution_candles_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Execution candles are required"):
            simulate_enhanced_trade(_candidate(SignalAction.BUY), [])

    def test_simulation_zero_risk_distance_raises(self) -> None:
        candidate = _candidate(SignalAction.BUY, entry=100.0, stop_loss=100.0)

        with self.assertRaisesRegex(ValueError, "Risk distance must be greater than zero"):
            simulate_enhanced_trade(candidate, [_price_candle(0, 100, 101, 99, 100)])

    def test_simulation_without_candle_after_signal_index_returns_open_at_end(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY, signal_index=1), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.5, 99.5, 100.25)])

        self.assertEqual(trade.result, "OPEN_AT_END")

    def test_win_r_multiple_is_positive_rr(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 99.5, 101)])

        self.assertEqual(trade.r_multiple, 2.0)

    def test_loss_r_multiple_is_minus_one(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.5, 98.5, 99)])

        self.assertEqual(trade.r_multiple, -1.0)

    def test_loss_both_hit_r_multiple_is_minus_one(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 98.5, 100)])

        self.assertEqual(trade.r_multiple, -1.0)

    def test_open_at_end_is_not_win_or_loss(self) -> None:
        trade = simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 100.8, 99.2, 100.5)])

        self.assertEqual(trade.result, "OPEN_AT_END")
        self.assertNotIn(trade.result, {"WIN", "LOSS", "LOSS_BOTH_HIT"})

    def test_simulation_helper_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 99.5, 101)])

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_simulation_helper_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            simulate_enhanced_trade(_candidate(SignalAction.BUY), [_price_candle(0, 100, 101, 99, 100), _price_candle(1, 100, 102, 99.5, 101)])

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_legacy_run_backtest_still_callable_after_simulation_helper(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_capture_decision_and_candidate_wait_returns_no_candidate(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.WAIT)):
            decision, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=2,
            )

        self.assertEqual(decision.stage, "skip")
        self.assertIsNone(candidate)

    def test_capture_decision_and_candidate_missing_levels_returns_no_candidate(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY, entry=None)):
            decision, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=2,
            )

        self.assertEqual(decision.stage, "skip")
        self.assertEqual(decision.reasons, ("missing trade levels",))
        self.assertIsNone(candidate)

    def test_capture_decision_and_candidate_buy_levels_create_candidate(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decision, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=2,
            )

        self.assertEqual(decision.stage, "signal_candidate")
        self.assertIsInstance(candidate, BacktestCandidate)
        self.assertEqual(candidate.action, SignalAction.BUY)

    def test_capture_decision_and_candidate_sell_levels_create_candidate(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.SELL)):
            decision, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=2,
            )

        self.assertEqual(decision.stage, "signal_candidate")
        self.assertIsInstance(candidate, BacktestCandidate)
        self.assertEqual(candidate.action, SignalAction.SELL)

    def test_capture_decision_and_candidate_keeps_signal_index(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            _, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=42,
            )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.signal_index, 42)

    def test_capture_decision_and_candidate_candidate_references_decision(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            decision, candidate = capture_backtest_decision_and_candidate(
                {"M1": _candles(3)},
                _signal_config(min_candles=3),
                _dt(7),
                signal_index=2,
            )

        self.assertIsNotNone(candidate)
        self.assertIs(candidate.decision, decision)

    def test_simulation_runner_returns_backtest_report(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertIsInstance(report, BacktestReport)

    def test_signal_candidate_is_simulated_into_trade_result(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertTrue(report.trades)
        self.assertIsInstance(report.trades[0], BacktestTradeResult)

    def test_simulation_runner_buy_win_is_in_report_trades(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades[0].action, SignalAction.BUY)
        self.assertEqual(report.trades[0].result, "WIN")

    def test_simulation_runner_sell_loss_is_in_report_trades(self) -> None:
        sell_signal = _signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)
        with patch("trading_signal_bot.backtest.generate_signal", return_value=sell_signal):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("sell_loss")},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades[0].action, SignalAction.SELL)
        self.assertEqual(report.trades[0].result, "LOSS")

    def test_simulation_runner_loss_both_hit_is_in_report_trades(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("both_hit")},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades[0].result, "LOSS_BOTH_HIT")

    def test_simulation_runner_open_at_end_is_in_report_trades(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("open_at_end")},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades[0].result, "OPEN_AT_END")

    def test_simulation_runner_metrics_total_trades_updates(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertGreater(report.metrics.total_trades, 0)

    def test_simulation_runner_metrics_net_r_reflects_trade_r_multiple(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertAlmostEqual(report.metrics.net_r, sum(trade.r_multiple for trade in report.trades))

    def test_simulation_runner_session_metrics_update_from_trades(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
            )

        self.assertGreater(report.session_metrics["Asia"].total_trades, 0)

    def test_simulation_runner_keeps_rejected_and_skipped_decisions(self) -> None:
        with patch(
            "trading_signal_bot.backtest.generate_signal",
            side_effect=[_signal(SignalAction.WAIT), _signal(SignalAction.BUY, entry=None)],
        ):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("open_at_end", count=2)},
                _signal_config(min_candles=1),
            )

        self.assertEqual([decision.stage for decision in report.decisions], ["skip", "skip"])
        self.assertEqual(report.trades, ())

    def test_simulation_runner_wait_decision_does_not_create_trade(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.WAIT)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("open_at_end", count=2)},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades, ())

    def test_simulation_runner_missing_levels_do_not_create_trade(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY, stop_loss=None)):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("open_at_end", count=2)},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades, ())

    def test_simulation_runner_missing_execution_candles_returns_market_data_decision(self) -> None:
        report = run_enhanced_backtest_report_with_simulation({"M5": _candles(2)}, _signal_config(min_candles=1))

        self.assertEqual(report.trades, ())
        self.assertEqual(report.decisions[0].stage, "market_data")

    def test_simulation_runner_records_simulation_error_decision(self) -> None:
        with patch(
            "trading_signal_bot.backtest.generate_signal",
            return_value=_signal(SignalAction.BUY, entry=100.0, stop_loss=100.0, take_profit=101.5),
        ):
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("open_at_end", count=2)},
                _signal_config(min_candles=1),
            )

        self.assertEqual(report.trades, ())
        self.assertIn("simulation_error", [decision.stage for decision in report.decisions])

    def test_simulation_runner_skips_to_after_exit_to_avoid_every_bar_stacking(self) -> None:
        with patch(
            "trading_signal_bot.backtest.generate_signal",
            side_effect=[_signal(SignalAction.BUY), _signal(SignalAction.WAIT)],
        ) as mocked:
            report = run_enhanced_backtest_report_with_simulation(
                {"M1": _runner_candles("buy_win", count=3)},
                _signal_config(min_candles=1),
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(mocked.call_count, 2)

    def test_legacy_run_backtest_still_callable_after_simulation_runner(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_decision_only_enhanced_report_still_does_not_simulate_trades(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
            report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertEqual(report.trades, ())

    def test_simulation_runner_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_enhanced_backtest_report_with_simulation(
                    {"M1": _runner_candles("buy_win")},
                    _signal_config(min_candles=1),
                )

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_simulation_runner_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_enhanced_backtest_report_with_simulation(
                    {"M1": _runner_candles("buy_win")},
                    _signal_config(min_candles=1),
                )

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_creates_backtest_realism_config_with_all_fields(self) -> None:
        realism = _realism_config()

        self.assertEqual(realism.initial_balance, 10000.0)
        self.assertEqual(realism.risk_percent, 1.0)
        self.assertEqual(realism.contract_size, 100.0)
        self.assertEqual(realism.min_volume, 0.01)
        self.assertEqual(realism.max_volume, 10.0)
        self.assertEqual(realism.volume_step, 0.01)
        self.assertTrue(realism.allow_min_volume)
        self.assertEqual(realism.spread_points, 20.0)
        self.assertEqual(realism.point_value, 0.01)
        self.assertEqual(realism.slippage_points, 5.0)
        self.assertEqual(realism.commission_per_lot, 7.0)
        self.assertEqual(realism.max_daily_loss_percent, 3.0)
        self.assertEqual(realism.max_consecutive_losses, 3)
        self.assertEqual(realism.cooldown_minutes, 30)
        self.assertEqual(realism.weekly_loss_pause_count, 0)
        self.assertEqual(realism.weekly_loss_pause_days, 0)

    def test_creates_backtest_daily_risk_state_with_all_fields(self) -> None:
        cooldown_until = _dt(8)
        state = BacktestDailyRiskState(
            date="2026-05-18",
            trades_today=3,
            losses_today=1,
            consecutive_losses=1,
            realized_loss_percent=1.0,
            cooldown_until=cooldown_until,
            stopped_for_day=False,
        )

        self.assertEqual(state.date, "2026-05-18")
        self.assertEqual(state.trades_today, 3)
        self.assertEqual(state.losses_today, 1)
        self.assertEqual(state.consecutive_losses, 1)
        self.assertEqual(state.realized_loss_percent, 1.0)
        self.assertEqual(state.cooldown_until, cooldown_until)
        self.assertFalse(state.stopped_for_day)

    def test_backtest_position_size_calculates_from_balance_risk_distance_and_contract(self) -> None:
        volume = calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config())

        self.assertEqual(volume, 1.0)

    def test_backtest_position_size_floors_to_volume_step(self) -> None:
        realism = _realism_config(volume_step=0.1)

        volume = calculate_backtest_position_size(10000.0, 100.0, 98.7, realism)

        self.assertEqual(volume, 0.7)

    def test_backtest_position_size_below_min_without_allow_min_raises(self) -> None:
        realism = _realism_config(min_volume=0.5, allow_min_volume=False)

        with self.assertRaisesRegex(ValueError, "below minimum volume"):
            calculate_backtest_position_size(1000.0, 100.0, 99.0, realism)

    def test_backtest_position_size_below_min_with_allow_min_returns_min_volume(self) -> None:
        realism = _realism_config(min_volume=0.5, allow_min_volume=True)

        volume = calculate_backtest_position_size(1000.0, 100.0, 99.0, realism)

        self.assertEqual(volume, 0.5)

    def test_backtest_position_size_caps_at_max_volume(self) -> None:
        realism = _realism_config(max_volume=0.5)

        volume = calculate_backtest_position_size(10000.0, 100.0, 99.0, realism)

        self.assertEqual(volume, 0.5)

    def test_backtest_position_size_zero_sl_distance_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Risk distance must be greater than zero"):
            calculate_backtest_position_size(10000.0, 100.0, 100.0, _realism_config())

    def test_backtest_position_size_zero_balance_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Balance must be greater than zero"):
            calculate_backtest_position_size(0.0, 100.0, 99.0, _realism_config())

    def test_backtest_position_size_zero_risk_percent_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Risk percent must be greater than zero"):
            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config(risk_percent=0.0))

    def test_backtest_position_size_zero_contract_size_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Contract size must be greater than zero"):
            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config(contract_size=0.0))

    def test_backtest_position_size_zero_volume_step_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Volume step must be greater than zero"):
            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config(volume_step=0.0))

    def test_backtest_position_size_min_greater_than_max_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Minimum volume must be lower than or equal to maximum volume"):
            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config(min_volume=2.0, max_volume=1.0))

    def test_backtest_position_size_has_no_martingale_multiplier_effect(self) -> None:
        realism = _realism_config()

        first = calculate_backtest_position_size(10000.0, 100.0, 99.0, realism)
        second = calculate_backtest_position_size(10000.0, 100.0, 99.0, realism)

        self.assertEqual(first, second)

    def test_backtest_trade_costs_calculate_commission(self) -> None:
        costs = calculate_backtest_trade_costs(2.0, _realism_config(commission_per_lot=7.0))

        self.assertEqual(costs["commission"], 14.0)

    def test_backtest_trade_costs_calculate_spread_cost(self) -> None:
        costs = calculate_backtest_trade_costs(2.0, _realism_config(spread_points=20.0, point_value=0.01, contract_size=100.0))

        self.assertEqual(costs["spread_cost"], 40.0)

    def test_backtest_trade_costs_calculate_slippage_cost(self) -> None:
        costs = calculate_backtest_trade_costs(2.0, _realism_config(slippage_points=5.0, point_value=0.01, contract_size=100.0))

        self.assertEqual(costs["slippage_cost"], 10.0)

    def test_backtest_trade_costs_calculate_total_cost(self) -> None:
        costs = calculate_backtest_trade_costs(
            2.0,
            _realism_config(
                commission_per_lot=7.0,
                spread_points=20.0,
                slippage_points=5.0,
                point_value=0.01,
                contract_size=100.0,
            ),
        )

        self.assertEqual(costs["total_cost"], 64.0)

    def test_backtest_trade_costs_negative_spread_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Spread points must be greater than or equal to zero"):
            calculate_backtest_trade_costs(1.0, _realism_config(spread_points=-1.0))

    def test_backtest_trade_costs_negative_slippage_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Slippage points must be greater than or equal to zero"):
            calculate_backtest_trade_costs(1.0, _realism_config(slippage_points=-1.0))

    def test_backtest_trade_costs_negative_commission_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Commission per lot must be greater than or equal to zero"):
            calculate_backtest_trade_costs(1.0, _realism_config(commission_per_lot=-1.0))

    def test_backtest_trade_costs_zero_volume_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Volume must be greater than zero"):
            calculate_backtest_trade_costs(0.0, _realism_config())

    def test_realism_helpers_do_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config())
            calculate_backtest_trade_costs(1.0, _realism_config())

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_realism_helpers_do_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            calculate_backtest_position_size(10000.0, 100.0, 99.0, _realism_config())
            calculate_backtest_trade_costs(1.0, _realism_config())

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_legacy_run_backtest_still_callable_after_realism_helpers(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_apply_money_result_fills_volume(self) -> None:
        result = apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

        self.assertEqual(result.volume, 1.0)

    def test_apply_money_result_calculates_pnl_from_gross_minus_costs(self) -> None:
        result = apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

        self.assertEqual(result.pnl, 168.0)

    def test_apply_money_result_calculates_balance_after(self) -> None:
        result = apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

        self.assertEqual(result.balance_after, 10168.0)

    def test_apply_money_result_win_has_positive_pnl_after_costs(self) -> None:
        result = apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

        self.assertGreater(result.pnl, 0)

    def test_apply_money_result_loss_has_negative_pnl_after_costs(self) -> None:
        result = apply_backtest_money_result(_money_trade("LOSS", -1.0), 10000.0, _realism_config())

        self.assertEqual(result.pnl, -132.0)
        self.assertLess(result.pnl, 0)

    def test_apply_money_result_loss_both_hit_is_conservative_negative(self) -> None:
        result = apply_backtest_money_result(_money_trade("LOSS_BOTH_HIT", -1.0), 10000.0, _realism_config())

        self.assertEqual(result.pnl, -132.0)
        self.assertLess(result.pnl, 0)

    def test_apply_money_result_open_at_end_uses_existing_r_multiple_and_costs(self) -> None:
        result = apply_backtest_money_result(_money_trade("OPEN_AT_END", 0.5), 10000.0, _realism_config())

        self.assertEqual(result.pnl, 18.0)

    def test_apply_money_result_zero_balance_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Balance must be greater than zero"):
            apply_backtest_money_result(_money_trade("WIN", 2.0), 0.0, _realism_config())

    def test_apply_money_result_zero_risk_distance_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Risk distance must be greater than zero"):
            apply_backtest_money_result(_money_trade("WIN", 2.0, stop_loss=100.0), 10000.0, _realism_config())

    def test_apply_money_result_does_not_mutate_original_trade(self) -> None:
        trade = _money_trade("WIN", 2.0)

        result = apply_backtest_money_result(trade, 10000.0, _realism_config())

        self.assertIsNone(trade.volume)
        self.assertIsNone(trade.pnl)
        self.assertIsNone(trade.balance_after)
        self.assertIsNot(result, trade)

    def test_apply_money_result_has_no_martingale_multiplier_effect(self) -> None:
        realism = _realism_config()

        first = apply_backtest_money_result(_money_trade("LOSS", -1.0), 10000.0, realism)
        second = apply_backtest_money_result(_money_trade("LOSS", -1.0), 10000.0, realism)

        self.assertEqual(first.volume, second.volume)

    def test_apply_money_result_volume_matches_position_size_helper(self) -> None:
        realism = _realism_config()
        expected_volume = calculate_backtest_position_size(10000.0, 100.0, 99.0, realism)

        result = apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, realism)

        self.assertEqual(result.volume, expected_volume)

    def test_apply_money_result_costs_match_cost_helper(self) -> None:
        realism = _realism_config()
        trade = _money_trade("WIN", 2.0)
        volume = calculate_backtest_position_size(10000.0, trade.entry, trade.stop_loss, realism)
        costs = calculate_backtest_trade_costs(volume, realism)

        result = apply_backtest_money_result(trade, 10000.0, realism)

        gross_pnl = trade.r_multiple * abs(trade.entry - trade.stop_loss) * realism.contract_size * volume
        self.assertEqual(result.pnl, gross_pnl - costs["total_cost"])

    def test_apply_money_result_pnl_can_be_worse_than_one_r_due_to_costs(self) -> None:
        result = apply_backtest_money_result(_money_trade("LOSS", -1.0), 10000.0, _realism_config())

        self.assertLess(result.pnl, -100.0)

    def test_apply_money_result_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_apply_money_result_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            apply_backtest_money_result(_money_trade("WIN", 2.0), 10000.0, _realism_config())

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_legacy_run_backtest_still_callable_after_apply_money_result(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_realism_runner_returns_backtest_report(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertIsInstance(report, BacktestReport)

    def test_realism_runner_missing_execution_candles_returns_market_data_decision(self) -> None:
        report = run_enhanced_backtest_report_with_realism({"M5": _candles(3)}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades, ())
        self.assertEqual(report.decisions[0].stage, "market_data")

    def test_realism_runner_signal_candidate_is_simulated_and_applies_money(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("buy_win")},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.decisions[0].stage, "signal_candidate")
        self.assertEqual(report.trades[0].result, "WIN")
        self.assertIsNotNone(report.trades[0].pnl)

    def test_realism_runner_trade_has_volume(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].volume, 1.0)

    def test_realism_runner_trade_has_pnl(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].pnl, 118.0)

    def test_realism_runner_trade_has_balance_after(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].balance_after, 10118.0)

    def test_realism_runner_second_trade_uses_first_balance_after(self) -> None:
        signals = [_signal(SignalAction.BUY), _signal(SignalAction.BUY)]
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=signals):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("two_buy_wins", count=4)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(len(report.trades), 2)
        self.assertEqual(report.trades[0].balance_after, 10118.0)
        self.assertGreater(report.trades[1].balance_after, report.trades[0].balance_after)

    def test_realism_runner_win_pnl_after_cost_is_positive(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertGreater(report.trades[0].pnl, 0)

    def test_realism_runner_loss_pnl_after_cost_is_negative(self) -> None:
        sell_signal = _signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)
        with patch("trading_signal_bot.backtest.generate_signal", return_value=sell_signal):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("sell_loss")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].result, "LOSS")
        self.assertLess(report.trades[0].pnl, 0)

    def test_realism_runner_loss_both_hit_keeps_negative_pnl(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("both_hit")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].result, "LOSS_BOTH_HIT")
        self.assertLess(report.trades[0].pnl, 0)

    def test_realism_runner_open_at_end_uses_existing_r_multiple_for_pnl(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("open_at_end")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades[0].result, "OPEN_AT_END")
        self.assertAlmostEqual(report.trades[0].r_multiple, 0.6)
        self.assertAlmostEqual(report.trades[0].pnl, 28.0)

    def test_realism_runner_metrics_total_trades_updates(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertGreater(report.metrics.total_trades, 0)

    def test_realism_runner_metrics_max_drawdown_uses_balance_after(self) -> None:
        signals = [_signal(SignalAction.BUY), _signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=signals):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("win_then_sell_loss", count=4)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertGreater(report.metrics.max_drawdown, 0)

    def test_realism_runner_session_metrics_include_realism_trade(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertGreater(report.session_metrics["Asia"].total_trades, 0)

    def test_realism_runner_records_realism_error_decision(self) -> None:
        realism = _realism_config(initial_balance=0.0)
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), realism)

        self.assertEqual(report.trades, ())
        self.assertIn("realism_error", [decision.stage for decision in report.decisions])

    def test_realism_runner_records_simulation_error_decision(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY, stop_loss=100.0)):
            report = run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

        self.assertEqual(report.trades, ())
        self.assertIn("simulation_error", [decision.stage for decision in report.decisions])

    def test_simulation_runner_behavior_unchanged_after_realism_runner(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1))

        self.assertIsNone(report.trades[0].pnl)
        self.assertIsNone(report.trades[0].balance_after)

    def test_decision_only_runner_behavior_unchanged_after_realism_runner(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
            report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertEqual(report.trades, ())

    def test_legacy_run_backtest_still_callable_after_realism_runner(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_realism_runner_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_realism_runner_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
                run_enhanced_backtest_report_with_realism({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1), _realism_config())

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_backtest_module_does_not_import_auto_trade_for_realism_runner(self) -> None:
        self.assertNotIn("auto_trade", Path("src/trading_signal_bot/backtest.py").read_text(encoding="utf-8"))

    def test_reset_daily_risk_state_uses_current_date(self) -> None:
        state = reset_backtest_daily_risk_state(_dt(7))

        self.assertEqual(state.date, "2026-05-18")

    def test_reset_daily_risk_state_starts_with_zero_values(self) -> None:
        state = reset_backtest_daily_risk_state(_dt(7))

        self.assertEqual(state.trades_today, 0)
        self.assertEqual(state.losses_today, 0)
        self.assertEqual(state.consecutive_losses, 0)
        self.assertEqual(state.realized_loss_percent, 0.0)
        self.assertIsNone(state.cooldown_until)
        self.assertFalse(state.stopped_for_day)

    def test_reset_daily_risk_state_if_same_day_returns_existing_state(self) -> None:
        state = _daily_state(date="2026-05-18", trades_today=2)

        result = reset_daily_risk_state_if_new_day(state, _dt(9))

        self.assertIs(result, state)

    def test_reset_daily_risk_state_if_new_day_resets_state(self) -> None:
        state = _daily_state(date="2026-05-17", trades_today=2, losses_today=2, stopped_for_day=True)

        result = reset_daily_risk_state_if_new_day(state, _dt(9))

        self.assertEqual(result.date, "2026-05-18")
        self.assertEqual(result.trades_today, 0)
        self.assertEqual(result.losses_today, 0)

    def test_new_day_reset_clears_cooldown(self) -> None:
        state = _daily_state(date="2026-05-17", cooldown_until=_dt(8))

        result = reset_daily_risk_state_if_new_day(state, _dt(9))

        self.assertIsNone(result.cooldown_until)

    def test_new_day_reset_clears_stopped_for_day(self) -> None:
        state = _daily_state(date="2026-05-17", stopped_for_day=True)

        result = reset_daily_risk_state_if_new_day(state, _dt(9))

        self.assertFalse(result.stopped_for_day)

    def test_evaluate_daily_risk_stopped_for_day_skips(self) -> None:
        should_skip, reasons = evaluate_backtest_daily_risk_state(_daily_state(stopped_for_day=True), _dt(7), _realism_config())

        self.assertTrue(should_skip)
        self.assertIn("daily risk stopped for day", reasons)

    def test_evaluate_daily_risk_max_daily_loss_skips(self) -> None:
        should_skip, reasons = evaluate_backtest_daily_risk_state(_daily_state(realized_loss_percent=3.0), _dt(7), _realism_config())

        self.assertTrue(should_skip)
        self.assertIn("max daily loss reached", reasons)

    def test_evaluate_daily_risk_max_consecutive_losses_skips(self) -> None:
        should_skip, reasons = evaluate_backtest_daily_risk_state(_daily_state(consecutive_losses=3), _dt(7), _realism_config())

        self.assertTrue(should_skip)
        self.assertIn("max consecutive losses reached", reasons)

    def test_evaluate_daily_risk_active_cooldown_skips(self) -> None:
        should_skip, reasons = evaluate_backtest_daily_risk_state(_daily_state(cooldown_until=_dt(8)), _dt(7), _realism_config())

        self.assertTrue(should_skip)
        self.assertIn("cooldown active", reasons)

    def test_evaluate_daily_risk_without_blocks_allows_trade(self) -> None:
        should_skip, reasons = evaluate_backtest_daily_risk_state(_daily_state(), _dt(7), _realism_config())

        self.assertFalse(should_skip)
        self.assertEqual(reasons, ())

    def test_update_daily_risk_win_increments_trades_and_resets_consecutive_losses(self) -> None:
        state = _daily_state(consecutive_losses=2)

        result = update_backtest_daily_risk_state_after_trade(state, _risk_trade("WIN", 100.0), _dt(7), _realism_config())

        self.assertEqual(result.trades_today, 1)
        self.assertEqual(result.consecutive_losses, 0)

    def test_update_daily_risk_win_clears_cooldown(self) -> None:
        state = _daily_state(cooldown_until=_dt(8))

        result = update_backtest_daily_risk_state_after_trade(state, _risk_trade("WIN", 100.0), _dt(7), _realism_config())

        self.assertIsNone(result.cooldown_until)

    def test_update_daily_risk_loss_increments_trades(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.trades_today, 1)

    def test_update_daily_risk_loss_increments_losses_today(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.losses_today, 1)

    def test_update_daily_risk_loss_increments_consecutive_losses(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(consecutive_losses=1), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.consecutive_losses, 2)

    def test_update_daily_risk_loss_increments_realized_loss_percent(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.realized_loss_percent, 1.0)

    def test_update_daily_risk_loss_sets_cooldown(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.cooldown_until, _dt(7) + timedelta(minutes=30))

    def test_update_daily_risk_zero_cooldown_does_not_set_cooldown(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(
            _daily_state(),
            _risk_trade("LOSS", -100.0),
            _dt(7),
            _realism_config(cooldown_minutes=0),
        )

        self.assertIsNone(result.cooldown_until)

    def test_update_daily_risk_loss_both_hit_counts_as_loss(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS_BOTH_HIT", -100.0), _dt(7), _realism_config())

        self.assertEqual(result.losses_today, 1)
        self.assertEqual(result.consecutive_losses, 1)

    def test_update_daily_risk_negative_pnl_counts_as_loss_even_if_result_not_loss(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("OPEN_AT_END", -25.0), _dt(7), _realism_config())

        self.assertEqual(result.losses_today, 1)

    def test_update_daily_risk_requires_pnl(self) -> None:
        with self.assertRaisesRegex(ValueError, "Trade pnl is required"):
            update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("WIN", None), _dt(7), _realism_config())

    def test_update_daily_risk_daily_loss_limit_stops_for_day(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(realized_loss_percent=2.5), _risk_trade("LOSS", -50.0), _dt(7), _realism_config())

        self.assertTrue(result.stopped_for_day)

    def test_update_daily_risk_drawdown_pause_sets_multi_day_cooldown(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(
            _daily_state(),
            _risk_trade("LOSS", -500.0),
            _dt(7),
            _realism_config(drawdown_pause_percent=5.0, drawdown_pause_days=7),
        )

        self.assertEqual(result.cooldown_until, _dt(7) + timedelta(days=7))
        self.assertTrue(result.stopped_for_day)

    def test_new_day_reset_preserves_active_drawdown_pause(self) -> None:
        state = _daily_state(cooldown_until=_dt(7) + timedelta(days=7), stopped_for_day=True, peak_balance=12000.0)

        result = reset_daily_risk_state_if_new_day(state, _dt(7) + timedelta(days=1))

        self.assertEqual(result.cooldown_until, _dt(7) + timedelta(days=7))
        self.assertTrue(result.stopped_for_day)
        self.assertEqual(result.peak_balance, 12000.0)

    def test_update_daily_risk_drawdown_pause_uses_peak_balance(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(
            _daily_state(peak_balance=12000.0),
            _risk_trade_with_balance("LOSS", pnl=-100.0, balance_after=11200.0),
            _dt(7),
            _realism_config(drawdown_pause_percent=5.0, drawdown_pause_days=7),
        )

        self.assertEqual(result.cooldown_until, _dt(7) + timedelta(days=7))
        self.assertTrue(result.stopped_for_day)
        self.assertEqual(result.peak_balance, 12000.0)

    def test_update_daily_risk_weekly_loss_pause_sets_multi_day_cooldown(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(
            _daily_state(weekly_consecutive_losses=1),
            _risk_trade("LOSS", -5.0),
            _dt(7),
            _realism_config(weekly_loss_pause_count=2, weekly_loss_pause_days=7),
        )

        self.assertEqual(result.weekly_consecutive_losses, 2)
        self.assertEqual(result.cooldown_until, _dt(7) + timedelta(days=7))
        self.assertTrue(result.stopped_for_day)

    def test_new_day_reset_preserves_weekly_loss_count_in_same_week(self) -> None:
        state = _daily_state(weekly_consecutive_losses=1, week_key="2026-W21")

        result = reset_daily_risk_state_if_new_day(state, _dt(7) + timedelta(days=1))

        self.assertEqual(result.weekly_consecutive_losses, 1)
        self.assertEqual(result.week_key, "2026-W21")

    def test_new_week_reset_clears_weekly_loss_count(self) -> None:
        state = _daily_state(weekly_consecutive_losses=1, week_key="2026-W21")

        result = reset_daily_risk_state_if_new_day(state, datetime(2026, 5, 25, tzinfo=UTC))

        self.assertEqual(result.weekly_consecutive_losses, 0)
        self.assertEqual(result.week_key, "2026-W22")

    def test_update_daily_risk_consecutive_loss_limit_stops_for_day(self) -> None:
        result = update_backtest_daily_risk_state_after_trade(_daily_state(consecutive_losses=2), _risk_trade("LOSS", -50.0), _dt(7), _realism_config())

        self.assertTrue(result.stopped_for_day)

    def test_backtest_risk_skip_decision_stage_is_risk_skip(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        self.assertEqual(decision.stage, "risk_skip")

    def test_backtest_risk_skip_decision_is_not_approved(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        self.assertFalse(decision.approved)

    def test_backtest_risk_skip_decision_action_is_none(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        self.assertIsNone(decision.action)

    def test_backtest_risk_skip_decision_keeps_reasons(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        self.assertEqual(decision.reasons, ("cooldown active",))

    def test_backtest_risk_skip_decision_uses_session(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(14), ("cooldown active",))

        self.assertEqual(decision.session, "NewYork")

    def test_backtest_risk_skip_decision_uses_execution_timeframe(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        self.assertEqual(decision.timeframe, "M1")

    def test_risk_skip_counts_as_skip_decision_in_metrics(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        metrics = calculate_backtest_metrics([], [decision])

        self.assertEqual(metrics.skipped_trades, 1)

    def test_summarize_skip_reasons_counts_risk_skip_reason(self) -> None:
        decision = backtest_risk_skip_decision(_signal_config(), _dt(7), ("cooldown active",))

        summary = summarize_skip_reasons([decision])

        self.assertEqual(summary["cooldown active"], 1)

    def test_legacy_run_backtest_still_callable_after_daily_risk_helpers(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_daily_risk_helpers_do_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_daily_risk_helpers_do_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            update_backtest_daily_risk_state_after_trade(_daily_state(), _risk_trade("LOSS", -100.0), _dt(7), _realism_config())

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_realism_runner_creates_risk_skip_when_cooldown_active(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertIn("risk_skip", [decision.stage for decision in report.decisions])
        self.assertIn("cooldown active", report.skip_reason_summary)

    def test_realism_runner_risk_skip_does_not_create_trade(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.metrics.skipped_trades, 1)

    def test_realism_runner_max_daily_loss_creates_risk_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(max_daily_loss_percent=1.0, cooldown_minutes=0, max_consecutive_losses=99),
            )

        self.assertIn("max daily loss reached", report.skip_reason_summary)

    def test_realism_runner_max_consecutive_losses_creates_risk_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(cooldown_minutes=0, max_daily_loss_percent=99.0, max_consecutive_losses=1),
            )

        self.assertIn("max consecutive losses reached", report.skip_reason_summary)

    def test_realism_runner_daily_reset_allows_next_day_trade(self) -> None:
        signals = [
            _signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0),
            _signal(SignalAction.BUY),
        ]
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=signals):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _risk_runner_candles_across_days()},
                _signal_config(min_candles=1),
                _realism_config(cooldown_minutes=60, max_daily_loss_percent=1.0, max_consecutive_losses=1),
            )

        self.assertEqual(len(report.trades), 2)
        self.assertEqual(report.trades[0].result, "LOSS")
        self.assertEqual(report.trades[1].result, "WIN")

    def test_realism_runner_cooldown_starts_from_exit_time(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(cooldown_minutes=30),
            )

        risk_skip = [decision for decision in report.decisions if decision.stage == "risk_skip"][0]
        self.assertEqual(risk_skip.timestamp, "2026-05-18T00:02:00+00:00")

    def test_realism_runner_loss_both_hit_triggers_cooldown_and_consecutive_loss(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.BUY)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("both_hit", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.trades[0].result, "LOSS_BOTH_HIT")
        self.assertIn("cooldown active", report.skip_reason_summary)

    def test_realism_runner_negative_open_at_end_counts_as_loss(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.BUY)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("negative_open_at_end", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.trades[0].result, "OPEN_AT_END")
        self.assertLess(report.trades[0].pnl, 0)

    def test_realism_runner_skip_reason_summary_counts_cooldown_active(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.skip_reason_summary["cooldown active"], 1)

    def test_realism_runner_skip_reason_summary_counts_max_daily_loss(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(max_daily_loss_percent=1.0, cooldown_minutes=0, max_consecutive_losses=99),
            )

        self.assertEqual(report.skip_reason_summary["max daily loss reached"], 1)

    def test_realism_runner_skip_reason_summary_counts_max_consecutive_losses(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(cooldown_minutes=0, max_daily_loss_percent=99.0, max_consecutive_losses=1),
            )

        self.assertEqual(report.skip_reason_summary["max consecutive losses reached"], 1)

    def test_realism_runner_metrics_skipped_trades_counts_risk_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.metrics.skipped_trades, 1)

    def test_realism_runner_total_trades_does_not_increase_from_risk_skip(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        self.assertEqual(report.metrics.total_trades, 1)

    def test_realism_runner_risk_skip_decision_stage(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        risk_skip = [decision for decision in report.decisions if decision.stage == "risk_skip"][0]
        self.assertEqual(risk_skip.stage, "risk_skip")

    def test_realism_runner_risk_skip_decision_not_approved(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        risk_skip = [decision for decision in report.decisions if decision.stage == "risk_skip"][0]
        self.assertFalse(risk_skip.approved)

    def test_realism_runner_risk_skip_decision_action_none(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        risk_skip = [decision for decision in report.decisions if decision.stage == "risk_skip"][0]
        self.assertIsNone(risk_skip.action)

    def test_realism_runner_risk_skip_decision_session_and_timeframe(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
            report = run_enhanced_backtest_report_with_realism(
                {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                _signal_config(min_candles=1),
                _realism_config(),
            )

        risk_skip = [decision for decision in report.decisions if decision.stage == "risk_skip"][0]
        self.assertEqual(risk_skip.session, "Asia")
        self.assertEqual(risk_skip.timeframe, "M1")

    def test_simulation_runner_still_has_no_money_fields_after_daily_risk_integration(self) -> None:
        with patch("trading_signal_bot.backtest.generate_signal", return_value=_signal(SignalAction.BUY)):
            report = run_enhanced_backtest_report_with_simulation({"M1": _runner_candles("buy_win")}, _signal_config(min_candles=1))

        self.assertIsNone(report.trades[0].pnl)
        self.assertIsNone(report.trades[0].balance_after)

    def test_decision_only_runner_still_has_no_trades_after_daily_risk_integration(self) -> None:
        with patch("trading_signal_bot.backtest.run_backtest_decision_capture", return_value=tuple(_sample_decisions())):
            report = run_enhanced_backtest_report({"M1": _candles(3)}, _signal_config(min_candles=1))

        self.assertEqual(report.trades, ())

    def test_legacy_run_backtest_still_callable_after_daily_risk_runner(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_daily_risk_runner_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
                run_enhanced_backtest_report_with_realism(
                    {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                    _signal_config(min_candles=1),
                    _realism_config(),
                )

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_daily_risk_runner_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("trading_signal_bot.backtest.generate_signal", side_effect=[_signal(SignalAction.SELL, stop_loss=101.0, take_profit=98.0, risk_reward=2.0)]):
                run_enhanced_backtest_report_with_realism(
                    {"M1": _runner_candles("sell_loss_then_wait", count=3)},
                    _signal_config(min_candles=1),
                    _realism_config(),
                )

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_backtest_module_still_does_not_import_auto_trade_after_daily_risk_runner(self) -> None:
        self.assertNotIn("auto_trade", Path("src/trading_signal_bot/backtest.py").read_text(encoding="utf-8"))

    def test_balance_summary_uses_latest_balance_after_as_final_balance(self) -> None:
        summary = summarize_balance_performance(_summary_helper_report(), 10000.0)

        self.assertEqual(summary["final_balance"], 10150.0)

    def test_balance_summary_without_balance_after_uses_initial_balance(self) -> None:
        report = build_backtest_report_from_decisions((), (_trade(balance_after=None),))

        summary = summarize_balance_performance(report, 10000.0)

        self.assertEqual(summary["final_balance"], 10000.0)

    def test_balance_summary_net_pnl(self) -> None:
        summary = summarize_balance_performance(_summary_helper_report(), 10000.0)

        self.assertEqual(summary["net_pnl"], 150.0)

    def test_balance_summary_return_percent(self) -> None:
        summary = summarize_balance_performance(_summary_helper_report(), 10000.0)

        self.assertEqual(summary["return_percent"], 1.5)

    def test_balance_summary_uses_metrics_max_drawdown(self) -> None:
        summary = summarize_balance_performance(_summary_helper_report(), 10000.0)

        self.assertEqual(summary["max_drawdown"], _summary_helper_report().metrics.max_drawdown)

    def test_balance_summary_invalid_initial_balance_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Initial balance must be greater than zero"):
            summarize_balance_performance(_summary_helper_report(), 0.0)

    def test_trade_summary_counts_total_trades(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertEqual(summary["total_trades"], 4)

    def test_trade_summary_counts_wins(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertEqual(summary["wins"], 1)

    def test_trade_summary_counts_losses(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertEqual(summary["losses"], 1)

    def test_trade_summary_counts_loss_both_hit(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertEqual(summary["loss_both_hit"], 1)

    def test_trade_summary_counts_open_at_end(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertEqual(summary["open_at_end"], 1)

    def test_trade_summary_win_rate(self) -> None:
        summary = summarize_trade_performance(_summary_helper_report())

        self.assertAlmostEqual(summary["win_rate"], 33.33333333333333)

    def test_trade_summary_uses_metrics_profit_factor(self) -> None:
        report = _summary_helper_report()

        summary = summarize_trade_performance(report)

        self.assertEqual(summary["profit_factor"], report.metrics.profit_factor)

    def test_trade_summary_uses_metrics_net_r(self) -> None:
        report = _summary_helper_report()

        summary = summarize_trade_performance(report)

        self.assertEqual(summary["net_r"], report.metrics.net_r)

    def test_trade_summary_uses_metric_averages(self) -> None:
        report = _summary_helper_report()

        summary = summarize_trade_performance(report)

        self.assertEqual(summary["average_win"], report.metrics.average_win)
        self.assertEqual(summary["average_loss"], report.metrics.average_loss)
        self.assertEqual(summary["average_rr"], report.metrics.average_rr)

    def test_risk_skip_summary_counts_cooldown_active(self) -> None:
        summary = summarize_risk_skips(_risk_skip_summary_report())

        self.assertEqual(summary["cooldown active"], 2)

    def test_risk_skip_summary_counts_daily_stop(self) -> None:
        summary = summarize_risk_skips(_risk_skip_summary_report())

        self.assertEqual(summary["daily risk stopped for day"], 1)

    def test_risk_skip_summary_counts_max_daily_loss(self) -> None:
        summary = summarize_risk_skips(_risk_skip_summary_report())

        self.assertEqual(summary["max daily loss reached"], 1)

    def test_risk_skip_summary_counts_max_consecutive_losses(self) -> None:
        summary = summarize_risk_skips(_risk_skip_summary_report())

        self.assertEqual(summary["max consecutive losses reached"], 1)

    def test_risk_skip_summary_ignores_non_risk_skip_reasons(self) -> None:
        summary = summarize_risk_skips(_risk_skip_summary_report())

        self.assertNotIn("insufficient candles", summary)

    def test_risk_skip_summary_without_risk_skips_is_empty(self) -> None:
        self.assertEqual(summarize_risk_skips(_sample_report()), {})

    def test_session_pnl_summary_has_all_sessions(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(set(summary), {"Asia", "London", "NewYork", "Other"})

    def test_session_pnl_summary_counts_trades(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["trades"], 2)
        self.assertEqual(summary["London"]["trades"], 1)

    def test_session_pnl_summary_counts_wins_and_losses(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["wins"], 1)
        self.assertEqual(summary["Asia"]["losses"], 1)

    def test_session_pnl_summary_net_pnl(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["net_pnl"], 50.0)

    def test_session_pnl_summary_average_pnl(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["average_pnl"], 25.0)

    def test_session_pnl_summary_win_rate(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["win_rate"], 50.0)

    def test_session_pnl_summary_net_r(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Asia"]["net_r"], 0.5)

    def test_session_pnl_summary_empty_session_is_zero(self) -> None:
        summary = calculate_session_pnl_summary(tuple(_session_pnl_trades()))

        self.assertEqual(summary["Other"]["trades"], 0)
        self.assertEqual(summary["Other"]["net_pnl"], 0)
        self.assertEqual(summary["Other"]["average_pnl"], 0.0)
        self.assertEqual(summary["Other"]["win_rate"], 0.0)

    def test_cost_summary_total_commission(self) -> None:
        summary = calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

        self.assertEqual(summary["total_commission"], 21.0)

    def test_cost_summary_total_spread_cost(self) -> None:
        summary = calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

        self.assertEqual(summary["total_spread_cost"], 60.0)

    def test_cost_summary_total_slippage_cost(self) -> None:
        summary = calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

        self.assertEqual(summary["total_slippage_cost"], 15.0)

    def test_cost_summary_total_cost(self) -> None:
        summary = calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

        self.assertEqual(summary["total_cost"], 96.0)

    def test_cost_summary_skips_trades_without_volume(self) -> None:
        summary = calculate_backtest_cost_summary((_trade(volume=None),), _realism_config())

        self.assertEqual(summary["total_cost"], 0.0)

    def test_cost_summary_without_any_volume_is_zero(self) -> None:
        summary = calculate_backtest_cost_summary((_trade(volume=None), _trade(volume=None)), _realism_config())

        self.assertEqual(
            summary,
            {
                "total_commission": 0.0,
                "total_spread_cost": 0.0,
                "total_slippage_cost": 0.0,
                "total_cost": 0.0,
            },
        )

    def test_legacy_run_backtest_still_callable_after_summary_helpers(self) -> None:
        self.assertTrue(callable(run_backtest))

    def test_summary_helpers_do_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            summarize_balance_performance(_summary_helper_report(), 10000.0)
            calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

            self.assertFalse((base / "trading_signal_order.csv").exists())

    def test_summary_helpers_do_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            summarize_balance_performance(_summary_helper_report(), 10000.0)
            calculate_backtest_cost_summary(tuple(_cost_summary_trades()), _realism_config())

            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())

    def test_backtest_source_has_no_auto_trade_strings_after_summary_helpers(self) -> None:
        source = Path("src/trading_signal_bot/backtest.py").read_text(encoding="utf-8")

        self.assertNotIn("auto_trade", source)
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("order_file", source)
        self.assertNotIn("trading_signal_order", source)


def _decision(
    session: str = "London",
    stage: str = "execution_policy",
    approved: bool = False,
    reasons: tuple[str, ...] = ("Spread is above maximum allowed",),
) -> BacktestDecision:
    return BacktestDecision(
        timestamp="2026-05-18T07:00:00Z",
        session=session,
        symbol="XAUUSD",
        timeframe="M1",
        action="BUY",
        stage=stage,
        approved=approved,
        reasons=reasons,
        htf_bias="BULLISH",
        execution_trend="BULLISH",
        price_location="NEAR_DEMAND",
        candle_confirmation_summary="strong close",
        risk_reward=1.5,
    )


def _trade(
    action: SignalAction = SignalAction.BUY,
    session: str = "London",
    result: str = "WIN",
    r_multiple: float = 1.5,
    risk_reward: float | None = 1.5,
    balance_after: float | None = 1015.0,
    pnl: float | None = None,
    volume: float | None = 0.1,
) -> BacktestTradeResult:
    if pnl is None and r_multiple is not None:
        pnl = r_multiple * 10.0
    return BacktestTradeResult(
        action=action,
        session=session,
        entry_time="2026-05-18T07:00:00Z",
        exit_time="2026-05-18T07:05:00Z",
        entry=100.0,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        result=result,
        r_multiple=r_multiple,
        risk_reward=risk_reward,
        volume=volume,
        pnl=pnl,
        balance_after=balance_after,
        loss_reason="stop_loss" if result.startswith("LOSS") else None,
    )


def _trade_with_reject_reasons(
    reject_reasons_before_entry: tuple[str, ...],
) -> BacktestTradeResult:
    trade = _trade()
    return BacktestTradeResult(
        action=trade.action,
        session=trade.session,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        entry=trade.entry,
        stop_loss=trade.stop_loss,
        tp1=trade.tp1,
        tp2=trade.tp2,
        result=trade.result,
        r_multiple=trade.r_multiple,
        risk_reward=trade.risk_reward,
        volume=trade.volume,
        pnl=trade.pnl,
        balance_after=trade.balance_after,
        loss_reason=trade.loss_reason,
        reject_reasons_before_entry=reject_reasons_before_entry,
    )


def _money_trade(
    result: str,
    r_multiple: float,
    stop_loss: float = 99.0,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        action=SignalAction.BUY,
        session="London",
        entry_time="2026-05-18T07:00:00Z",
        exit_time="2026-05-18T07:05:00Z",
        entry=100.0,
        stop_loss=stop_loss,
        tp1=None,
        tp2=102.0,
        result=result,
        r_multiple=r_multiple,
        risk_reward=2.0,
        volume=None,
        pnl=None,
        balance_after=None,
        loss_reason="stop_loss_hit" if result.startswith("LOSS") else None,
        reject_reasons_before_entry=(),
    )


def _risk_trade(
    result: str,
    pnl: float | None,
) -> BacktestTradeResult:
    balance_after = 10000.0 + pnl if pnl is not None else None
    return _risk_trade_with_balance(result, pnl, balance_after)


def _risk_trade_with_balance(
    result: str,
    pnl: float | None,
    balance_after: float | None,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        action=SignalAction.BUY,
        session="London",
        entry_time="2026-05-18T07:00:00Z",
        exit_time="2026-05-18T07:05:00Z",
        entry=100.0,
        stop_loss=99.0,
        tp1=None,
        tp2=101.5,
        result=result,
        r_multiple=1.5 if result == "WIN" else -1.0,
        risk_reward=1.5,
        volume=1.0,
        pnl=pnl,
        balance_after=balance_after,
        loss_reason="stop_loss_hit" if result.startswith("LOSS") else None,
        reject_reasons_before_entry=(),
    )


def _daily_state(
    date: str = "2026-05-18",
    trades_today: int = 0,
    losses_today: int = 0,
    consecutive_losses: int = 0,
    weekly_consecutive_losses: int = 0,
    week_key: str = "2026-W21",
    realized_loss_percent: float = 0.0,
    cooldown_until: datetime | None = None,
    stopped_for_day: bool = False,
    peak_balance: float = 0.0,
) -> BacktestDailyRiskState:
    return BacktestDailyRiskState(
        date=date,
        trades_today=trades_today,
        losses_today=losses_today,
        consecutive_losses=consecutive_losses,
        weekly_consecutive_losses=weekly_consecutive_losses,
        week_key=week_key,
        realized_loss_percent=realized_loss_percent,
        cooldown_until=cooldown_until,
        stopped_for_day=stopped_for_day,
        peak_balance=peak_balance,
    )


def _candidate(
    action: SignalAction,
    entry: float = 100.0,
    stop_loss: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    risk_reward: float | None = 2.0,
    signal_index: int = 0,
) -> BacktestCandidate:
    if stop_loss is None:
        stop_loss = 99.0 if action == SignalAction.BUY else 101.0
    if tp2 is None and tp1 is None:
        tp2 = 102.0 if action == SignalAction.BUY else 98.0
    return BacktestCandidate(
        decision=_decision(stage="signal_candidate", approved=True, reasons=()),
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        signal_index=signal_index,
    )


def _price_candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 01:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


def _runner_candles(scenario: str, count: int = 3) -> list[Candle]:
    templates = {
        "buy_win": [
            (100.0, 101.0, 99.5, 100.0),
            (100.0, 102.5, 99.5, 101.5),
            (101.5, 102.0, 100.5, 101.0),
        ],
        "sell_loss": [
            (100.0, 100.5, 99.0, 100.0),
            (100.0, 101.5, 99.5, 101.0),
            (101.0, 101.2, 100.2, 100.8),
        ],
        "both_hit": [
            (100.0, 101.0, 99.5, 100.0),
            (100.0, 102.5, 98.5, 100.5),
            (100.5, 101.0, 99.5, 100.0),
        ],
        "open_at_end": [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.8, 99.2, 100.4),
            (100.4, 100.9, 99.4, 100.6),
        ],
        "two_buy_wins": [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.5, 99.5, 101.5),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.5, 99.5, 101.5),
        ],
        "win_then_sell_loss": [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.5, 99.5, 101.5),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 101.5, 99.5, 101.0),
        ],
        "sell_loss_then_wait": [
            (100.0, 100.5, 99.0, 100.0),
            (100.0, 101.5, 99.5, 101.0),
            (101.0, 101.2, 100.2, 100.8),
        ],
        "negative_open_at_end": [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.2, 99.6),
            (99.6, 100.4, 99.1, 99.5),
        ],
    }
    values = templates[scenario]
    candles: list[Candle] = []
    for index in range(count):
        open_, high, low, close = values[min(index, len(values) - 1)]
        candles.append(
            Candle(
                timestamp=f"2026-05-18 00:{index:02d}",
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def _risk_runner_candles_across_days() -> list[Candle]:
    values = [
        ("2026-05-18 00:00", 100.0, 100.5, 99.0, 100.0),
        ("2026-05-18 00:01", 100.0, 101.5, 99.5, 101.0),
        ("2026-05-19 00:00", 100.0, 100.5, 99.5, 100.0),
        ("2026-05-19 00:01", 100.0, 102.5, 99.5, 101.5),
    ]
    return [
        Candle(
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1000 + index,
        )
        for index, (timestamp, open_, high, low, close) in enumerate(values)
    ]


def _realism_config(
    initial_balance: float = 10000.0,
    risk_percent: float = 1.0,
    contract_size: float = 100.0,
    min_volume: float = 0.01,
    max_volume: float = 10.0,
    volume_step: float = 0.01,
    allow_min_volume: bool = True,
    spread_points: float = 20.0,
    point_value: float = 0.01,
    slippage_points: float = 5.0,
    commission_per_lot: float = 7.0,
    max_daily_loss_percent: float = 3.0,
    max_consecutive_losses: int = 3,
    cooldown_minutes: int = 30,
    drawdown_pause_percent: float = 0.0,
    drawdown_pause_days: int = 0,
    weekly_loss_pause_count: int = 0,
    weekly_loss_pause_days: int = 0,
) -> BacktestRealismConfig:
    return BacktestRealismConfig(
        initial_balance=initial_balance,
        risk_percent=risk_percent,
        contract_size=contract_size,
        min_volume=min_volume,
        max_volume=max_volume,
        volume_step=volume_step,
        allow_min_volume=allow_min_volume,
        spread_points=spread_points,
        point_value=point_value,
        slippage_points=slippage_points,
        commission_per_lot=commission_per_lot,
        max_daily_loss_percent=max_daily_loss_percent,
        max_consecutive_losses=max_consecutive_losses,
        cooldown_minutes=cooldown_minutes,
        drawdown_pause_percent=drawdown_pause_percent,
        drawdown_pause_days=drawdown_pause_days,
        weekly_loss_pause_count=weekly_loss_pause_count,
        weekly_loss_pause_days=weekly_loss_pause_days,
    )


def _metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_trades=3,
        approved_trades=2,
        rejected_trades=1,
        skipped_trades=1,
        win_rate=50.0,
        loss_rate=50.0,
        profit_factor=1.5,
        max_drawdown=1.0,
        average_win=1.5,
        average_loss=-1.0,
        average_rr=1.5,
        max_consecutive_losses=2,
        net_r=0.5,
    )


def _sample_trades() -> list[BacktestTradeResult]:
    return [
        _trade(session="Asia", result="WIN", r_multiple=1.5, balance_after=1015.0),
        _trade(session="London", result="LOSS", r_multiple=-1.0, balance_after=1005.0),
        _trade(session="London", result="LOSS", r_multiple=-1.0, balance_after=995.0),
        _trade(session="NewYork", result="WIN", r_multiple=2.0, balance_after=1015.0),
    ]


def _sample_decisions() -> list[BacktestDecision]:
    return [
        _decision(session="Asia", stage="approved", approved=True, reasons=()),
        _decision(
            session="London",
            stage="execution_policy",
            approved=False,
            reasons=("Spread is above maximum allowed",),
        ),
        _decision(
            session="London",
            stage="risk_manager",
            approved=False,
            reasons=("Risk/reward is below minimum", "Spread is above maximum allowed"),
        ),
        _decision(
            session="NewYork",
            stage="insufficient_candles",
            approved=False,
            reasons=("insufficient candles",),
        ),
        _decision(session="NewYork", stage="approved", approved=True, reasons=()),
    ]


def _signal_config(min_candles: int = 3) -> SignalConfig:
    return SignalConfig(
        symbol="XAUUSD",
        timeframe="M5",
        csv_path="samples/ohlcv_sample.csv",
        fast_ema_period=9,
        slow_ema_period=21,
        rsi_period=14,
        atr_period=14,
        atr_multiplier=1.5,
        body_break_atr_ratio=0.2,
        risk_reward=1.5,
        min_candles=min_candles,
        max_candle_age_minutes=180,
        multi_timeframe_enabled=True,
        timeframe_paths={},
        dry_run=True,
        send_wait=False,
        execution_timeframe="M1",
    )


def _signal(
    action: SignalAction,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    take_profit: float | None = 101.5,
    risk_reward: float | None = 1.5,
    no_trade_reason: str = "wait for confirmation",
    strategy_name: str = "test",
) -> Signal:
    return Signal(
        action=action,
        symbol="XAUUSD",
        timeframe="M1",
        strategy_name=strategy_name,
        market_structure="Uptrend",
        setup_type="NEAR_DEMAND",
        trend_summary="BULLISH",
        trend_alignment="BULLISH",
        confidence=Confidence.MEDIUM,
        reason="strong close",
        entry_condition="after candle close",
        invalidation="below swing low",
        no_trade_reason=no_trade_reason,
        support=99.0,
        resistance=102.0,
        latest_close=100.0,
        fast_ema=100.0,
        slow_ema=99.0,
        rsi=55.0,
        atr=1.0,
        levels=TradeLevels(
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
        ),
    )


def _candles(count: int) -> list[Candle]:
    return [
        _candle(index, open_=100.0 + index, high=101.0 + index, low=99.0 + index, close=100.5 + index)
        for index in range(count)
    ]


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 00:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 18, hour, minute, tzinfo=UTC)


def _sample_report() -> BacktestReport:
    trades = _sample_trades()
    decisions = _sample_decisions()
    return BacktestReport(
        trades=tuple(trades),
        decisions=tuple(decisions),
        metrics=calculate_backtest_metrics(trades, decisions),
        session_metrics=calculate_session_metrics(trades, decisions),
        reject_reason_summary=summarize_reject_reasons(decisions),
        skip_reason_summary=summarize_skip_reasons(decisions),
        stopped_reason=None,
    )


def _empty_report() -> BacktestReport:
    metrics = calculate_backtest_metrics([], [])
    return BacktestReport(
        trades=(),
        decisions=(),
        metrics=metrics,
        session_metrics={},
        reject_reason_summary={},
        skip_reason_summary={},
        stopped_reason=None,
    )


def _summary_helper_report() -> BacktestReport:
    trades = (
        _trade(session="Asia", result="WIN", r_multiple=1.5, pnl=150.0, balance_after=10150.0),
        _trade(session="London", result="LOSS", r_multiple=-1.0, pnl=-100.0, balance_after=10050.0),
        _trade(session="NewYork", result="LOSS_BOTH_HIT", r_multiple=-1.0, pnl=-100.0, balance_after=9950.0),
        _trade(session="Asia", result="OPEN_AT_END", r_multiple=0.5, pnl=50.0, balance_after=10150.0),
    )
    decisions = tuple(_sample_decisions())
    return build_backtest_report_from_decisions(decisions, trades)


def _risk_skip_summary_report() -> BacktestReport:
    metrics = calculate_backtest_metrics([], [])
    return BacktestReport(
        trades=(),
        decisions=(),
        metrics=metrics,
        session_metrics={},
        reject_reason_summary={},
        skip_reason_summary={
            "cooldown active": 2,
            "daily risk stopped for day": 1,
            "max daily loss reached": 1,
            "max consecutive losses reached": 1,
            "insufficient candles": 9,
        },
        stopped_reason=None,
    )


def _session_pnl_trades() -> list[BacktestTradeResult]:
    return [
        _trade(session="Asia", result="WIN", r_multiple=1.5, pnl=150.0),
        _trade(session="Asia", result="LOSS", r_multiple=-1.0, pnl=-100.0),
        _trade(session="London", result="LOSS_BOTH_HIT", r_multiple=-1.0, pnl=-75.0),
        _trade(session="NewYork", result="OPEN_AT_END", r_multiple=0.25, pnl=25.0),
    ]


def _cost_summary_trades() -> list[BacktestTradeResult]:
    return [
        _trade(volume=1.0),
        _trade(volume=2.0),
        _trade(volume=None),
    ]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _summary_payload() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "backtest_summary.json"
        export_backtest_summary_json(_sample_report(), path)
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
