from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from trading_signal_bot.forward_validation import (
    ForwardValidationConfig,
    ForwardValidationInput,
    ForwardValidationRecord,
    ForwardValidationResult,
)
from trading_signal_bot.models import Candle, SignalAction
from trading_signal_bot.realtime_forward_runner import (
    RealtimeForwardDecision,
    RealtimeForwardRunnerConfig,
    RealtimeForwardState,
    build_realtime_preflight_pipeline_result,
    build_realtime_dry_run_market_input,
    build_realtime_forward_state_after_result,
    build_realtime_forward_validation_input,
    build_realtime_signal_candidate_input,
    build_realtime_signal_pipeline_result,
    default_realtime_forward_config,
    has_realtime_trade_levels,
    latest_execution_candle_time,
    load_realtime_forward_state,
    missing_realtime_trade_level_reasons,
    run_realtime_forward_loop,
    run_realtime_forward_once,
    should_process_snapshot,
    snapshot_to_market_metadata,
    stop_file_exists,
    write_realtime_forward_state,
)
from trading_signal_bot.realtime_market_data import RealtimeMarketSnapshot


class RealtimeForwardRunnerTest(unittest.TestCase):
    def test_create_realtime_forward_runner_config(self) -> None:
        config = RealtimeForwardRunnerConfig(
            symbol="XAUUSD",
            timeframes=("M1",),
            execution_timeframe="M1",
            candle_count=100,
            interval_seconds=30,
            output_dir=Path("logs/forward_validation"),
            state_path=Path("logs/forward_validation/realtime_state.json"),
            stop_file_path=Path("logs/forward_validation/STOP_REALTIME_FORWARD"),
            mode="paper",
            max_iterations=1,
            session="London",
            high_impact_news_nearby=True,
        )

        self.assertEqual(config.symbol, "XAUUSD")
        self.assertTrue(config.high_impact_news_nearby)

    def test_create_realtime_forward_state(self) -> None:
        state = RealtimeForwardState(
            last_processed_candle_time="2026-05-28T09:00:00+00:00",
            last_run_timestamp="2026-05-28T09:01:00+00:00",
            last_stage="approved",
            last_approved=True,
            last_reasons=("ready",),
            last_record_written=True,
        )

        self.assertEqual(state.last_stage, "approved")
        self.assertTrue(state.last_record_written)

    def test_create_realtime_forward_decision(self) -> None:
        decision = RealtimeForwardDecision(True, None, "2026-05-28T09:00:00+00:00")

        self.assertTrue(decision.should_process)
        self.assertIsNone(decision.reason)

    def test_default_realtime_forward_config_values(self) -> None:
        output_dir = Path("logs/forward_validation")
        config = default_realtime_forward_config("XAUUSD", output_dir)

        self.assertEqual(config.timeframes, ("H4", "H1", "M30", "M15", "M5", "M1"))
        self.assertEqual(config.execution_timeframe, "M1")
        self.assertEqual(config.candle_count, 300)
        self.assertEqual(config.interval_seconds, 60)
        self.assertEqual(config.state_path, output_dir / "realtime_state.json")
        self.assertEqual(config.stop_file_path, output_dir / "STOP_REALTIME_FORWARD")
        self.assertEqual(config.mode, "paper")

    def test_load_realtime_forward_state_missing_file_returns_blank_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = load_realtime_forward_state(Path(directory) / "missing.json")

        self.assertEqual(state, RealtimeForwardState())

    def test_write_realtime_forward_state_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "realtime_state.json"
            write_realtime_forward_state(_state(), path)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["last_processed_candle_time"], "2026-05-28T09:00:00+00:00")
        self.assertEqual(payload["last_reasons"], ["ready"])

    def test_write_read_realtime_forward_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realtime_state.json"
            write_realtime_forward_state(_state(), path)

            loaded = load_realtime_forward_state(path)

        self.assertEqual(loaded, _state())

    def test_load_realtime_forward_state_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realtime_state.json"
            path.write_text("{bad", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid realtime forward state JSON"):
                load_realtime_forward_state(path)

    def test_load_realtime_forward_state_reasons_list_becomes_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realtime_state.json"
            path.write_text(json.dumps({"last_reasons": ["a", "b"]}), encoding="utf-8")

            state = load_realtime_forward_state(path)

        self.assertEqual(state.last_reasons, ("a", "b"))

    def test_load_realtime_forward_state_reasons_string_becomes_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realtime_state.json"
            path.write_text(json.dumps({"last_reasons": "a"}), encoding="utf-8")

            state = load_realtime_forward_state(path)

        self.assertEqual(state.last_reasons, ("a",))

    def test_latest_execution_candle_time_returns_latest_timestamp(self) -> None:
        self.assertEqual(latest_execution_candle_time(_snapshot(), "M1"), "2026-05-28T09:01:00+00:00")

    def test_latest_execution_candle_time_supports_lowercase_timeframe(self) -> None:
        self.assertEqual(latest_execution_candle_time(_snapshot(), "m1"), "2026-05-28T09:01:00+00:00")

    def test_latest_execution_candle_time_missing_timeframe_returns_none(self) -> None:
        self.assertIsNone(latest_execution_candle_time(_snapshot(), "M5"))

    def test_latest_execution_candle_time_empty_candles_returns_none(self) -> None:
        snapshot = _snapshot(candles_by_timeframe={"M1": []})

        self.assertIsNone(latest_execution_candle_time(snapshot, "M1"))

    def test_should_process_snapshot_missing_execution_candle(self) -> None:
        decision = should_process_snapshot(_snapshot(candles_by_timeframe={}), RealtimeForwardState(), "M1")

        self.assertFalse(decision.should_process)
        self.assertEqual(decision.reason, "missing execution candle")

    def test_should_process_snapshot_duplicate_candle(self) -> None:
        state = RealtimeForwardState(last_processed_candle_time="2026-05-28T09:01:00+00:00")

        decision = should_process_snapshot(_snapshot(), state, "M1")

        self.assertFalse(decision.should_process)
        self.assertEqual(decision.reason, "duplicate execution candle")

    def test_should_process_snapshot_new_candle(self) -> None:
        decision = should_process_snapshot(_snapshot(), RealtimeForwardState(), "M1")

        self.assertTrue(decision.should_process)
        self.assertIsNone(decision.reason)

    def test_snapshot_to_market_metadata_fields(self) -> None:
        metadata = snapshot_to_market_metadata(_snapshot(), "2026-05-28T09:01:00+00:00")

        self.assertEqual(metadata["bid"], 100.0)
        self.assertEqual(metadata["ask"], 100.2)
        self.assertEqual(metadata["current_price"], 100.1)
        self.assertEqual(metadata["spread_points"], 20.0)
        self.assertEqual(metadata["snapshot_timestamp"], "2026-05-28T09:02:00+00:00")
        self.assertTrue(metadata["market_open"])
        self.assertEqual(metadata["snapshot_errors"], ["none"])
        self.assertEqual(metadata["latest_execution_candle_time"], "2026-05-28T09:01:00+00:00")

    def test_build_realtime_forward_validation_input_maps_snapshot_and_config(self) -> None:
        validation_input = build_realtime_forward_validation_input(
            _snapshot(),
            _config(),
            "2026-05-28T09:01:00+00:00",
        )

        self.assertEqual(validation_input.symbol, "XAUUSD")
        self.assertEqual(validation_input.mode, "paper")
        self.assertIsNone(validation_input.action)
        self.assertIsNone(validation_input.entry)
        self.assertIsNone(validation_input.stop_loss)
        self.assertIsNone(validation_input.tp1)
        self.assertIsNone(validation_input.tp2)
        self.assertIsNone(validation_input.risk_reward)
        self.assertEqual(validation_input.current_price, 100.1)
        self.assertEqual(validation_input.spread_points, 20.0)
        self.assertEqual(validation_input.session, "London")
        self.assertEqual(validation_input.metadata["latest_execution_candle_time"], "2026-05-28T09:01:00+00:00")

    def test_build_realtime_signal_candidate_input_wait_maps_action_without_levels(self) -> None:
        validation_input = build_realtime_signal_candidate_input(
            _snapshot(),
            _config(),
            _signal_config(),
            "2026-05-28T09:01:00+00:00",
            generate_signal_fn=lambda candles, config, timeframe_candles: _signal(
                SignalAction.WAIT,
                entry=None,
                stop_loss=None,
                take_profit=None,
                risk_reward=None,
                reason="wait reason",
                no_trade_reason="no setup",
            ),
        )

        self.assertEqual(validation_input.action, SignalAction.WAIT)
        self.assertIsNone(validation_input.entry)
        self.assertIsNone(validation_input.stop_loss)
        self.assertIsNone(validation_input.tp1)
        self.assertIsNone(validation_input.risk_reward)
        self.assertEqual(validation_input.metadata["signal_action"], "WAIT")
        self.assertEqual(validation_input.metadata["signal_reason"], "wait reason")
        self.assertEqual(validation_input.metadata["no_trade_reason"], "no setup")

    def test_build_realtime_signal_candidate_input_buy_maps_levels(self) -> None:
        validation_input = build_realtime_signal_candidate_input(
            _snapshot(),
            _config(),
            _signal_config(),
            "2026-05-28T09:01:00+00:00",
            generate_signal_fn=lambda candles, config, timeframe_candles: _signal(
                SignalAction.BUY,
                entry=100.5,
                stop_loss=99.0,
                take_profit=103.5,
                risk_reward=2.0,
            ),
        )

        self.assertEqual(validation_input.action, SignalAction.BUY)
        self.assertEqual(validation_input.entry, 100.5)
        self.assertEqual(validation_input.stop_loss, 99.0)
        self.assertEqual(validation_input.tp1, 103.5)
        self.assertIsNone(validation_input.tp2)
        self.assertEqual(validation_input.risk_reward, 2.0)

    def test_build_realtime_signal_candidate_input_sell_maps_action(self) -> None:
        validation_input = build_realtime_signal_candidate_input(
            _snapshot(),
            _config(),
            _signal_config(),
            "2026-05-28T09:01:00+00:00",
            generate_signal_fn=lambda candles, config, timeframe_candles: _signal(
                SignalAction.SELL,
                entry=100.5,
                stop_loss=102.0,
                take_profit=97.5,
                risk_reward=2.0,
            ),
        )

        self.assertEqual(validation_input.action, SignalAction.SELL)

    def test_build_realtime_signal_candidate_input_missing_execution_candles_is_safe(self) -> None:
        validation_input = build_realtime_signal_candidate_input(
            _snapshot(candles_by_timeframe={}),
            _config(),
            _signal_config(),
            None,
            generate_signal_fn=lambda candles, config, timeframe_candles: self.fail("should not generate signal"),
        )

        self.assertIsNone(validation_input.action)
        self.assertIsNone(validation_input.entry)
        self.assertIn("missing execution candles", validation_input.metadata["missing_trade_level_reasons"])

    def test_build_realtime_signal_candidate_input_preserves_snapshot_metadata(self) -> None:
        validation_input = build_realtime_signal_candidate_input(
            _snapshot(),
            _config(),
            _signal_config(),
            "2026-05-28T09:01:00+00:00",
            generate_signal_fn=lambda candles, config, timeframe_candles: _signal(SignalAction.WAIT),
        )

        self.assertEqual(validation_input.metadata["bid"], 100.0)
        self.assertEqual(validation_input.metadata["ask"], 100.2)
        self.assertEqual(validation_input.metadata["current_price"], 100.1)
        self.assertEqual(validation_input.metadata["spread_points"], 20.0)
        self.assertEqual(validation_input.metadata["latest_execution_candle_time"], "2026-05-28T09:01:00+00:00")

    def test_build_realtime_dry_run_market_input_maps_snapshot_and_config(self) -> None:
        market_input = build_realtime_dry_run_market_input(_snapshot(), _config())

        self.assertEqual(market_input.current_price, 100.1)
        self.assertEqual(market_input.spread_points, 20.0)
        self.assertEqual(market_input.session, "London")
        self.assertTrue(market_input.high_impact_news_nearby)
        self.assertIsNone(market_input.atr_value)
        self.assertIsNone(market_input.average_atr)

    def test_has_realtime_trade_levels_false_when_action_missing(self) -> None:
        self.assertFalse(has_realtime_trade_levels(_complete_validation_input(action=None)))

    def test_has_realtime_trade_levels_false_when_entry_missing(self) -> None:
        self.assertFalse(has_realtime_trade_levels(_complete_validation_input(entry=None)))

    def test_has_realtime_trade_levels_false_when_stop_loss_missing(self) -> None:
        self.assertFalse(has_realtime_trade_levels(_complete_validation_input(stop_loss=None)))

    def test_has_realtime_trade_levels_false_when_tp_missing(self) -> None:
        self.assertFalse(has_realtime_trade_levels(_complete_validation_input(tp1=None, tp2=None)))

    def test_has_realtime_trade_levels_false_when_risk_reward_missing(self) -> None:
        self.assertFalse(has_realtime_trade_levels(_complete_validation_input(risk_reward=None)))

    def test_has_realtime_trade_levels_true_when_trade_levels_complete(self) -> None:
        self.assertTrue(has_realtime_trade_levels(_complete_validation_input()))

    def test_missing_realtime_trade_level_reasons_ignore_action_none(self) -> None:
        self.assertEqual(missing_realtime_trade_level_reasons(_complete_validation_input(action=None)), ())

    def test_missing_realtime_trade_level_reasons_ignore_wait(self) -> None:
        self.assertEqual(missing_realtime_trade_level_reasons(_complete_validation_input(action=SignalAction.WAIT)), ())

    def test_missing_realtime_trade_level_reasons_entry(self) -> None:
        self.assertEqual(missing_realtime_trade_level_reasons(_complete_validation_input(entry=None)), ("missing entry",))

    def test_missing_realtime_trade_level_reasons_stop_loss(self) -> None:
        self.assertEqual(
            missing_realtime_trade_level_reasons(_complete_validation_input(stop_loss=None)),
            ("missing stop loss",),
        )

    def test_missing_realtime_trade_level_reasons_take_profit(self) -> None:
        self.assertEqual(
            missing_realtime_trade_level_reasons(_complete_validation_input(tp1=None, tp2=None)),
            ("missing take profit",),
        )

    def test_missing_realtime_trade_level_reasons_risk_reward(self) -> None:
        self.assertEqual(
            missing_realtime_trade_level_reasons(_complete_validation_input(risk_reward=None)),
            ("missing risk reward",),
        )

    def test_missing_realtime_trade_level_reasons_complete_is_empty(self) -> None:
        self.assertEqual(missing_realtime_trade_level_reasons(_complete_validation_input()), ())

    def test_build_realtime_preflight_pipeline_result_schema(self) -> None:
        result = build_realtime_preflight_pipeline_result(
            "realtime_preflight",
            ("missing realtime trade levels",),
        )

        self.assertEqual(result.stage, "realtime_preflight")
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("missing realtime trade levels",))
        self.assertIsNone(result.execution_plan)
        self.assertIsNone(result.risk_decision)
        self.assertEqual(tuple(result.journal_results), ())

    def test_build_realtime_signal_pipeline_result_schema(self) -> None:
        result = build_realtime_signal_pipeline_result(
            "realtime_signal_invalid",
            ("missing entry",),
        )

        self.assertEqual(result.stage, "realtime_signal_invalid")
        self.assertFalse(result.approved)
        self.assertEqual(result.reasons, ("missing entry",))
        self.assertIsNone(result.execution_plan)
        self.assertIsNone(result.risk_decision)
        self.assertEqual(tuple(result.journal_results), ())

    def test_build_realtime_forward_state_after_result_maps_fields(self) -> None:
        state = build_realtime_forward_state_after_result(
            latest_candle_time="2026-05-28T09:01:00+00:00",
            stage="approved",
            approved=True,
            reasons=("ready",),
            record_written=True,
            timestamp="2026-05-28T09:02:00+00:00",
        )

        self.assertEqual(state.last_processed_candle_time, "2026-05-28T09:01:00+00:00")
        self.assertEqual(state.last_stage, "approved")
        self.assertTrue(state.last_approved)
        self.assertEqual(state.last_reasons, ("ready",))
        self.assertTrue(state.last_record_written)

    def test_build_realtime_forward_state_after_result_auto_generates_timestamp(self) -> None:
        state = build_realtime_forward_state_after_result(
            latest_candle_time="2026-05-28T09:01:00+00:00",
            stage="approved",
            approved=True,
            reasons=("ready",),
            record_written=True,
        )

        self.assertIsNotNone(state.last_run_timestamp)

    def test_stop_file_exists_missing_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(stop_file_exists(Path(directory) / "STOP_REALTIME_FORWARD"))

    def test_stop_file_exists_existing_file_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STOP_REALTIME_FORWARD"
            path.write_text("stop", encoding="utf-8")

            self.assertTrue(stop_file_exists(path))

    def test_run_once_stop_file_returns_stopped_and_does_not_call_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config_for_dir(Path(directory))
            config.stop_file_path.write_text("stop", encoding="utf-8")
            fetch_snapshot = _FakeFetchSnapshot()
            pipeline = _FakePipeline()

            result = run_realtime_forward_once(
                config,
                _signal_config(),
                _sizing_config(),
                _validation_config(Path(directory)),
                fetch_snapshot=fetch_snapshot,
                run_pipeline=pipeline,
            )

        self.assertEqual(result.status, "stopped")
        self.assertFalse(result.processed)
        self.assertEqual(result.reason, "stop file detected")
        self.assertEqual(fetch_snapshot.calls, 0)
        self.assertEqual(pipeline.calls, 0)

    def test_run_once_missing_execution_candle_skips(self) -> None:
        result = _run_once_with(snapshot=_snapshot(candles_by_timeframe={}))

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "missing execution candle")

    def test_run_once_duplicate_execution_candle_skips_without_pipeline(self) -> None:
        pipeline = _FakePipeline()
        result = _run_once_with(
            state=RealtimeForwardState(last_processed_candle_time="2026-05-28T09:01:00+00:00"),
            run_pipeline=pipeline,
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "duplicate execution candle")
        self.assertEqual(pipeline.calls, 0)

    def test_run_once_new_candle_without_trade_levels_uses_preflight(self) -> None:
        pipeline = _FakePipeline()
        validation = _PreflightValidation()

        result = _run_once_with(run_pipeline=pipeline, run_validation=validation)

        self.assertEqual(result.status, "processed")
        self.assertTrue(result.processed)
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(validation.calls, 1)
        self.assertEqual(validation.pipeline_result.stage, "realtime_preflight")
        self.assertFalse(validation.pipeline_result.approved)
        self.assertEqual(validation.pipeline_result.reasons, ("missing realtime trade levels",))
        self.assertEqual(result.validation_result.record.stage, "realtime_preflight")
        self.assertEqual(result.validation_result.record.reasons, ("missing realtime trade levels",))
        self.assertEqual(result.latest_candle_time, "2026-05-28T09:01:00+00:00")
        self.assertEqual(result.state.last_stage, "realtime_preflight")
        self.assertFalse(result.state.last_approved)
        self.assertEqual(result.state.last_reasons, ("missing realtime trade levels",))

    def test_run_once_preflight_write_success_updates_records_summaries_and_state(self) -> None:
        pipeline = _FakePipeline()
        validation = _PreflightValidation()
        load_records = _FakeLoadRecords()
        write_summaries = _FakeWriteSummaries()
        state_writer = _FakeStateWriter()

        result = _run_once_with(
            run_pipeline=pipeline,
            run_validation=validation,
            load_records=load_records,
            write_summaries=write_summaries,
            write_state=state_writer,
        )

        self.assertEqual(result.status, "processed")
        self.assertTrue(result.processed)
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(load_records.calls, 1)
        self.assertEqual(write_summaries.calls, 1)
        self.assertEqual(state_writer.calls, 1)
        self.assertEqual(state_writer.state.last_stage, "realtime_preflight")
        self.assertEqual(state_writer.state.last_reasons, ("missing realtime trade levels",))

    def test_run_once_wait_signal_does_not_call_pipeline_and_records_signal_stage(self) -> None:
        pipeline = _FakePipeline()
        validation = _PreflightValidation()

        result = _run_once_with(
            run_pipeline=pipeline,
            run_validation=validation,
            build_validation_input=_FakeBuildValidationInput(
                _complete_validation_input(
                    action=SignalAction.WAIT,
                    entry=None,
                    stop_loss=None,
                    tp1=None,
                    tp2=None,
                    risk_reward=None,
                    metadata={"no_trade_reason": "wait for setup"},
                )
            ),
        )

        self.assertEqual(result.status, "processed")
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(validation.calls, 1)
        self.assertEqual(result.validation_result.record.stage, "realtime_signal")
        self.assertEqual(result.validation_result.record.reasons, ("wait for setup",))

    def test_run_once_invalid_signal_levels_do_not_call_pipeline(self) -> None:
        pipeline = _FakePipeline()
        validation = _PreflightValidation()

        result = _run_once_with(
            run_pipeline=pipeline,
            run_validation=validation,
            build_validation_input=_FakeBuildValidationInput(_complete_validation_input(entry=None, stop_loss=None)),
        )

        self.assertEqual(result.status, "processed")
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(result.validation_result.record.stage, "realtime_signal_invalid")
        self.assertEqual(result.validation_result.record.reasons, ("missing entry", "missing stop loss"))

    def test_run_once_build_validation_input_error_returns_error(self) -> None:
        result = _run_once_with(
            build_validation_input=_FakeBuildValidationInput(error=RuntimeError("candidate boom")),
        )

        self.assertEqual(result.status, "error")
        self.assertIn("candidate boom", result.error_message or "")

    def test_run_once_duplicate_candle_does_not_build_validation_input(self) -> None:
        builder = _FakeBuildValidationInput(_complete_validation_input())

        result = _run_once_with(
            state=RealtimeForwardState(last_processed_candle_time="2026-05-28T09:01:00+00:00"),
            build_validation_input=builder,
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(builder.calls, 0)

    def test_run_once_stop_file_priority_does_not_build_validation_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config_for_dir(Path(directory))
            config.stop_file_path.write_text("stop", encoding="utf-8")
            builder = _FakeBuildValidationInput(_complete_validation_input())

            result = run_realtime_forward_once(
                config,
                _signal_config(),
                _sizing_config(),
                _validation_config(Path(directory)),
                fetch_snapshot=_FakeFetchSnapshot(),
                run_pipeline=_FakePipeline(),
                run_validation=_PreflightValidation(),
                load_state=_FakeLoadState(RealtimeForwardState()),
                write_state=_FakeStateWriter(),
                load_records=_FakeLoadRecords(),
                write_summaries=_FakeWriteSummaries(),
                build_validation_input=builder,
            )

        self.assertEqual(result.status, "stopped")
        self.assertEqual(builder.calls, 0)

    def test_run_once_with_complete_trade_levels_calls_pipeline_and_validation(self) -> None:
        pipeline = _FakePipeline()
        validation = _FakeValidation()

        result = _run_once_with(
            run_pipeline=pipeline,
            run_validation=validation,
            build_validation_input=_FakeBuildValidationInput(_complete_validation_input()),
        )

        self.assertEqual(result.status, "processed")
        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(validation.calls, 1)

    def test_run_once_success_returns_processed_and_writes_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_writer = _FakeStateWriter()
            result = _run_once_with(
                directory=Path(directory),
                write_state=state_writer,
                build_validation_input=_FakeBuildValidationInput(_complete_validation_input()),
            )

        self.assertEqual(result.status, "processed")
        self.assertTrue(result.processed)
        self.assertEqual(state_writer.calls, 1)
        self.assertEqual(result.state.last_processed_candle_time, "2026-05-28T09:01:00+00:00")
        self.assertEqual(result.state.last_stage, "approved")
        self.assertTrue(result.state.last_approved)
        self.assertEqual(result.state.last_reasons, ("ready",))

    def test_run_once_record_write_failure_returns_record_failed(self) -> None:
        load_records = _FakeLoadRecords()
        state_writer = _FakeStateWriter()

        result = _run_once_with(
            run_validation=_FakeValidation(_validation_result(write_success=False)),
            load_records=load_records,
            write_state=state_writer,
        )

        self.assertEqual(result.status, "record_failed")
        self.assertFalse(result.processed)
        self.assertEqual(result.reason, "forward validation record write failed")
        self.assertEqual(load_records.calls, 0)
        self.assertEqual(state_writer.calls, 0)

    def test_run_once_load_records_failure_returns_summary_failed(self) -> None:
        state_writer = _FakeStateWriter()

        result = _run_once_with(
            load_records=_FakeLoadRecords(error=ValueError("bad jsonl")),
            write_state=state_writer,
        )

        self.assertEqual(result.status, "summary_failed")
        self.assertFalse(result.processed)
        self.assertEqual(result.reason, "forward summary update failed")
        self.assertIn("bad jsonl", result.error_message or "")
        self.assertEqual(state_writer.calls, 0)

    def test_run_once_write_summaries_failure_returns_summary_failed(self) -> None:
        state_writer = _FakeStateWriter()

        result = _run_once_with(
            write_summaries=_FakeWriteSummaries(error=OSError("cannot write")),
            write_state=state_writer,
        )

        self.assertEqual(result.status, "summary_failed")
        self.assertEqual(result.reason, "forward summary update failed")
        self.assertIn("cannot write", result.error_message or "")
        self.assertEqual(state_writer.calls, 0)

    def test_run_once_pipeline_exception_returns_error(self) -> None:
        result = _run_once_with(
            run_pipeline=_FakePipeline(error=RuntimeError("pipeline boom")),
            build_validation_input=_FakeBuildValidationInput(_complete_validation_input()),
        )

        self.assertEqual(result.status, "error")
        self.assertFalse(result.processed)
        self.assertIn("pipeline boom", result.error_message or "")

    def test_run_once_validation_exception_returns_error(self) -> None:
        result = _run_once_with(run_validation=_FakeValidation(error=RuntimeError("validation boom")))

        self.assertEqual(result.status, "error")
        self.assertFalse(result.processed)
        self.assertIn("validation boom", result.error_message or "")

    def test_run_once_state_write_exception_returns_error_without_processed_state(self) -> None:
        result = _run_once_with(write_state=_FakeStateWriter(error=OSError("state boom")))

        self.assertEqual(result.status, "error")
        self.assertFalse(result.processed)
        self.assertIn("state boom", result.error_message or "")
        self.assertNotEqual(result.state.last_processed_candle_time, "2026-05-28T09:01:00+00:00")

    def test_run_once_uses_adapter_result_pipeline_result_attribute(self) -> None:
        validation = _FakeValidation()
        pipeline_result = SimpleNamespace(name="inner")

        _run_once_with(
            run_pipeline=_FakePipeline(return_value=SimpleNamespace(pipeline_result=pipeline_result)),
            run_validation=validation,
            build_validation_input=_FakeBuildValidationInput(_complete_validation_input()),
        )

        self.assertIs(validation.pipeline_result, pipeline_result)

    def test_run_once_uses_adapter_result_directly_when_no_pipeline_result_attribute(self) -> None:
        validation = _FakeValidation()
        pipeline_result = SimpleNamespace(name="direct")

        _run_once_with(
            run_pipeline=_FakePipeline(return_value=pipeline_result),
            run_validation=validation,
            build_validation_input=_FakeBuildValidationInput(_complete_validation_input()),
        )

        self.assertIs(validation.pipeline_result, pipeline_result)

    def test_loop_max_iterations_one_calls_run_once_once(self) -> None:
        runner = _FakeRunOnce([_run_result("processed")])

        result = run_realtime_forward_loop(
            _config(max_iterations=1),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(runner.calls, 1)

    def test_loop_max_iterations_three_calls_run_once_three_times(self) -> None:
        runner = _FakeRunOnce([_run_result("processed"), _run_result("skipped"), _run_result("processed")])

        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(runner.calls, 3)

    def test_loop_stop_file_before_first_run_stops_without_run_once(self) -> None:
        runner = _FakeRunOnce([_run_result("processed")])

        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
            stop_check=_FakeStopCheck([True]),
        )

        self.assertEqual(result.status, "stopped")
        self.assertTrue(result.stopped)
        self.assertEqual(runner.calls, 0)

    def test_loop_once_result_stopped_stops_loop(self) -> None:
        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce([_run_result("stopped")]),
            sleep_fn=_FakeSleep(),
        )

        self.assertEqual(result.status, "stopped")
        self.assertTrue(result.stopped)
        self.assertEqual(result.iterations, 1)

    def test_loop_counts_processed_skipped_and_errors(self) -> None:
        result = run_realtime_forward_loop(
            _config(max_iterations=5),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce(
                [
                    _run_result("processed"),
                    _run_result("skipped"),
                    _run_result("error"),
                    _run_result("record_failed"),
                    _run_result("summary_failed"),
                ]
            ),
            sleep_fn=_FakeSleep(),
            max_errors=5,
        )

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.error_count, 3)

    def test_loop_duplicate_skipped_and_processed_continue(self) -> None:
        runner = _FakeRunOnce([_run_result("skipped"), _run_result("processed"), _run_result("skipped")])

        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.iterations, 3)

    def test_loop_continue_on_error_true_continues_until_max_errors(self) -> None:
        runner = _FakeRunOnce([_run_result("error"), _run_result("processed"), _run_result("error")])

        result = run_realtime_forward_loop(
            _config(max_iterations=5),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
            continue_on_error=True,
            max_errors=2,
        )

        self.assertEqual(result.status, "error_limit_reached")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(runner.calls, 3)

    def test_loop_continue_on_error_false_stops_on_first_error(self) -> None:
        runner = _FakeRunOnce([_run_result("error"), _run_result("processed")])

        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=runner,
            sleep_fn=_FakeSleep(),
            continue_on_error=False,
        )

        self.assertEqual(result.status, "error_limit_reached")
        self.assertEqual(result.iterations, 1)
        self.assertEqual(runner.calls, 1)

    def test_loop_max_errors_returns_error_limit_reached(self) -> None:
        result = run_realtime_forward_loop(
            _config(max_iterations=5),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce([_run_result("error"), _run_result("record_failed")]),
            sleep_fn=_FakeSleep(),
            max_errors=2,
        )

        self.assertEqual(result.status, "error_limit_reached")
        self.assertEqual(result.error_count, 2)

    def test_loop_max_iterations_complete_and_sleep_behavior(self) -> None:
        sleep = _FakeSleep()

        result = run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce([_run_result("processed"), _run_result("processed"), _run_result("processed")]),
            sleep_fn=sleep,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(sleep.calls, [60, 60])

    def test_loop_sleep_not_called_when_stopped(self) -> None:
        sleep = _FakeSleep()

        run_realtime_forward_loop(
            _config(max_iterations=3),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce([_run_result("stopped")]),
            sleep_fn=sleep,
        )

        self.assertEqual(sleep.calls, [])

    def test_loop_invalid_config_values(self) -> None:
        for config, max_errors in (
            (_config(interval_seconds=0, max_iterations=1), 3),
            (_config(max_iterations=0), 3),
            (_config(max_iterations=1), 0),
        ):
            with self.subTest(config=config, max_errors=max_errors):
                result = run_realtime_forward_loop(
                    config,
                    _signal_config(),
                    _sizing_config(),
                    _validation_config(Path("logs/forward_validation")),
                    run_once=_FakeRunOnce([_run_result("processed")]),
                    sleep_fn=_FakeSleep(),
                    max_errors=max_errors,
                )

                self.assertEqual(result.status, "invalid_config")

    def test_loop_last_result_and_stopped_flag(self) -> None:
        last = _run_result("skipped")

        result = run_realtime_forward_loop(
            _config(max_iterations=2),
            _signal_config(),
            _sizing_config(),
            _validation_config(Path("logs/forward_validation")),
            run_once=_FakeRunOnce([_run_result("processed"), last]),
            sleep_fn=_FakeSleep(),
        )

        self.assertIs(result.last_result, last)
        self.assertFalse(result.stopped)
        self.assertEqual(result.iterations, 2)

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


