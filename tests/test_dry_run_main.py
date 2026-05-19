from __future__ import annotations

import ast
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from trading_signal_bot.dry_run_main import (
    build_adapter_input_from_env,
    build_market_input_from_env,
    format_dry_run_summary,
    main,
    parse_optional_action,
    parse_optional_float,
)
from trading_signal_bot.dry_run_pipeline import DryRunPipelineResult
from trading_signal_bot.models import SignalAction
from trading_signal_bot.pipeline_adapter import DryRunAdapterResult


class DryRunMainTest(unittest.TestCase):
    def test_parse_optional_float_none_returns_none(self) -> None:
        self.assertIsNone(parse_optional_float(None))

    def test_parse_optional_float_empty_returns_none(self) -> None:
        self.assertIsNone(parse_optional_float(""))

    def test_parse_optional_float_numeric_string(self) -> None:
        self.assertEqual(parse_optional_float("123.45"), 123.45)

    def test_parse_optional_float_invalid_raises_clear_error(self) -> None:
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

    def test_parse_optional_action_invalid_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "DRY_RUN_ACTION must be BUY, SELL, WAIT, or empty"):
            parse_optional_action("HOLD")

    def test_build_adapter_input_from_env_defaults_to_no_action_and_paper(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            adapter_input = build_adapter_input_from_env()

            self.assertIsNone(adapter_input.action)
            self.assertEqual(adapter_input.mode, "paper")

    def test_build_adapter_input_from_env_maps_dry_run_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DRY_RUN_ACTION": "BUY",
                "DRY_RUN_ENTRY": "100.1",
                "DRY_RUN_STOP_LOSS": "99.2",
                "DRY_RUN_TP1": "101.3",
                "DRY_RUN_TP2": "102.4",
                "DRY_RUN_RISK_REWARD": "1.7",
                "DRY_RUN_MODE": "demo",
            },
            clear=True,
        ):
            adapter_input = build_adapter_input_from_env()

            self.assertEqual(adapter_input.action, SignalAction.BUY)
            self.assertEqual(adapter_input.entry, 100.1)
            self.assertEqual(adapter_input.stop_loss, 99.2)
            self.assertEqual(adapter_input.tp1, 101.3)
            self.assertEqual(adapter_input.tp2, 102.4)
            self.assertEqual(adapter_input.risk_reward, 1.7)
            self.assertEqual(adapter_input.mode, "demo")

    def test_build_market_input_from_env_maps_market_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DRY_RUN_CURRENT_PRICE": "100.5",
                "DRY_RUN_SPREAD_POINTS": "150",
                "DRY_RUN_ATR_VALUE": "1.2",
                "DRY_RUN_AVERAGE_ATR": "0.8",
                "DRY_RUN_SESSION": "London",
                "DRY_RUN_HIGH_IMPACT_NEWS_NEARBY": "false",
            },
            clear=True,
        ):
            market_input = build_market_input_from_env()

            self.assertEqual(market_input.current_price, 100.5)
            self.assertEqual(market_input.spread_points, 150.0)
            self.assertEqual(market_input.atr_value, 1.2)
            self.assertEqual(market_input.average_atr, 0.8)
            self.assertEqual(market_input.session, "London")
            self.assertFalse(market_input.high_impact_news_nearby)

    def test_high_impact_news_env_parses_true(self) -> None:
        with patch.dict(
            os.environ,
            {"DRY_RUN_CURRENT_PRICE": "100.5", "DRY_RUN_HIGH_IMPACT_NEWS_NEARBY": "true"},
            clear=True,
        ):
            self.assertTrue(build_market_input_from_env().high_impact_news_nearby)

    def test_high_impact_news_env_parses_false(self) -> None:
        with patch.dict(
            os.environ,
            {"DRY_RUN_CURRENT_PRICE": "100.5", "DRY_RUN_HIGH_IMPACT_NEWS_NEARBY": "false"},
            clear=True,
        ):
            self.assertFalse(build_market_input_from_env().high_impact_news_nearby)

    def test_format_dry_run_summary_mentions_no_order_sent(self) -> None:
        summary = format_dry_run_summary(_adapter_result())

        self.assertIn("No order was sent", summary)

    def test_format_dry_run_summary_mentions_no_mt5_intent_written(self) -> None:
        summary = format_dry_run_summary(_adapter_result())

        self.assertIn("No MT5 order intent was written", summary)

    def test_format_dry_run_summary_mentions_dry_run_only(self) -> None:
        summary = format_dry_run_summary(_adapter_result())

        self.assertIn("Dry-run only", summary)

    def test_main_returns_zero_when_dry_run_completes_with_reject(self) -> None:
        with patch.dict(os.environ, {"DRY_RUN_CURRENT_PRICE": "100.0"}, clear=True):
            with _patched_boundaries():
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertIn("approved=False", output.getvalue())
        self.assertIn("No order was sent", output.getvalue())

    def test_main_returns_one_when_runtime_error_prevents_run(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("trading_signal_bot.dry_run_main.load_env_file"):
                with patch("trading_signal_bot.dry_run_main.load_signal_config", side_effect=ValueError("bad config")):
                    output = StringIO()
                    with redirect_stdout(output):
                        exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Dry-run failed before pipeline execution", output.getvalue())
        self.assertIn("No order was sent", output.getvalue())

    def test_source_ast_has_no_auto_trade_module_import(self) -> None:
        imports = _dry_run_main_imports()

        self.assertFalse(any(import_name.endswith("auto_trade") or import_name == "auto_trade" for import_name in imports))

    def test_source_has_no_process_auto_trade_reference(self) -> None:
        source = _dry_run_main_path().read_text(encoding="utf-8")

        self.assertNotIn("process_auto_trade", source)

    def test_source_has_no_order_file_or_mt5_order_path(self) -> None:
        source = _dry_run_main_path().read_text(encoding="utf-8")

        self.assertNotIn("order_file", source)
        self.assertNotIn("trading_signal_order", source)

    def test_main_does_not_create_mt5_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.dict(os.environ, {"DRY_RUN_CURRENT_PRICE": "100.0"}, clear=True):
                with _patched_boundaries():
                    current = Path.cwd()
                    os.chdir(base)
                    try:
                        with redirect_stdout(StringIO()):
                            main()
                    finally:
                        os.chdir(current)

            self.assertFalse((base / "trading_signal_order.csv").exists())
            self.assertFalse((base / "logs" / "trading_signal_order.csv").exists())


def _adapter_result() -> DryRunAdapterResult:
    return DryRunAdapterResult(
        pipeline_result=DryRunPipelineResult(
            approved=False,
            stage="no_trade_filter",
            reasons=("No trade action candidate",),
            execution_plan=None,
            risk_decision=None,
            journal_results=(),
        ),
        message="Dry-run pipeline rejected. No order was sent. No MT5 order intent was written.",
    )


def _patched_boundaries():
    return _BoundaryPatches()


class _BoundaryPatches:
    def __enter__(self):
        self._patches = [
            patch("trading_signal_bot.dry_run_main.load_env_file"),
            patch("trading_signal_bot.dry_run_main.load_signal_config", return_value=object()),
            patch("trading_signal_bot.dry_run_main.load_auto_trade_config", return_value=object()),
            patch("trading_signal_bot.dry_run_main.load_timeframe_candles", return_value={}),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for item in reversed(self._patches):
            item.stop()


def _dry_run_main_imports() -> list[str]:
    tree = ast.parse(_dry_run_main_path().read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return imports


def _dry_run_main_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "dry_run_main.py"


if __name__ == "__main__":
    unittest.main()
