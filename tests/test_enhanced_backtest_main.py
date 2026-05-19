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
    BacktestReport,
)
from trading_signal_bot.enhanced_backtest_main import (
    build_backtest_range_from_env,
    enhanced_backtest_mode_from_env,
    enhanced_backtest_output_dir_from_env,
    format_enhanced_backtest_summary,
    main,
)
from trading_signal_bot.models import Candle, SignalConfig


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

    def test_mode_from_env_is_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "Decision"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "decision")
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "SIMULATION"}, clear=True):
            self.assertEqual(enhanced_backtest_mode_from_env(), "simulation")

    def test_mode_from_env_invalid_raises(self) -> None:
        with patch.dict(os.environ, {"ENHANCED_BACKTEST_MODE": "live"}, clear=True):
            with self.assertRaisesRegex(ValueError, "ENHANCED_BACKTEST_MODE must be decision or simulation"):
                enhanced_backtest_mode_from_env()

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

        self.assertIn("offline decision report only", summary)

    def test_summary_has_decision_mode_label(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"), mode="decision")

        self.assertIn("Mode: decision-only offline report", summary)

    def test_summary_has_simulation_mode_label(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"), mode="simulation")

        self.assertIn("Mode: offline simulation report", summary)

    def test_summary_has_decision_counts(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Decisions: 3", summary)
        self.assertIn("Approved decisions: 1", summary)
        self.assertIn("Rejected decisions: 1", summary)
        self.assertIn("Skipped decisions: 1", summary)

    def test_summary_has_trades_simulated_zero(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Trades simulated: 0", summary)

    def test_summary_has_output_directory(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Output directory: logs", summary)

    def test_summary_has_no_order_sent(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("No order was sent.", summary)

    def test_summary_has_no_mt5_order_intent_written(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("No MT5 order intent was written.", summary)

    def test_summary_has_offline_backtest_only(self) -> None:
        summary = format_enhanced_backtest_summary(_report(), Path("logs/enhanced_backtest"))

        self.assertIn("Offline backtest only.", summary)

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

    def test_output_dir_has_all_four_files_after_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "enhanced"
            with _patched_success_boundaries(output_dir):
                with redirect_stdout(StringIO()):
                    main()

            self.assertEqual(
                {path.name for path in output_dir.iterdir() if path.is_file()},
                {
                    "backtest_trades.csv",
                    "backtest_decisions.csv",
                    "backtest_session_summary.csv",
                    "backtest_summary.json",
                },
            )

    def test_source_ast_has_no_auto_trade_import(self) -> None:
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))

    def test_source_has_no_process_auto_trade(self) -> None:
        self.assertNotIn("process_auto_trade", _source_text())

    def test_source_has_no_order_file(self) -> None:
        self.assertNotIn("order_file", _source_text())

    def test_source_has_no_trading_signal_order(self) -> None:
        self.assertNotIn("trading_signal_order", _source_text())

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


def _patched_success_boundaries(output_dir: Path):
    return _BoundaryPatches(output_dir)


class _BoundaryPatches:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self._patches = []

    def __enter__(self):
        self._patches = [
            patch.dict(os.environ, {"ENHANCED_BACKTEST_OUTPUT_DIR": str(self.output_dir)}, clear=True),
            patch("trading_signal_bot.enhanced_backtest_main.load_env_file"),
            patch("trading_signal_bot.enhanced_backtest_main.load_signal_config", return_value=_signal_config()),
            patch("trading_signal_bot.enhanced_backtest_main.load_timeframe_candles", return_value={"M1": _candles()}),
            patch("trading_signal_bot.enhanced_backtest_main.run_enhanced_backtest_report", return_value=_report()),
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