def _config(
    *,
    interval_seconds: int = 60,
    max_iterations: int | None = None,
) -> RealtimeForwardRunnerConfig:
    output_dir = Path("logs/forward_validation")
    return RealtimeForwardRunnerConfig(
        symbol="XAUUSD",
        timeframes=("M1",),
        execution_timeframe="M1",
        candle_count=300,
        interval_seconds=interval_seconds,
        output_dir=output_dir,
        state_path=output_dir / "realtime_state.json",
        stop_file_path=output_dir / "STOP_REALTIME_FORWARD",
        mode="paper",
        max_iterations=max_iterations,
        session="London",
        high_impact_news_nearby=True,
    )


def _config_for_dir(directory: Path) -> RealtimeForwardRunnerConfig:
    return RealtimeForwardRunnerConfig(
        symbol="XAUUSD",
        timeframes=("M1",),
        execution_timeframe="M1",
        candle_count=300,
        interval_seconds=60,
        output_dir=directory,
        state_path=directory / "realtime_state.json",
        stop_file_path=directory / "STOP_REALTIME_FORWARD",
        mode="paper",
        session="London",
        high_impact_news_nearby=True,
    )


def _state() -> RealtimeForwardState:
    return RealtimeForwardState(
        last_processed_candle_time="2026-05-28T09:00:00+00:00",
        last_run_timestamp="2026-05-28T09:01:00+00:00",
        last_stage="approved",
        last_approved=True,
        last_reasons=("ready",),
        last_record_written=True,
    )


