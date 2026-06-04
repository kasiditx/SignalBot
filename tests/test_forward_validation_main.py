from __future__ import annotations

import ast
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading_signal_bot.forward_validation import (
    ForwardValidationRecord,
    ForwardValidationResult,
)
from trading_signal_bot.forward_validation_main import (
    build_dry_run_adapter_input_from_forward_input,
    build_dry_run_market_input_from_env,
    build_forward_validation_config_from_env,
    build_forward_validation_input_from_env,
    format_forward_validation_summary,
    forward_validation_output_dir_from_env,
    main,
    parse_bool_env,
    parse_optional_action,
    parse_optional_float,
)
from trading_signal_bot.models import Candle, SignalAction, SignalConfig


class ForwardValidationMainTest(unittest.TestCase):
    def test_parse_optional_float_none_returns_none(self) -> None:
        self.assertIsNone(parse_optional_float(None))

    def test_parse_optional_float_empty_returns_none(self) -> None:
        self.assertIsNone(parse_optional_float(""))

    def test_parse_optional_float_numeric_string(self) -> None:
        self.assertEqual(parse_optional_float("123.45"), 123.45)

    def test_parse_optional_float_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected a numeric value"):
            parse_optional_float("abc")

    def test_parse_optional_action_none_returns_none(self) -> None:
        self.assertIsNone(parse_optional_action(None))

    def test_parse_optional_action_empty_returns_none(self) -> None:
        self.assertIsNone(parse_optional_action(""))

    def test_parse_optional_action_buy(self) -> None:
        self.assertEqual(parse_optional_action("BUY"), SignalAction.BUY)

    def test_parse_optional_action_sell(self) -> None:
        self.assertEqual(parse_optional_action("SELL"), SignalAction.SELL)

    def test_parse_optional_action_case_insensitive(self) -> None:
        self.assertEqual(parse_optional_action("buy"), SignalAction.BUY)
        self.assertEqual(parse_optional_action("sell"), SignalAction.SELL)

    def test_parse_optional_action_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "FORWARD_ACTION must be BUY, SELL, or empty"):
            parse_optional_action("WAIT")

    def test_parse_bool_env_true_values(self) -> None:
        for value in ("true", "1", "yes", "y", "on"):
            with self.subTest(value=value):
                self.assertTrue(parse_bool_env(value))

    def test_parse_bool_env_false_values(self) -> None:
        for value in ("false", "0", "no", "n", "off"):
            with self.subTest(value=value):
                self.assertFalse(parse_bool_env(value, default=True))

    def test_parse_bool_env_none_returns_default(self) -> None:
        self.assertTrue(parse_bool_env(None, default=True))
        self.assertFalse(parse_bool_env(None, default=False))

    def test_parse_bool_env_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Boolean env value must be true or false"):
            parse_bool_env("maybe")

    def test_output_dir_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(forward_validation_output_dir_from_env(), Path("logs/forward_validation"))

    def test_output_dir_from_env(self) -> None:
        with patch.dict(os.environ, {"FORWARD_VALIDATION_OUTPUT_DIR": "tmp/forward"}, clear=True):
            self.assertEqual(forward_validation_output_dir_from_env(), Path("tmp/forward"))

    def test_build_forward_validation_config_from_env_paths(self) -> None:
        with patch.dict(os.environ, {"FORWARD_VALIDATION_OUTPUT_DIR": "tmp/forward"}, clear=True):
            config = build_forward_validation_config_from_env()

        self.assertEqual(config.record_csv_path, Path("tmp/forward/forward_records.csv"))
        self.assertEqual(config.record_jsonl_path, Path("tmp/forward/forward_records.jsonl"))
        self.assertEqual(config.daily_summary_path, Path("tmp/forward/daily_summary.csv"))
        self.assertEqual(config.weekly_summary_path, Path("tmp/forward/weekly_summary.csv"))

    def test_build_forward_validation_input_from_env_maps_forward_values(self) -> None:
        with patch.dict(os.environ, _forward_env(), clear=True):
            validation_input = build_forward_validation_input_from_env(_signal_config())

        self.assertEqual(validation_input.symbol, "XAUUSD")
        self.assertEqual(validation_input.mode, "demo")
        self.assertEqual(validation_input.action, SignalAction.BUY)
        self.assertEqual(validation_input.entry, 2400.0)
        self.assertEqual(validation_input.stop_loss, 2398.0)
        self.assertEqual(validation_input.tp1, 2403.0)
        self.assertEqual(validation_input.tp2, 2405.0)
        self.assertEqual(validation_input.risk_reward, 1.5)
        self.assertEqual(validation_input.current_price, 2400.5)
        self.assertEqual(validation_input.spread_points, 20.0)
        self.assertEqual(validation_input.session, "London")

    def test_build_forward_validation_input_default_mode_is_paper(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            validation_input = build_forward_validation_input_from_env(_signal_config())

        self.assertEqual(validation_input.mode, "paper")

    def test_build_forward_validation_input_metadata_contains_market_fields(self) -> None:
        with patch.dict(os.environ, _forward_env(), clear=True):
            validation_input = build_forward_validation_input_from_env(_signal_config())

        self.assertEqual(validation_input.metadata["current_price"], 2400.5)
        self.assertEqual(validation_input.metadata["spread_points"], 20.0)
        self.assertEqual(validation_input.metadata["session"], "London")
        self.assertEqual(validation_input.metadata["atr_value"], 12.5)
        self.assertEqual(validation_input.metadata["average_atr"], 10.0)
        self.assertTrue(validation_input.metadata["high_impact_news_nearby"])

    def test_build_dry_run_adapter_input_from_forward_input_maps_trade_fields(self) -> None:
        validation_input = build_forward_validation_input_from_env(_signal_config())

        adapter_input = build_dry_run_adapter_input_from_forward_input(validation_input)

        self.assertEqual(adapter_input.action, validation_input.action)
        self.assertEqual(adapter_input.entry, validation_input.entry)
        self.assertEqual(adapter_input.stop_loss, validation_input.stop_loss)
        self.assertEqual(adapter_input.tp1, validation_input.tp1)
        self.assertEqual(adapter_input.tp2, validation_input.tp2)
        self.assertEqual(adapter_input.risk_reward, validation_input.risk_reward)
        self.assertEqual(adapter_input.mode, validation_input.mode)

    def test_build_dry_run_market_input_from_env_maps_market_fields(self) -> None:
        with patch.dict(os.environ, _forward_env(), clear=True):
            market_input = build_dry_run_market_input_from_env()

        self.assertEqual(market_input.current_price, 2400.5)
        self.assertEqual(market_input.spread_points, 20.0)
        self.assertEqual(market_input.session, "London")
        self.assertEqual(market_input.atr_value, 12.5)
        self.assertEqual(market_input.average_atr, 10.0)
        self.assertTrue(market_input.high_impact_news_nearby)

    def test_format_forward_validation_summary_has_required_lines(self) -> None:
        summary = format_forward_validation_summary(_validation_result(), Path("logs/forward_validation"))

        self.assertIn("Forward Validation Summary", summary)
        self.assertIn("Stage:", summary)
        self.assertIn("Approved:", summary)
        self.assertIn("Reasons:", summary)
        self.assertIn("Record written:", summary)
        self.assertIn("Daily/weekly summaries: not written", summary)
        self.assertIn("Output directory:", summary)
        self.assertIn("No order was sent.", summary)
        self.assertIn("No MT5 order intent was written.", summary)
        self.assertIn("Forward dry-run only.", summary)

    def test_format_forward_validation_summary_shows_summaries_written(self) -> None:
        summary = format_forward_validation_summary(
            _validation_result(),
            Path("logs/forward_validation"),
            summaries_written=True,
        )

        self.assertIn("Daily/weekly summaries: written", summary)

    def test_main_live_mode_rejects_and_does_not_call_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="live") as boundaries:
                with redirect_stdout(StringIO()):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(boundaries.pipeline.call_count, 0)
        self.assertEqual(boundaries.forward.call_count, 1)

    def test_main_paper_mode_calls_pipeline_and_forward_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper") as boundaries:
                with redirect_stdout(StringIO()):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(boundaries.pipeline.call_count, 1)
        self.assertEqual(boundaries.forward.call_count, 1)
        self.assertEqual(boundaries.forward.call_args.args[1], boundaries.pipeline.return_value.pipeline_result)

    def test_main_success_writes_forward_record_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper", mock_forward=False):
                with redirect_stdout(StringIO()):
                    exit_code = main()

            output_dir = Path(directory)
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "forward_records.csv").exists())
            self.assertTrue((output_dir / "forward_records.jsonl").exists())
            self.assertTrue((output_dir / "daily_summary.csv").exists())
            self.assertTrue((output_dir / "weekly_summary.csv").exists())

    def test_main_live_mode_writes_record_and_summary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="live", mock_forward=False) as boundaries:
                with redirect_stdout(StringIO()):
                    exit_code = main()

            output_dir = Path(directory)
            self.assertEqual(exit_code, 0)
            self.assertEqual(boundaries.pipeline.call_count, 0)
            self.assertTrue((output_dir / "forward_records.csv").exists())
            self.assertTrue((output_dir / "forward_records.jsonl").exists())
            self.assertTrue((output_dir / "daily_summary.csv").exists())
            self.assertTrue((output_dir / "weekly_summary.csv").exists())

    def test_main_does_not_update_summaries_when_record_write_fails(self) -> None:
        failed_result = _validation_result(write_success=False)
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper", forward_result=failed_result):
                with patch("trading_signal_bot.forward_validation_main.load_forward_records_jsonl") as load_records:
                    with patch("trading_signal_bot.forward_validation_main.write_forward_summaries") as write_summaries:
                        with redirect_stdout(StringIO()):
                            exit_code = main()

        self.assertEqual(exit_code, 1)
        load_records.assert_not_called()
        write_summaries.assert_not_called()

    def test_main_returns_one_when_summary_load_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper"):
                with patch(
                    "trading_signal_bot.forward_validation_main.load_forward_records_jsonl",
                    side_effect=ValueError("bad jsonl"),
                ):
                    output = StringIO()
                    with redirect_stdout(output):
                        exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Forward validation failed", output.getvalue())
        self.assertIn("No order was sent.", output.getvalue())

    def test_main_returns_one_when_summary_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper"):
                with patch("trading_signal_bot.forward_validation_main.load_forward_records_jsonl", return_value=()):
                    with patch(
                        "trading_signal_bot.forward_validation_main.write_forward_summaries",
                        side_effect=OSError("cannot write summary"),
                    ):
                        output = StringIO()
                        with redirect_stdout(output):
                            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Forward validation failed", output.getvalue())
        self.assertIn("No MT5 order intent was written.", output.getvalue())

    def test_main_runtime_error_returns_one(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("trading_signal_bot.forward_validation_main.load_env_file"):
                with patch("trading_signal_bot.forward_validation_main.load_signal_config", side_effect=ValueError("bad config")):
                    output = StringIO()
                    with redirect_stdout(output):
                        exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Forward validation failed", output.getvalue())
        self.assertIn("No order was sent.", output.getvalue())

    def test_source_has_no_forbidden_terms(self) -> None:
        source = _source_text()
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)
        self.assertNotIn("trading_signal_order", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper", mock_forward=False):
                with redirect_stdout(StringIO()):
                    main()

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _patched_main_boundaries(directory, mode="paper", mock_forward=False):
                with redirect_stdout(StringIO()):
                    main()

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


class _PatchedMainBoundaries:
    def __init__(
        self,
        directory: str,
        mode: str,
        mock_forward: bool,
        forward_result: ForwardValidationResult | None = None,
    ) -> None:
        self.directory = directory
        self.mode = mode
        self.mock_forward = mock_forward
        self.forward_result = forward_result
        self.pipeline = None
        self.forward = None
        self._patches = []

    def __enter__(self):
        env = _forward_env() | {
            "FORWARD_VALIDATION_OUTPUT_DIR": self.directory,
            "FORWARD_VALIDATION_MODE": self.mode,
        }
        pipeline_patcher = patch(
            "trading_signal_bot.forward_validation_main.run_pipeline_from_configs",
            return_value=SimpleNamespace(pipeline_result=_pipeline_result()),
        )
        self._patches = [
            patch.dict(os.environ, env, clear=True),
            patch("trading_signal_bot.forward_validation_main.load_env_file"),
            patch("trading_signal_bot.forward_validation_main.load_signal_config", return_value=_signal_config()),
            patch("trading_signal_bot.forward_validation_main.load_timeframe_candles", return_value={"M1": _candles()}),
            pipeline_patcher,
        ]
        if self.mock_forward:
            self._patches.append(
                patch(
                    "trading_signal_bot.forward_validation_main.run_forward_validation",
                    return_value=self.forward_result or _validation_result(),
                )
            )
        started = [item.start() for item in self._patches]
        self.pipeline = started[4]
        self.forward = started[5] if self.mock_forward else None
        return self

    def __exit__(self, exc_type, exc, traceback):
        for item in reversed(self._patches):
            item.stop()


def _patched_main_boundaries(
    directory: str,
    *,
    mode: str,
    mock_forward: bool = True,
    forward_result: ForwardValidationResult | None = None,
) -> _PatchedMainBoundaries:
    return _PatchedMainBoundaries(directory, mode, mock_forward, forward_result)


def _forward_env() -> dict[str, str]:
    return {
        "FORWARD_VALIDATION_MODE": "demo",
        "FORWARD_ACTION": "BUY",
        "FORWARD_ENTRY": "2400.0",
        "FORWARD_STOP_LOSS": "2398.0",
        "FORWARD_TP1": "2403.0",
        "FORWARD_TP2": "2405.0",
        "FORWARD_RISK_REWARD": "1.5",
        "FORWARD_CURRENT_PRICE": "2400.5",
        "FORWARD_SPREAD_POINTS": "20",
        "FORWARD_SESSION": "London",
        "FORWARD_ATR_VALUE": "12.5",
        "FORWARD_AVERAGE_ATR": "10.0",
        "FORWARD_HIGH_IMPACT_NEWS_NEARBY": "true",
    }


def _validation_result(*, write_success: bool = True) -> ForwardValidationResult:
    return ForwardValidationResult(
        record=ForwardValidationRecord(
            timestamp="2026-05-21T00:00:00+00:00",
            symbol="XAUUSD",
            mode="paper",
            action="BUY",
            stage="approved",
            approved=True,
            reasons=("ready",),
            entry=2400.0,
            stop_loss=2398.0,
            tp1=2403.0,
            tp2=2405.0,
            risk_reward=1.5,
            execution_plan_present=True,
            risk_decision_present=True,
            order_sent=False,
            order_intent_written=False,
            journal_success=True,
            metadata={},
        ),
        pipeline_result=_pipeline_result(),
        write_success=write_success,
        error_message=None if write_success else "write failed",
    )


def _pipeline_result() -> SimpleNamespace:
    return SimpleNamespace(
        approved=True,
        stage="approved",
        reasons=("ready",),
        execution_plan=object(),
        risk_decision=object(),
        journal_results=(),
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
        Candle("2026-05-21 00:00", 100.0, 101.0, 99.0, 100.5, 1000.0),
        Candle("2026-05-21 00:01", 100.5, 101.5, 99.5, 101.0, 1001.0),
        Candle("2026-05-21 00:02", 101.0, 102.0, 100.0, 101.5, 1002.0),
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
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "forward_validation_main.py"


if __name__ == "__main__":
    unittest.main()
