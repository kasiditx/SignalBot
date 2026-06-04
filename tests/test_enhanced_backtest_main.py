from __future__ import annotations

import ast
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from trading_signal_bot.backtest import (
    BacktestDecision,
    BacktestMetrics,
    BacktestRange,
    BacktestRealismConfig,
    BacktestReport,
    BacktestTradeResult,
)
from trading_signal_bot.enhanced_backtest_main import (
    _get_bool_env,
    _get_float_env,
    _get_int_env,
    build_backtest_range_from_env,
    build_realism_config_from_env,
    enhanced_backtest_mode_from_env,
    enhanced_backtest_output_dir_from_env,
    format_enhanced_backtest_summary,
    main,
)
from trading_signal_bot.models import Candle, SignalAction, SignalConfig


class EnhancedBacktestMainTest(unittest.TestCase):
    def test_output_dir_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(enhanced_backtest_output_dir_from_env(), Path("logs/enhanced_backtest"))

    def test_output_dir_from_env(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_OUTPUT_DIR": "tmp/report"}, clear=True):
            self.assertEqual(enhanced_backtest_output_dir_from_env(), Path("tmp/report"))

    def test_mode_default_is_decision(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "decision")

    def test_mode_from_env_decision(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "decision"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "decision")

    def test_mode_from_env_simulation(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "simulation"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "simulation")

    def test_mode_from_env_realism(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "realism"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "realism")

    def test_mode_from_env_is_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "Decision"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "decision")
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "SIMULATION"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "simulation")

    def test_mode_from_env_realism_is_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "REALISM"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "realism")
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "Realism"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "realism")

    def test_mode_from_env_invalid_raises(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "live"}, clear=True):
            with self.assertRaisesRegex(ValueError, "ENHANCED_BACKTEST_MODE must be decision, simulation, or realism"):
                enhanced_backtest_mode_from_env()

    def test_build_realism_config_from_env_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            realism = build_realism_config_from_env()

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

    def test_build_realism_config_from_env_overrides_all_fields(self) -> None:
        env = {
            "BACKTEST_INITIAL_BALANCE": "20000",
            "BACKTEST_RISK_PERCENT": "0.5",
            "BACKTEST_CONTRACT_SIZE": "50",
            "BACKTEST_MIN_VOLUME": "0.02",
            "BACKTEST_MAX_VOLUME": "5",
            "BACKTEST_VOLUME_STEP": "0.02",
            "BACKTEST_ALLOW_MIN_VOLUME": "false",
            "BACKTEST_SPREAD_POINTS": "15",
            "BACKTEST_POINT_VALUE": "0.1",
            "BACKTEST_SLIPPAGE_POINTS": "2",
            "BACKTEST_COMMISSION_PER_LOT": "3.5",
            "BACKTEST_MAX_DAILY_LOSS_PERCENT": "2",
            "BACKTEST_MAX_CONSECUTIVE_LOSSES": "2",
            "BACKTEST_COOLDOWN_MINUTES": "45",
        }

        with patch.dict(os.environ, env, clear=True):
            realism = build_realism_config_from_env()

        self.assertEqual(realism.initial_balance, 20000.0)
        self.assertEqual(realism.risk_percent, 0.5)
        self.assertEqual(realism.contract_size, 50.0)
        self.assertEqual(realism.min_volume, 0.02)
        self.assertEqual(realism.max_volume, 5.0)
        self.assertEqual(realism.volume_step, 0.02)
        self.assertFalse(realism.allow_min_volume)
        self.assertEqual(realism.spread_points, 15.0)
        self.assertEqual(realism.point_value, 0.1)
        self.assertEqual(realism.slippage_points, 2.0)
        self.assertEqual(realism.commission_per_lot, 3.5)
        self.assertEqual(realism.max_daily_loss_percent, 2.0)
        self.assertEqual(realism.max_consecutive_losses, 2)
        self.assertEqual(realism.cooldown_minutes, 45)

    def test_get_bool_env_parses_true_and_false(self) -> None:
        for value in ("true", "1", "yes", "on"):
            with patch.dict(os.environ, {"BOOL_TEST": value}, clear=True):
                self.assertTrue(_get_bool_env("BOOL_TEST", False))
        for value in ("false", "0", "no", "off"):
            with patch.dict(os.environ, {"BOOL_TEST": value}, clear=True):
                self.assertFalse(_get_bool_env("BOOL_TEST", True))

    def test_invalid_float_env_raises(self) -> None:
        with patch.dict(os.environ, {"FLOAT_TEST": "abc"}, clear=True):
            with self.assertRaisesRegex(ValueError, "FLOAT_TEST must be a number"):
                _get_float_env("FLOAT_TEST", 1.0)

    def test_invalid_int_env_raises(self) -> None:
        with patch.dict(os.environ, {"INT_TEST": "1.5"}, clear=True):
            with self.assertRaisesRegex(ValueError, "INT_TEST must be an integer"):
                _get_int_env("INT_TEST", 1)

    def test_invalid_bool_env_raises(self) -> None:
        with patch.dict(os.environ, {"BOOL_TEST": "maybe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "BOOL_TEST must be true or false"):
                _get_bool_env("BOOL_TEST", True)

    def test_realism_config_min_volume_greater_than_max_raises(self) -> None:
        with patch.dict(os.environ, {"BACKTEST_MIN_VOLUME": "2", "BACKTEST_MAX_VOLUME": "1"}, clear=True):
            with self.assertRaisesRegex(ValueError, "BACKTEST_MIN_VOLUME must be lower than or equal to BACKTEST_MAX_VOLUME"):
                build_realism_config_from_env()

    def test_build_range_returns_none_without_lookback_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = build_backtest_range_from_env({"M1": _candles()}, _signal_config())

        self.assertIsNone(result)

    def test_build_range_from_lookback_days(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_LOOKBACK_DAYS": "2"}, clear=True):
            result = build_backtest_range_from_env({"M1": _candles()}, _signal_config())

        self.assertIsInstance(result, BacktestRange)
        self.assertEqual(result.label, "last 2 days")
        self.assertEqual(result.end, datetime(2026, 5, 18, 0, 2, tzinfo=result.end.tzinfo))

    def test_summary_has_title(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Enhanced Backtest Summary", summary)

    def test_summary_has_mode(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Mode: decision-only offline report", summary)

    def test_summary_has_decision_mode_label(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"), mode="decision")

        self.assertIn("Mode: decision-only offline report", summary)

    def test_summary_has_simulation_mode_label(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"), mode="simulation")

        self.assertIn("Mode: offline simulation report", summary)

    def test_summary_has_realism_mode_balance_lines(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="realism",
            realism=_realism_config(),
        )

        self.assertIn("Mode: offline realism report", summary)
        self.assertIn("Initial balance: 10000.00", summary)
        self.assertIn("Final balance: 10118.00", summary)
        self.assertIn("Net PnL: 118.00", summary)

    def test_summary_has_decision_counts(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Decisions:", summary)
        self.assertIn("Total decisions: 3", summary)
        self.assertIn("Approved: 1", summary)
        self.assertIn("Rejected: 1", summary)
        self.assertIn("Skipped: 1", summary)

    def test_decision_summary_does_not_show_trade_balance_cost_or_session_sections(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertNotIn("Trades:", summary)
        self.assertNotIn("Balance:", summary)
        self.assertNotIn("Costs:", summary)
        self.assertNotIn("Session PnL:", summary)

    def test_simulation_summary_has_trade_section(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="simulation",
        )

        self.assertIn("Mode: offline simulation report", summary)
        self.assertIn("Trades:", summary)
        self.assertIn("Trades simulated: 1", summary)
        self.assertIn("Win rate: 100.00%", summary)
        self.assertIn("Profit factor: 1.50", summary)
        self.assertIn("Net R: 1.50R", summary)

    def test_simulation_summary_does_not_show_realism_only_sections(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="simulation",
        )

        self.assertNotIn("Balance:", summary)
        self.assertNotIn("Costs:", summary)
        self.assertNotIn("Session PnL:", summary)

    def test_realism_summary_has_trade_balance_cost_risk_and_session_sections(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="realism",
            realism=_realism_config(),
        )

        for expected in (
            "Trades:",
            "Balance:",
            "Initial balance",
            "Final balance",
            "Net PnL",
            "Return %",
            "Max drawdown",
            "Costs:",
            "Commission",
            "Spread cost",
            "Slippage cost",
            "Total cost",
            "Risk skips:",
            "Session PnL:",
            "Asia",
            "London",
            "NewYork",
            "Other",
        ):
            self.assertIn(expected, summary)

    def test_realism_summary_without_realism_config_skips_balance_and_costs(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="realism",
            realism=None,
        )

        self.assertIn("Mode: offline realism report", summary)
        self.assertNotIn("Balance:", summary)
        self.assertNotIn("Costs:", summary)
        self.assertIn("Risk skips:", summary)
        self.assertIn("Session PnL:", summary)

    def test_risk_skip_summary_shows_none_when_empty(self) -> None:
        summary = format_enhanced_backtest_summary(
            _realism_report(),
            Path("logs/enhanced_backtest"),
            mode="realism",
            realism=_realism_config(),
        )

        self.assertIn("Risk skips:", summary)
        self.assertIn("- None", summary)

    def test_risk_skip_summary_shows_reason_counts(self) -> None:
        summary = format_enhanced_backtest_summary(
            _risk_skip_report(),
            Path("logs/enhanced_backtest"),
            mode="realism",
            realism=_realism_config(),
        )

        self.assertIn("cooldown active: 1", summary)

    def test_summary_has_output_directory(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Output directory: logs", summary)

    def test_summary_has_enhanced_summary_files_message(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Enhanced summary files: enhanced_backtest_summary.json and CSV summaries", summary)

    def test_all_summary_modes_have_safety_text_and_output_directory(self) -> None:
        mode_cases = (
            ("decision", _report(), None),
            ("simulation", _realism_report(), None),
            ("realism", _realism_report(), _realism_config()),
        )
        for mode, report, realism in mode_cases:
            with self.subTest(mode=mode):
                summary = format_enhanced_backtest_summary(
                    report,
                    Path("logs/enhanced_backtest"),
                    mode=mode,
                    realism=realism,
                )

                self.assertIn("No order was sent.", summary)
                self.assertIn("No MT5 order intent was written.", summary)
                self.assertIn("Offline backtest only.", summary)
                self.assertIn("Output directory", summary)

    def test_main_returns_zero_and_exports_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with redirect_stdout(StringIO()):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "backtest_trades.csv").exists())
            self.assertTrue((output_dir / "backtest_decisions.csv").exists())
            self.assertTrue((output_dir / "backtest_session_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_summary.json").exists())
            self.assertTrue((output_dir / "enhanced_backtest_summary.json").exists())
            self.assertTrue((output_dir / "backtest_risk_skip_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_session_pnl_summary.csv").exists())

    def test_main_decision_mode_calls_decision_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with patch.dict(
                os.environ,
                {"ENHANCED_BACKTEST_OUTPUT_DIR": str(output_dir), "ENHANCED_BACKTEST_MODE": "decision"},
                clear=True,
            ):
                with patch("trading_signal_bot.enhanced_backtest_main.load_env_file"):
                    with patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", return_value=_signal_config()):
                        with patch("trading_signal_bot.enhanced_backtest_main.load_timeframe_candles", return_value={"M1": _candles()}):
                            with patch(
                                "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report",
                                return_value=_report(),
                            ) as decision_runner:
                                with patch(
                                    "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_simulation",
                                    return_value=_report(),
                                ) as simulation_runner:
                                    with redirect_stdout(StringIO()):
                                        exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(decision_runner.call_count, 1)
        self.assertEqual(simulation_runner.call_count, 0)

    def test_main_simulation_mode_calls_simulation_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with patch.dict(
                os.environ,
                {"ENHANCED_BACKTEST_OUTPUT_DIR": str(output_dir), "ENHANCED_BACKTEST_MODE": "simulation"},
                clear=True,
            ):
                with patch("trading_signal_bot.enhanced_backtest_main.load_env_file"):
                    with patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", return_value=_signal_config()):
                        with patch("trading_signal_bot.enhanced_backtest_main.load_timeframe_candles", return_value={"M1": _candles()}):
                            with patch(
                                "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report",
                                return_value=_report(),
                            ) as decision_runner:
                                with patch(
                                    "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_simulation",
                                    return_value=_report(),
                                ) as simulation_runner:
                                    with redirect_stdout(StringIO()):
                                        exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(decision_runner.call_count, 0)
        self.assertEqual(simulation_runner.call_count, 1)

    def test_main_realism_mode_calls_realism_runner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with patch.dict(
                os.environ,
                {"ENHANCED_BACKTEST_OUTPUT_DIR": str(output_dir), "ENHANCED_BACKTEST_MODE": "realism"},
                clear=True,
            ):
                with patch("trading_signal_bot.enhanced_backtest_main.load_env_file"):
                    with patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", return_value=_signal_config()):
                        with patch("trading_signal_bot.enhanced_backtest_main.load_timeframe_candles", return_value={"M1": _candles()}):
                            with patch(
                                "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report",
                                return_value=_report(),
                            ) as decision_runner:
                                with patch(
                                    "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_simulation",
                                    return_value=_report(),
                                ) as simulation_runner:
                                    with patch(
                                        "trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_realism",
                                        return_value=_realism_report(),
                                    ) as realism_runner:
                                        with redirect_stdout(StringIO()):
                                            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(decision_runner.call_count, 0)
        self.assertEqual(simulation_runner.call_count, 0)
        self.assertEqual(realism_runner.call_count, 1)

    def test_main_returns_one_on_runtime_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("trading_signal_bot.enhanced_backtest_main.load_env_file"):
                with patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", side_effect=ValueError("bad config")):
                    output = StringIO()
                    with redirect_stdout(output):
                        exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Enhanced backtest failed", output.getvalue())
        self.assertIn("No order was sent.", output.getvalue())

    def test_decision_mode_exports_legacy_and_enhanced_base_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with redirect_stdout(StringIO()):
                    main()

            self.assertEqual(_output_file_names(output_dir), _legacy_files() | _enhanced_base_files())
            self.assertFalse((output_dir / "backtest_realism_summary.csv").exists())
            self.assertFalse((output_dir / "backtest_cost_summary.csv").exists())

    def test_simulation_mode_exports_legacy_and_enhanced_base_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir, mode="simulation", report=_realism_report()):
                with redirect_stdout(StringIO()):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(_output_file_names(output_dir), _legacy_files() | _enhanced_base_files())
            self.assertFalse((output_dir / "backtest_realism_summary.csv").exists())
            self.assertFalse((output_dir / "backtest_cost_summary.csv").exists())

    def test_realism_mode_exports_legacy_enhanced_base_and_realism_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir, mode="realism", report=_realism_report()):
                with redirect_stdout(StringIO()):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(_output_file_names(output_dir), _legacy_files() | _enhanced_base_files() | _realism_files())

    def test_realism_mode_passes_realism_config_to_enhanced_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir, mode="realism", report=_realism_report()):
                with patch(
                    "trading_signal_bot.enhanced_backtest_main.export_enhanced_backtest_summary_files",
                ) as enhanced_export:
                    with redirect_stdout(StringIO()):
                        exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(enhanced_export.call_count, 1)
            self.assertIsInstance(enhanced_export.call_args.kwargs["realism"], BacktestRealismConfig)
            self.assertEqual(enhanced_export.call_args.kwargs["mode"], "realism")

    def test_enhanced_export_failure_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with patch(
                    "trading_signal_bot.enhanced_backtest_main.export_enhanced_backtest_summary_files",
                    side_effect=ValueError("enhanced export failed"),
                ):
                    output = StringIO()
                    with redirect_stdout(output):
                        exit_code = main()

            self.assertEqual(exit_code, 1)
            self.assertIn("Enhanced backtest failed", output.getvalue())
            self.assertIn("No order was sent.", output.getvalue())

    def test_export_backtest_report_is_called_before_enhanced_export(self) -> None:
        calls: list[str] = []

        def legacy_export(*args, **kwargs) -> None:
            calls.append("legacy")

        def enhanced_export(*args, **kwargs) -> None:
            calls.append("enhanced")

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with patch(
                    "trading_signal_bot.enhanced_backtest_main.export_backtest_report",
                    side_effect=legacy_export,
                ):
                    with patch(
                        "trading_signal_bot.enhanced_backtest_main.export_enhanced_backtest_summary_files",
                        side_effect=enhanced_export,
                    ):
                        with redirect_stdout(StringIO()):
                            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["legacy", "enhanced"])

    def test_source_ast_has_no_auto_trade_import(self) -> None:
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))

    def test_source_has_no_process_auto_trade(self) -> None:
        self.assertNotIn("process_auto_trade", _source_text())

    def test_source_has_no_order_file(self) -> None:
        self.assertNotIn("order_file", _source_text())

    def test_source_has_no_trading_signal_order(self) -> None:
        self.assertNotIn("trading_signal_order", _source_text())

    def test_source_has_no_auto_trade_order_file_env(self) -> None:
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", _source_text())

    def test_does_not_call_legacy_backtest_main(self) -> None:
        source = _source_text()

        self.assertNotIn("backtest.main", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with redirect_stdout(StringIO()):
                    main()

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with redirect_stdout(StringIO()):
                    main()

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


def _patched_success_boundaries(
    output_dir: Path,
    mode: str = "decision",
    report: BacktestReport | None = None,
):
    return _BoundaryPatches(output_dir, mode, report or _report())


class _BoundaryPatches:
    def __init__(self, output_dir: Path, mode: str, report: BacktestReport) -> None:
        self.output_dir = output_dir
        self.mode = mode
        self.report = report
        self._patches = []

    def __enter__(self):
        self._patches = [
            patch.dict(
                os.environ,
                {
                    "ENHANCED_BACKTEST_OUTPUT_DIR": str(self.output_dir),
                    "ENHANCED_BACKTEST_MODE": self.mode,
                },
                clear=True,
            ),
            patch("trading_signal_bot.enhanced_backtest_main.load_env_file"),
            patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", return_value=_signal_config()),
            patch("trading_signal_bot.enhanced_backtest_main.load_timeframe_candles", return_value={"M1": _candles()}),
            patch("trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report", return_value=self.report),
            patch("trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_simulation", return_value=self.report),
            patch("trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report_with_realism", return_value=self.report),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for item in reversed(self._patches):
            item.stop()


def _report() -> BacktestReport:
    metrics = BacktestMetrics(
        total_trades=0,
        approved_trades=1,
        rejected_trades=1,
        skipped_trades=1,
        win_rate=0.0,
        loss_rate=0.0,
        profit_factor=0.0,
        max_drawdown=0.0,
        average_win=0.0,
        average_loss=0.0,
        average_rr=0.0,
        max_consecutive_losses=0,
        net_r=0.0,
    )
    return BacktestReport(
        trades=(),
        decisions=(
            _decision("Asia", "signal_candidate", True, ()),
            _decision("London", "execution_policy", False, ("Spread is above maximum allowed",)),
            _decision("NewYork", "insufficient_candles", False, ("insufficient candles",)),
        ),
        metrics=metrics,
        session_metrics={"Asia": metrics, "London": metrics, "NewYork": metrics, "Other": metrics},
        reject_reason_summary={"Spread is above maximum allowed": 1},
        skip_reason_summary={"insufficient candles": 1},
        stopped_reason=None,
    )


def _realism_report() -> BacktestReport:
    metrics = BacktestMetrics(
        total_trades=1,
        approved_trades=1,
        rejected_trades=0,
        skipped_trades=0,
        win_rate=100.0,
        loss_rate=0.0,
        profit_factor=1.5,
        max_drawdown=0.0,
        average_win=1.5,
        average_loss=0.0,
        average_rr=1.5,
        max_consecutive_losses=0,
        net_r=1.5,
    )
    trade = BacktestTradeResult(
        action=SignalAction.BUY,
        session="Asia",
        entry_time="2026-05-18T00:00:00+00:00",
        exit_time="2026-05-18T00:01:00+00:00",
        entry=100.0,
        stop_loss=99.0,
        tp1=None,
        tp2=101.5,
        result="WIN",
        r_multiple=1.5,
        risk_reward=1.5,
        volume=1.0,
        pnl=118.0,
        balance_after=10118.0,
        loss_reason=None,
    )
    return BacktestReport(
        trades=(trade,),
        decisions=(_decision("Asia", "signal_candidate", True, ()),),
        metrics=metrics,
        session_metrics={"Asia": metrics, "London": metrics, "NewYork": metrics, "Other": metrics},
        reject_reason_summary={},
        skip_reason_summary={},
        stopped_reason=None,
    )


def _risk_skip_report() -> BacktestReport:
    metrics = BacktestMetrics(
        total_trades=0,
        approved_trades=0,
        rejected_trades=0,
        skipped_trades=1,
        win_rate=0.0,
        loss_rate=0.0,
        profit_factor=0.0,
        max_drawdown=0.0,
        average_win=0.0,
        average_loss=0.0,
        average_rr=0.0,
        max_consecutive_losses=0,
        net_r=0.0,
    )
    return BacktestReport(
        trades=(),
        decisions=(
            _decision("London", "risk_skip", False, ("cooldown active",)),
        ),
        metrics=metrics,
        session_metrics={"Asia": metrics, "London": metrics, "NewYork": metrics, "Other": metrics},
        reject_reason_summary={},
        skip_reason_summary={"cooldown active": 1},
        stopped_reason=None,
    )


def _realism_config() -> BacktestRealismConfig:
    return BacktestRealismConfig(
        initial_balance=10000.0,
        risk_percent=1.0,
        contract_size=100.0,
        min_volume=0.01,
        max_volume=10.0,
        volume_step=0.01,
        allow_min_volume=True,
        spread_points=20.0,
        point_value=0.01,
        slippage_points=5.0,
        commission_per_lot=7.0,
        max_daily_loss_percent=3.0,
        max_consecutive_losses=3,
        cooldown_minutes=30,
    )


def _decision(
    session: str,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
) -> BacktestDecision:
    return BacktestDecision(
        timestamp="2026-05-18T07:00:00+00:00",
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


def _signal_config() -> SignalConfig:
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
        min_candles=3,
        max_candle_age_minutes=180,
        multi_timeframe_enabled=True,
        timeframe_paths={},
        dry_run=True,
        send_wait=False,
        execution_timeframe="M1",
    )


def _candles() -> list[Candle]:
    return [
        Candle("2026-05-18 00:00", 100.0, 101.0, 99.0, 100.5, 1000.0),
        Candle("2026-05-18 00:01", 100.5, 101.5, 99.5, 101.0, 1001.0),
        Candle("2026-05-18 00:02", 101.0, 102.0, 100.0, 101.5, 1002.0),
    ]


def _legacy_files() -> set[str]:
    return {
        "backtest_trades.csv",
        "backtest_decisions.csv",
        "backtest_session_summary.csv",
        "backtest_summary.json",
    }


def _enhanced_base_files() -> set[str]:
    return {
        "enhanced_backtest_summary.json",
        "backtest_risk_skip_summary.csv",
        "backtest_session_pnl_summary.csv",
    }


def _realism_files() -> set[str]:
    return {
        "backtest_realism_summary.csv",
        "backtest_cost_summary.csv",
    }


def _output_file_names(output_dir: Path) -> set[str]:
    return {path.name for path in output_dir.iterdir() if path.is_file()}


def _source_text() -> str:
    return _source_path().read_text(encoding="utf-8")


def _source_imports() -> list[str]:
    tree = ast.parse(_source_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return imports


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "enhanced_backtest_main.py"


if __name__ == "__main__":
    unittest.main()