def _signal_config() -> SimpleNamespace:
    return SimpleNamespace(symbol="XAUUSD")


def _sizing_config() -> SimpleNamespace:
    return SimpleNamespace(mode="paper")


def _validation_config(directory: Path) -> ForwardValidationConfig:
    return ForwardValidationConfig(
        record_csv_path=directory / "forward_records.csv",
        record_jsonl_path=directory / "forward_records.jsonl",
        daily_summary_path=directory / "daily_summary.csv",
        weekly_summary_path=directory / "weekly_summary.csv",
    )


def _signal(
    action: SignalAction,
    *,
    entry: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    risk_reward: float | None = None,
    reason: str = "signal reason",
    no_trade_reason: str = "no trade",
):
    return SimpleNamespace(
        action=action,
        reason=reason,
        no_trade_reason=no_trade_reason,
        levels=SimpleNamespace(
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
        ),
    )


def _complete_validation_input(
    *,
    action: SignalAction | None = SignalAction.BUY,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    tp1: float | None = 102.0,
    tp2: float | None = None,
    risk_reward: float | None = 2.0,
    metadata: dict[str, object] | None = None,
) -> ForwardValidationInput:
    return ForwardValidationInput(
        symbol="XAUUSD",
        mode="paper",
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        current_price=100.1,
        spread_points=20.0,
        session="London",
        metadata=metadata or {"latest_execution_candle_time": "2026-05-28T09:01:00+00:00"},
    )


