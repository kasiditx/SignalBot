from __future__ import annotations

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading_signal_bot.realtime_forward_main import (
    _get_bool_env,
    _get_int_env,
    _get_optional_int_env,
    _optional_text,
    build_forward_validation_config_from_realtime_output_dir,
    build_realtime_forward_runner_config_from_env,
    build_safe_realtime_paper_sizing_config,
    format_realtime_forward_error,
    format_realtime_forward_summary,
    main,
    realtime_forward_output_dir_from_env,
)
from trading_signal_bot.realtime_forward_runner import RealtimeForwardLoopResult


class RealtimeForwardMainTest(unittest.TestCase):
    def test_get_int_env_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_get_int_env("MISSING_INT", 60), 60)

    def test_get_int_env_valid_int(self) -> None:
        with patch.dict(os.environ, {"VALUE": "42"}, clear=True):
            self.assertEqual(_get_int_env("VALUE", 1), 42)

    def test_get_int_env_invalid_int_raises(self) -> None:
        with patch.dict(os.environ, {"VALUE": "abc"}, clear=True):
            with self.assertRaisesRegex(ValueError, "VALUE must be an integer"):
                _get_int_env("VALUE", 1)

    def test_get_int_env_below_minimum_raises(self) -> None:
        with patch.dict(os.environ, {"VALUE": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "VALUE must be >= 1"):
                _get_int_env("VALUE", 1, minimum=1)

    def test_get_optional_int_env_none_or_empty_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_get_optional_int_env("VALUE"))
        with patch.dict(os.environ, {"VALUE": ""}, clear=True):
            self.assertIsNone(_get_optional_int_env("VALUE"))

    def test_get_optional_int_env_valid_int(self) -> None:
        with patch.dict(os.environ, {"VALUE": "3"}, clear=True):
            self.assertEqual(_get_optional_int_env("VALUE"), 3)

    def test_get_optional_int_env_invalid_raises(self) -> None:
        with patch.dict(os.environ, {"VALUE": "abc"}, clear=True):
            with self.assertRaisesRegex(ValueError, "VALUE must be an integer"):
                _get_optional_int_env("VALUE")

    def test_get_bool_env_true_variants(self) -> None:
        for value in ("true", "1", "yes", "y", "on"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"FLAG": value}, clear=True):
                    self.assertTrue(_get_bool_env("FLAG", False))

    def test_get_bool_env_false_variants(self) -> None:
        for value in ("false", "0", "no", "n", "off"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"FLAG": value}, clear=True):
                    self.assertFalse(_get_bool_env("FLAG", True))

    def test_get_bool_env_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(_get_bool_env("FLAG", True))

    def test_get_bool_env_invalid_raises(self) -> None:
        with patch.dict(os.environ, {"FLAG": "maybe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "FLAG must be true or false"):
                _get_bool_env("FLAG", False)

    def test_optional_text(self) -> None:
        self.assertIsNone(_optional_text(None))
        self.assertIsNone(_optional_text(""))
        self.assertEqual(_optional_text(" London "), "London")

    def test_output_dir_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(realtime_forward_output_dir_from_env(), Path("logs/forward_validation"))

    def test_output_dir_override(self) -> None:
        with patch.dict(os.environ, {"REALTIME_FORWARD_OUTPUT_DIR": "tmp/realtime"}, clear=True):
            self.assertEqual(realtime_forward_output_dir_from_env(), Path("tmp/realtime"))

    def test_build_forward_validation_config_paths(self) -> None:
        config = build_forward_validation_config_from_realtime_output_dir(Path("tmp/realtime"))

        self.assertEqual(config.record_csv_path, Path("tmp/realtime/forward_records.csv"))
        self.assertEqual(config.record_jsonl_path, Path("tmp/realtime/forward_records.jsonl"))
        self.assertEqual(config.daily_summary_path, Path("tmp/realtime/daily_summary.csv"))
        self.assertEqual(config.weekly_summary_path, Path("tmp/realtime/weekly_summary.csv"))

    def test_build_runner_config_defaults_from_signal_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = build_realtime_forward_runner_config_from_env(_signal_config())

        self.assertEqual(config.symbol, "XAUUSD")
        self.assertEqual(config.mode, "paper")
        self.assertEqual(config.execution_timeframe, "M1")
        self.assertEqual(config.candle_count, 300)
        self.assertEqual(config.interval_seconds, 60)
        self.assertIsNone(config.max_iterations)
        self.assertIsNone(config.session)
        self.assertFalse(config.high_impact_news_nearby)
        self.assertEqual(config.state_path, Path("logs/forward_validation/realtime_state.json"))
        self.assertEqual(config.stop_file_path, Path("logs/forward_validation/STOP_REALTIME_FORWARD"))

    def test_build_runner_config_env_overrides(self) -> None:
        env = {
            "REALTIME_FORWARD_SYMBOL": "EURUSD",
            "REALTIME_FORWARD_MODE": "demo",
            "REALTIME_FORWARD_OUTPUT_DIR": "tmp/realtime",
            "REALTIME_FORWARD_EXECUTION_TIMEFRAME": "m5",
            "REALTIME_FORWARD_CANDLE_COUNT": "150",
            "REALTIME_FORWARD_INTERVAL_SECONDS": "30",
            "REALTIME_FORWARD_MAX_ITERATIONS": "2",
            "REALTIME_FORWARD_SESSION": "London",
            "REALTIME_HIGH_IMPACT_NEWS_NEARBY": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            config = build_realtime_forward_runner_config_from_env(_signal_config())

        self.assertEqual(config.symbol, "EURUSD")
        self.assertEqual(config.mode, "demo")
        self.assertEqual(config.output_dir, Path("tmp/realtime"))
        self.assertEqual(config.execution_timeframe, "M5")
        self.assertEqual(config.candle_count, 150)
        self.assertEqual(config.interval_seconds, 30)
        self.assertEqual(config.max_iterations, 2)
        self.assertEqual(config.session, "London")
        self.assertTrue(config.high_impact_news_nearby)
        self.assertEqual(config.state_path, Path("tmp/realtime/realtime_state.json"))
        self.assertEqual(config.stop_file_path, Path("tmp/realtime/STOP_REALTIME_FORWARD"))

    def test_safe_realtime_paper_sizing_config(self) -> None:
        config = build_safe_realtime_paper_sizing_config()

        self.assertFalse(config.enabled)
        self.assertEqual(config.mode, "paper")
        self.assertGreater(config.account_balance, 0)
        self.assertGreater(config.risk_percent, 0)

    def test_format_realtime_forward_summary_has_required_lines(self) -> None:
        summary = format_realtime_forward_summary(_loop_result("completed"), _runner_config())

        self.assertIn("Realtime Forward Dry-run Summary", summary)
        self.assertIn("Mode:", summary)
        self.assertIn("Symbol:", summary)
        self.assertIn("Status:", summary)
        self.assertIn("Iterations:", summary)
        self.assertIn("Processed:", summary)
        self.assertIn("Skipped:", summary)
        self.assertIn("Errors:", summary)
        self.assertIn("Stopped:", summary)
        self.assertIn("Output directory:", summary)
        self.assertIn("State file:", summary)
        self.assertIn("Stop file:", summary)
        self.assertIn("No order was sent.", summary)
        self.assertIn("No MT5 order intent was written.", summary)
        self.assertIn("Realtime forward dry-run only.", summary)

    def test_format_realtime_forward_error_has_safety_text(self) -> None:
        summary = format_realtime_forward_error("bad config")

        self.assertIn("Realtime forward failed", summary)
        self.assertIn("error=bad config", summary)
        self.assertIn("No order was sent.", summary)
        self.assertIn("No MT5 order intent was written.", summary)
        self.assertIn("Realtime forward dry-run only.", summary)

    def test_main_live_mode_returns_one_and_does_not_call_loop(self) -> None:
        with _patched_main({"REALTIME_FORWARD_MODE": "live"}) as runner:
            with redirect_stdout(StringIO()) as output:
                exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(runner.calls, 0)
        self.assertIn("Realtime forward live mode is not allowed.", output.getvalue())
        self.assertIn("No order was sent.", output.getvalue())

    def test_main_paper_mode_calls_loop(self) -> None:
        with _patched_main({"REALTIME_FORWARD_MODE": "paper"}) as runner:
            with redirect_stdout(StringIO()):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(runner.calls, 1)

    def test_main_return_codes_for_loop_statuses(self) -> None:
        for status, expected in (
            ("completed", 0),
            ("stopped", 0),
            ("error_limit_reached", 1),
            ("invalid_config", 1),
        ):
            with self.subTest(status=status):
                with _patched_main({"REALTIME_FORWARD_MODE": "paper"}, result=_loop_result(status)) as runner:
                    with redirect_stdout(StringIO()):
                        exit_code = main()

                self.assertEqual(exit_code, expected)
                self.assertEqual(runner.calls, 1)

    def test_main_unexpected_exception_returns_one(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("trading_signal_bot.realtime_forward_main.load_env_file", side_effect=ValueError("boom")):
                with redirect_stdout(StringIO()) as output:
                    exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Realtime forward failed", output.getvalue())
        self.assertIn("No MT5 order intent was written.", output.getvalue())

    def test_main_summary_has_safety_text(self) -> None:
        with _patched_main({"REALTIME_FORWARD_MODE": "paper"}):
            with redirect_stdout(StringIO()) as output:
                main()

        self.assertIn("No order was sent.", output.getvalue())
        self.assertIn("No MT5 order intent was written.", output.getvalue())
        self.assertIn("Realtime forward dry-run only.", output.getvalue())

    def test_source_has_no_forbidden_terms(self) -> None:
        source = _source_path().read_text(encoding="utf-8")

        self.assertNotIn("order_send", source)
        self.assertNotIn("auto_trade", source)
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)
        self.assertNotIn("trading_signal_order", source)
        self.assertNotIn("positions_get", source)
        self.assertNotIn("orders_get", source)
        self.assertNotIn("TRADE_ACTION_", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        self.assertFalse(Path("trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        self.assertFalse(Path("logs/trading_signal_order.csv").exists())


class _PatchedMain:
    def __init__(self, env: dict[str, str], result: RealtimeForwardLoopResult | None = None) -> None:
        self.env = env
        self.runner = _FakeLoop(result or _loop_result("completed"))
        self._patches = []

    def __enter__(self):
        self._patches = [
            patch.dict(os.environ, self.env, clear=True),
            patch("trading_signal_bot.realtime_forward_main.load_env_file"),
            patch("trading_signal_bot.realtime_forward_main.load_signal_config", return_value=_signal_config()),
            patch("trading_signal_bot.realtime_forward_main.run_realtime_forward_loop", self.runner),
        ]
        for item in self._patches:
            item.start()
        return self.runner

    def __exit__(self, exc_type, exc, traceback):
        for item in reversed(self._patches):
            item.stop()


class _FakeLoop:
    def __init__(self, result: RealtimeForwardLoopResult) -> None:
        self.result = result
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return self.result


def _patched_main(env: dict[str, str], result: RealtimeForwardLoopResult | None = None) -> _PatchedMain:
    return _PatchedMain(env, result)


def _signal_config() -> SimpleNamespace:
    return SimpleNamespace(symbol="XAUUSD")


def _runner_config() -> SimpleNamespace:
    output_dir = Path("logs/forward_validation")
    return SimpleNamespace(
        mode="paper",
        symbol="XAUUSD",
        output_dir=output_dir,
        state_path=output_dir / "realtime_state.json",
        stop_file_path=output_dir / "STOP_REALTIME_FORWARD",
    )


def _loop_result(status: str) -> RealtimeForwardLoopResult:
    return RealtimeForwardLoopResult(
        status=status,
        iterations=1,
        processed_count=1 if status == "completed" else 0,
        skipped_count=0,
        error_count=1 if status in {"error_limit_reached", "invalid_config"} else 0,
        stopped=status == "stopped",
        last_result=None,
    )


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "realtime_forward_main.py"


if __name__ == "__main__":
    unittest.main()