def _no_level_validation_input() -> ForwardValidationInput:
    return _complete_validation_input(
        action=None,
        entry=None,
        stop_loss=None,
        tp1=None,
        tp2=None,
        risk_reward=None,
    )


def _validation_result(*, write_success: bool = True) -> ForwardValidationResult:
    return ForwardValidationResult(
        record=ForwardValidationRecord(
            timestamp="2026-05-28T09:02:00+00:00",
            symbol="XAUUSD",
            mode="paper",
            action=None,
            stage="approved",
            approved=True,
            reasons=("ready",),
            entry=None,
            stop_loss=None,
            tp1=None,
            tp2=None,
            risk_reward=None,
            execution_plan_present=True,
            risk_decision_present=True,
            order_sent=False,
            order_intent_written=False,
            journal_success=True,
            metadata={},
        ),
        pipeline_result=SimpleNamespace(stage="approved"),
        write_success=write_success,
        error_message=None if write_success else "write failed",
    )


def _snapshot(
    *,
    candles_by_timeframe: dict[str, list[Candle]] | None = None,
) -> RealtimeMarketSnapshot:
    return RealtimeMarketSnapshot(
        symbol="XAUUSD",
        candles_by_timeframe=candles_by_timeframe
        if candles_by_timeframe is not None
        else {
            "M1": [
                Candle("2026-05-28T09:00:00+00:00", 100.0, 101.0, 99.0, 100.0, 1000.0),
                Candle("2026-05-28T09:01:00+00:00", 100.0, 101.0, 99.0, 100.5, 1001.0),
            ]
        },
        bid=100.0,
        ask=100.2,
        current_price=100.1,
        spread_points=20.0,
        timestamp=datetime(2026, 5, 28, 9, 2, tzinfo=timezone.utc),
        market_open=True,
        errors=("none",),
    )


def _run_once_with(
    *,
    directory: Path | None = None,
    snapshot: RealtimeMarketSnapshot | None = None,
    state: RealtimeForwardState | None = None,
    run_pipeline=None,
    run_validation=None,
    load_records=None,
    write_summaries=None,
    write_state=None,
    build_validation_input=None,
):
    if directory is None:
        temp = tempfile.TemporaryDirectory()
        directory_path = Path(temp.name)
    else:
        temp = None
        directory_path = directory
    try:
        return run_realtime_forward_once(
            _config_for_dir(directory_path),
            _signal_config(),
            _sizing_config(),
            _validation_config(directory_path),
            fetch_snapshot=_FakeFetchSnapshot(snapshot or _snapshot()),
            run_pipeline=run_pipeline or _FakePipeline(),
            run_validation=run_validation or _FakeValidation(),
            load_state=_FakeLoadState(state or RealtimeForwardState()),
            write_state=write_state or _FakeStateWriter(),
            load_records=load_records or _FakeLoadRecords(),
            write_summaries=write_summaries or _FakeWriteSummaries(),
            build_validation_input=build_validation_input or _FakeBuildValidationInput(_no_level_validation_input()),
        )
    finally:
        if temp is not None:
            temp.cleanup()


class _FakeFetchSnapshot:
    def __init__(self, snapshot: RealtimeMarketSnapshot | None = None) -> None:
        self.snapshot = snapshot or _snapshot()
        self.calls = 0

    def __call__(self, symbol, timeframes, candle_count):
        self.calls += 1
        return self.snapshot


class _FakePipeline:
    def __init__(self, return_value=None, error: Exception | None = None) -> None:
        self.return_value = return_value or SimpleNamespace(pipeline_result=SimpleNamespace(stage="approved"))
        self.error = error
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.return_value


class _FakeValidation:
    def __init__(self, return_value: ForwardValidationResult | None = None, error: Exception | None = None) -> None:
        self.return_value = return_value or _validation_result()
        self.error = error
        self.calls = 0
        self.pipeline_result = None

    def __call__(self, validation_input, pipeline_result, validation_config):
        self.calls += 1
        self.pipeline_result = pipeline_result
        if self.error is not None:
            raise self.error
        return self.return_value


class _FakeBuildValidationInput:
    def __init__(self, return_value: ForwardValidationInput | None = None, error: Exception | None = None) -> None:
        self.return_value = return_value or _no_level_validation_input()
        self.error = error
        self.calls = 0

    def __call__(self, snapshot, config, signal_config, latest_candle_time):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.return_value


class _PreflightValidation:
    def __init__(self) -> None:
        self.calls = 0
        self.pipeline_result = None

    def __call__(self, validation_input, pipeline_result, validation_config):
        self.calls += 1
        self.pipeline_result = pipeline_result
        return ForwardValidationResult(
            record=ForwardValidationRecord(
                timestamp="2026-05-28T09:02:00+00:00",
                symbol=validation_input.symbol,
                mode=validation_input.mode,
                action=None,
                stage=pipeline_result.stage,
                approved=pipeline_result.approved,
                reasons=pipeline_result.reasons,
                entry=validation_input.entry,
                stop_loss=validation_input.stop_loss,
                tp1=validation_input.tp1,
                tp2=validation_input.tp2,
                risk_reward=validation_input.risk_reward,
                execution_plan_present=pipeline_result.execution_plan is not None,
                risk_decision_present=pipeline_result.risk_decision is not None,
                order_sent=False,
                order_intent_written=False,
                journal_success=True,
                metadata=validation_input.metadata,
            ),
            pipeline_result=pipeline_result,
            write_success=True,
            error_message=None,
        )


class _FakeLoadState:
    def __init__(self, state: RealtimeForwardState) -> None:
        self.state = state

    def __call__(self, path):
        return self.state


class _FakeStateWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.state = None

    def __call__(self, state, path):
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.state = state


class _FakeLoadRecords:
    def __init__(self, records=(), error: Exception | None = None) -> None:
        self.records = records
        self.error = error
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.records


class _FakeWriteSummaries:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, records, config):
        self.calls += 1
        if self.error is not None:
            raise self.error


class _FakeRunOnce:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, config, signal_config, sizing_config, validation_config):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return _run_result("processed")


class _FakeSleep:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class _FakeStopCheck:
    def __init__(self, results) -> None:
        self.results = list(results)

    def __call__(self, path):
        if self.results:
            return self.results.pop(0)
        return False


def _run_result(status: str):
    return SimpleNamespace(
        status=status,
        processed=status == "processed",
        reason=None,
        latest_candle_time="2026-05-28T09:01:00+00:00",
        state=RealtimeForwardState(),
        validation_result=None,
        snapshot=None,
        error_message=None,
    )


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "realtime_forward_runner.py"


if __name__ == "__main__":
    unittest.main()
