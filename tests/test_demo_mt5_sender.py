from __future__ import annotations

import ast
import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading_signal_bot.demo_execution import DemoExecutionState, DemoOrderIntent, default_demo_execution_config
from trading_signal_bot.demo_mt5_sender import (
    DEMO_ORDER_RECORD_FIELDS,
    DemoOpenPosition,
    DemoSendResult,
    _bool_env_enabled,
    _map_demo_intent_to_mt5_request,
    _mt5_filling_type,
    _mt5_order_type_from_demo_type,
    _mt5_symbol_filling_type,
    _optional_mt5_constant,
    _position_action_from_type,
    _position_value,
    _required_mt5_constant,
    _text_contains_demo,
    _text_contains_live_or_real,
    append_demo_order_record_csv,
    append_demo_order_record_jsonl,
    build_mt5_demo_send_fn,
    build_mt5_order_request_from_demo_request,
    build_demo_send_blocked_result,
    build_demo_request_lifecycle_record,
    build_demo_send_dry_run_result,
    detect_mt5_account_mode,
    demo_order_record_from_result,
    demo_execution_env_enabled,
    demo_sender_stop_active,
    fetch_demo_open_positions,
    load_demo_order_records_jsonl,
    map_mt5_position_to_demo_position,
    send_demo_order,
    validate_demo_open_position_guards,
    validate_demo_pyramiding_guard,
    validate_demo_duplicate_candle,
    validate_demo_duplicate_guards,
    validate_demo_duplicate_signal_id,
    validate_demo_execution_gate,
    validate_demo_position_limits,
    validate_demo_sender_preconditions,
    validate_demo_same_symbol_action_guard,
    verify_mt5_demo_account,
    write_demo_order_record,
)


class DemoMt5SenderTest(unittest.TestCase):
    def test_create_demo_send_result_with_all_fields(self) -> None:
        lifecycle = build_demo_request_lifecycle_record(
            "demo_request_built",
            _intent(),
            approved=True,
            account_mode="demo",
            metadata={"source": "unit-test"},
        )

        result = DemoSendResult(
            attempted=False,
            accepted=False,
            lifecycle_record=lifecycle,
            request={"symbol": "XAUUSD"},
            error_message=None,
        )

        self.assertFalse(result.attempted)
        self.assertFalse(result.accepted)
        self.assertEqual(result.lifecycle_record, lifecycle)
        self.assertEqual(result.request, {"symbol": "XAUUSD"})
        self.assertIsNone(result.error_message)

    def test_create_demo_open_position_with_all_fields(self) -> None:
        position = DemoOpenPosition(
            ticket=1,
            symbol="XAUUSD",
            volume=0.01,
            position_type=0,
            action="BUY",
            price_open=100.0,
            stop_loss=99.0,
            take_profit=102.0,
            magic=21001,
            comment="demo",
            time=123456,
        )

        self.assertEqual(position.ticket, 1)
        self.assertEqual(position.symbol, "XAUUSD")
        self.assertEqual(position.volume, 0.01)
        self.assertEqual(position.position_type, 0)
        self.assertEqual(position.action, "BUY")
        self.assertEqual(position.price_open, 100.0)
        self.assertEqual(position.stop_loss, 99.0)
        self.assertEqual(position.take_profit, 102.0)
        self.assertEqual(position.magic, 21001)
        self.assertEqual(position.comment, "demo")
        self.assertEqual(position.time, 123456)

    def test_position_value_reads_dict(self) -> None:
        self.assertEqual(_position_value({"ticket": 123}, "ticket"), 123)

    def test_position_value_reads_object(self) -> None:
        self.assertEqual(_position_value(SimpleNamespace(ticket=123), "ticket"), 123)

    def test_position_value_missing_field_returns_default(self) -> None:
        self.assertEqual(_position_value(SimpleNamespace(), "ticket", "missing"), "missing")

    def test_position_action_from_type_buy(self) -> None:
        self.assertEqual(_position_action_from_type(0, _FakeMt5PositionModule()), "BUY")

    def test_position_action_from_type_sell(self) -> None:
        self.assertEqual(_position_action_from_type(1, _FakeMt5PositionModule()), "SELL")

    def test_position_action_from_type_unknown_none(self) -> None:
        self.assertIsNone(_position_action_from_type(9, _FakeMt5PositionModule()))

    def test_position_action_from_type_missing_constants_none(self) -> None:
        self.assertIsNone(_position_action_from_type(0, SimpleNamespace()))

    def test_text_contains_demo_true(self) -> None:
        self.assertTrue(_text_contains_demo("Broker Demo Server"))

    def test_text_contains_demo_false(self) -> None:
        self.assertFalse(_text_contains_demo("Broker Server"))

    def test_text_contains_demo_none_false(self) -> None:
        self.assertFalse(_text_contains_demo(None))

    def test_text_contains_live_or_real_live(self) -> None:
        self.assertTrue(_text_contains_live_or_real("Live Server"))

    def test_text_contains_live_or_real_real(self) -> None:
        self.assertTrue(_text_contains_live_or_real("Real Account"))

    def test_text_contains_live_or_real_production(self) -> None:
        self.assertTrue(_text_contains_live_or_real("Production"))

    def test_text_contains_live_or_real_demo_false(self) -> None:
        self.assertFalse(_text_contains_live_or_real("Demo Server"))

    def test_text_contains_live_or_real_none_false(self) -> None:
        self.assertFalse(_text_contains_live_or_real(None))

    def test_detect_mt5_account_mode_none(self) -> None:
        self.assertIsNone(detect_mt5_account_mode(None))

    def test_detect_mt5_account_mode_server_demo(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(server="Broker Demo")), "demo")

    def test_detect_mt5_account_mode_company_demo(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(company="Broker Demo")), "demo")

    def test_detect_mt5_account_mode_name_demo(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(name="Demo Account")), "demo")

    def test_detect_mt5_account_mode_server_live(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(server="Broker Live")), "live")

    def test_detect_mt5_account_mode_server_real(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(server="Broker Real")), "live")

    def test_detect_mt5_account_mode_server_production(self) -> None:
        self.assertEqual(detect_mt5_account_mode(SimpleNamespace(server="Broker Production")), "live")

    def test_detect_mt5_account_mode_unknown_fields(self) -> None:
        self.assertIsNone(detect_mt5_account_mode(SimpleNamespace(server="Broker")))

    def test_verify_mt5_demo_account_demo_approved(self) -> None:
        guard = verify_mt5_demo_account(_FakeMt5(SimpleNamespace(server="Broker Demo")))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_verify_mt5_demo_account_none_rejected(self) -> None:
        guard = verify_mt5_demo_account(_FakeMt5(None))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo account confirmation is required",))

    def test_verify_mt5_demo_account_live_rejected(self) -> None:
        guard = verify_mt5_demo_account(_FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("live account is not allowed",))

    def test_verify_mt5_demo_account_unknown_rejected(self) -> None:
        guard = verify_mt5_demo_account(_FakeMt5(SimpleNamespace(server="Broker")))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo account confirmation is required",))

    def test_verify_mt5_demo_account_raise_rejected(self) -> None:
        guard = verify_mt5_demo_account(_FakeMt5(error=RuntimeError("account boom")))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("failed to read MT5 account info",))

    def test_map_demo_intent_to_mt5_request_buy_type(self) -> None:
        request = _map_demo_intent_to_mt5_request(_intent(action="BUY", order_type="DEMO_BUY"))

        self.assertEqual(request["type"], "BUY")

    def test_map_demo_intent_to_mt5_request_sell_type(self) -> None:
        request = _map_demo_intent_to_mt5_request(_intent(action="SELL", order_type="DEMO_SELL"))

        self.assertEqual(request["type"], "SELL")

    def test_map_demo_intent_to_mt5_request_fields(self) -> None:
        request = _map_demo_intent_to_mt5_request(_intent())

        self.assertEqual(request["symbol"], "XAUUSD")
        self.assertEqual(request["volume"], 0.01)
        self.assertEqual(request["price"], 100.0)
        self.assertEqual(request["sl"], 99.0)
        self.assertEqual(request["tp"], 102.0)
        self.assertEqual(request["deviation"], 20)
        self.assertEqual(request["magic"], 21001)
        self.assertEqual(request["comment"], "SignalBot demo execution")

    def test_map_demo_intent_to_mt5_request_custom_deviation(self) -> None:
        request = _map_demo_intent_to_mt5_request(_intent(), deviation=5)

        self.assertEqual(request["deviation"], 5)

    def test_map_demo_intent_to_mt5_request_returns_dict_only(self) -> None:
        self.assertIsInstance(_map_demo_intent_to_mt5_request(_intent()), dict)

    def test_map_demo_intent_to_mt5_request_uses_no_mt5_constants(self) -> None:
        request = _map_demo_intent_to_mt5_request(_intent())

        self.assertEqual(set(request), {"symbol", "type", "volume", "price", "sl", "tp", "deviation", "magic", "comment"})

    def test_build_demo_request_lifecycle_record_account_rejected(self) -> None:
        record = build_demo_request_lifecycle_record(
            "demo_account_rejected",
            _intent(),
            approved=False,
            reasons=("live account is not allowed",),
            account_mode="live",
            metadata={"source": "unit-test"},
        )

        self.assertEqual(record.stage, "demo_account_rejected")
        self.assertFalse(record.approved)
        self.assertEqual(record.reasons, ("live account is not allowed",))
        self.assertEqual(record.account_mode, "live")
        self.assertEqual(record.metadata["source"], "unit-test")

    def test_build_demo_request_lifecycle_record_request_built(self) -> None:
        record = build_demo_request_lifecycle_record("demo_request_built", _intent(), approved=True, account_mode="demo")

        self.assertEqual(record.stage, "demo_request_built")
        self.assertTrue(record.approved)
        self.assertEqual(record.account_mode, "demo")

    def test_build_demo_send_dry_run_result_demo_account_attempted_false(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

        self.assertFalse(result.attempted)

    def test_build_demo_send_dry_run_result_demo_account_accepted_false(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

        self.assertFalse(result.accepted)

    def test_build_demo_send_dry_run_result_demo_account_builds_request(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

        self.assertIsNotNone(result.request)
        self.assertEqual(result.request["symbol"], "XAUUSD")

    def test_build_demo_send_dry_run_result_demo_account_stage_request_built(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

        self.assertEqual(result.lifecycle_record.stage, "demo_request_built")

    def test_build_demo_send_dry_run_result_reject_attempted_false(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertFalse(result.attempted)

    def test_build_demo_send_dry_run_result_reject_accepted_false(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertFalse(result.accepted)

    def test_build_demo_send_dry_run_result_reject_request_none(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertIsNone(result.request)

    def test_build_demo_send_dry_run_result_reject_stage_account_rejected(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertEqual(result.lifecycle_record.stage, "demo_account_rejected")

    def test_build_demo_send_dry_run_result_reject_reasons_preserved(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")))

        self.assertEqual(result.lifecycle_record.reasons, ("live account is not allowed",))

    def test_build_demo_send_dry_run_result_account_info_raise_does_not_crash(self) -> None:
        result = build_demo_send_dry_run_result(_intent(), _FakeMt5(error=RuntimeError("account boom")))

        self.assertEqual(result.lifecycle_record.reasons, ("failed to read MT5 account info",))
        self.assertIsNone(result.request)

    def test_send_demo_order_account_reject_does_not_call_fake_sender(self) -> None:
        sender = _FakeSender(SimpleNamespace(retcode=10009))

        result = send_demo_order(_intent(), _FakeMt5(SimpleNamespace(server="Broker Live")), send_fn=sender)

        self.assertEqual(sender.calls, 0)
        self.assertFalse(result.attempted)

    def test_send_demo_order_demo_account_calls_fake_sender_once(self) -> None:
        sender = _FakeSender(SimpleNamespace(retcode=10009))

        send_demo_order(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")), send_fn=sender)

        self.assertEqual(sender.calls, 1)

    def test_send_demo_order_passes_request_to_fake_sender(self) -> None:
        sender = _FakeSender(SimpleNamespace(retcode=10009))

        send_demo_order(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")), send_fn=sender)

        self.assertEqual(sender.requests[0]["symbol"], "XAUUSD")
        self.assertEqual(sender.requests[0]["type"], "BUY")

    def test_send_demo_order_accepts_retcode_10009(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009)),
        )

        self.assertTrue(result.accepted)

    def test_send_demo_order_accepts_retcode_10008(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10008)),
        )

        self.assertTrue(result.accepted)

    def test_send_demo_order_rejected_retcode_not_accepted(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10030)),
        )

        self.assertFalse(result.accepted)

    def test_send_demo_order_retcode_10030_preserves_rejected_details(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10030, comment="Unsupported filling mode")),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_rejected")
        self.assertEqual(result.lifecycle_record.mt5_retcode, 10030)
        self.assertEqual(result.lifecycle_record.mt5_comment, "Unsupported filling mode")

    def test_send_demo_order_missing_retcode_rejected_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(comment="missing")),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_rejected")
        self.assertEqual(result.lifecycle_record.reasons, ("missing sender retcode",))

    def test_send_demo_order_sender_exception_failed_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(error=RuntimeError("send boom")),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_failed")

    def test_send_demo_order_parses_result_dict(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender({"retcode": 10009, "comment": "accepted", "ticket": 111}),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.lifecycle_record.ticket, 111)

    def test_send_demo_order_parses_result_object(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009, comment="accepted", ticket=222)),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.lifecycle_record.ticket, 222)

    def test_send_demo_order_custom_success_retcodes(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=1)),
            success_retcodes=(1,),
        )

        self.assertTrue(result.accepted)

    def test_send_demo_order_maps_lifecycle_retcode(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009)),
        )

        self.assertEqual(result.lifecycle_record.mt5_retcode, 10009)

    def test_send_demo_order_maps_lifecycle_comment(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009, comment="accepted")),
        )

        self.assertEqual(result.lifecycle_record.mt5_comment, "accepted")

    def test_send_demo_order_maps_ticket_from_ticket(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009, ticket=123)),
        )

        self.assertEqual(result.lifecycle_record.ticket, 123)

    def test_send_demo_order_maps_ticket_from_order(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009, order=456)),
        )

        self.assertEqual(result.lifecycle_record.ticket, 456)

    def test_send_demo_order_maps_ticket_from_deal(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009, deal=789)),
        )

        self.assertEqual(result.lifecycle_record.ticket, 789)

    def test_send_demo_order_account_reject_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Live")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009)),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_account_rejected")

    def test_send_demo_order_accepted_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10009)),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_accepted")

    def test_send_demo_order_rejected_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(SimpleNamespace(retcode=10030)),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_rejected")

    def test_send_demo_order_failed_stage(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(error=RuntimeError("send boom")),
        )

        self.assertEqual(result.lifecycle_record.stage, "demo_order_failed")

    def test_send_demo_order_exception_error_message(self) -> None:
        result = send_demo_order(
            _intent(),
            _FakeMt5(SimpleNamespace(server="Broker Demo")),
            send_fn=_FakeSender(error=RuntimeError("send boom")),
        )

        self.assertEqual(result.error_message, "send boom")

    def test_send_demo_order_missing_send_fn_type_error(self) -> None:
        with self.assertRaises(TypeError):
            send_demo_order(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))  # type: ignore[call-arg]

    def test_bool_env_enabled_true_variants(self) -> None:
        for value in ("true", "1", "yes", "y", "on", "TRUE", " Yes "):
            with self.subTest(value=value), patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": value}, clear=True):
                self.assertTrue(_bool_env_enabled("DEMO_EXECUTION_ENABLED"))

    def test_bool_env_enabled_false_missing_invalid(self) -> None:
        for env in ({}, {"DEMO_EXECUTION_ENABLED": "false"}, {"DEMO_EXECUTION_ENABLED": "0"}, {"DEMO_EXECUTION_ENABLED": "invalid"}):
            with self.subTest(env=env), patch.dict("os.environ", env, clear=True):
                self.assertFalse(_bool_env_enabled("DEMO_EXECUTION_ENABLED"))

    def test_demo_execution_env_enabled_true_approved(self) -> None:
        with patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = demo_execution_env_enabled()

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_demo_execution_env_enabled_missing_rejected(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            guard = demo_execution_env_enabled()

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo execution is not enabled",))

    def test_demo_execution_env_enabled_false_rejected(self) -> None:
        with patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "false"}, clear=True):
            guard = demo_execution_env_enabled()

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo execution is not enabled",))

    def test_demo_sender_stop_active_no_stop_files_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            guard = demo_sender_stop_active(default_demo_execution_config(Path(directory)))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_demo_sender_stop_active_demo_stop_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_demo_execution_config(Path(directory))
            config.stop_demo_execution_path.touch()

            guard = demo_sender_stop_active(config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo execution stop file active",))

    def test_demo_sender_stop_active_global_stop_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_demo_execution_config(Path(directory))
            config.stop_all_trading_path.touch()

            guard = demo_sender_stop_active(config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("global trading stop file active",))

    def test_demo_sender_stop_active_both_stop_files_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_demo_execution_config(Path(directory))
            config.stop_demo_execution_path.touch()
            config.stop_all_trading_path.touch()

            guard = demo_sender_stop_active(config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("demo execution stop file active", "global trading stop file active"))

    def test_validate_demo_sender_preconditions_env_disabled_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution is not enabled", guard.reasons)

    def test_validate_demo_sender_preconditions_stop_file_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            config = default_demo_execution_config(Path(directory))
            config.stop_demo_execution_path.touch()

            guard = validate_demo_sender_preconditions(
                _intent(),
                config,
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution stop file active", guard.reasons)

    def test_validate_demo_sender_preconditions_account_mode_none_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode=None,
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo account confirmation is required", guard.reasons)

    def test_validate_demo_sender_preconditions_account_mode_live_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="live",
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("live account is not allowed", guard.reasons)

    def test_validate_demo_sender_preconditions_spread_none_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=None,
            )

        self.assertFalse(guard.approved)
        self.assertIn("spread points are required", guard.reasons)

    def test_validate_demo_sender_preconditions_spread_too_high_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=31.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("spread exceeds max spread", guard.reasons)

    def test_validate_demo_sender_preconditions_valid_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
            )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_sender_preconditions_invalid_candidate_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(action="WAIT", order_type="WAIT", volume=0.02),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("action must be BUY or SELL", guard.reasons)
        self.assertIn("volume exceeds max lot", guard.reasons)

    def test_validate_demo_sender_preconditions_invalid_risk_state_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_sender_preconditions(
                _intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(trades_today=3, open_positions=1),
                account_mode="demo",
                spread_points=10.0,
            )

        self.assertFalse(guard.approved)
        self.assertIn("max trades per day reached", guard.reasons)
        self.assertIn("max open positions reached", guard.reasons)

    def test_validate_demo_sender_preconditions_multiple_guards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            config = default_demo_execution_config(Path(directory))
            config.stop_all_trading_path.touch()

            guard = validate_demo_sender_preconditions(
                _intent(action="WAIT", order_type="WAIT"),
                config,
                DemoExecutionState(trades_today=3),
                account_mode=None,
                spread_points=None,
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution is not enabled", guard.reasons)
        self.assertIn("global trading stop file active", guard.reasons)
        self.assertIn("demo account confirmation is required", guard.reasons)
        self.assertIn("spread points are required", guard.reasons)
        self.assertIn("action must be BUY or SELL", guard.reasons)
        self.assertIn("max trades per day reached", guard.reasons)

    def test_build_demo_send_blocked_result_attempted_false(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertFalse(result.attempted)

    def test_build_demo_send_blocked_result_accepted_false(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertFalse(result.accepted)

    def test_build_demo_send_blocked_result_request_none(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertIsNone(result.request)

    def test_build_demo_send_blocked_result_stage(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertEqual(result.lifecycle_record.stage, "demo_send_blocked")

    def test_build_demo_send_blocked_result_lifecycle_approved_false(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertFalse(result.lifecycle_record.approved)

    def test_build_demo_send_blocked_result_reasons_preserved(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked", "risk"), account_mode="demo")

        self.assertEqual(result.lifecycle_record.reasons, ("blocked", "risk"))

    def test_build_demo_send_blocked_result_account_mode_preserved(self) -> None:
        result = build_demo_send_blocked_result(_intent(), ("blocked",), account_mode="demo")

        self.assertEqual(result.lifecycle_record.account_mode, "demo")

    def test_demo_order_record_from_result_all_fields(self) -> None:
        record = demo_order_record_from_result(_intent(), _demo_result())

        self.assertEqual(tuple(record), DEMO_ORDER_RECORD_FIELDS)

    def test_demo_order_record_from_result_accepted_maps_trade_result(self) -> None:
        record = demo_order_record_from_result(_intent(), _demo_result())

        self.assertEqual(record["retcode"], 10009)
        self.assertEqual(record["comment"], "accepted")
        self.assertEqual(record["ticket"], 123)
        self.assertTrue(record["accepted"])

    def test_demo_order_record_from_result_rejected_maps_reasons(self) -> None:
        result = _demo_result(accepted=False, stage="demo_order_rejected", reasons=("demo sender rejected order",))

        record = demo_order_record_from_result(_intent(), result)

        self.assertEqual(record["reasons"], ["demo sender rejected order"])
        self.assertFalse(record["accepted"])

    def test_demo_order_record_from_result_error_message_in_metadata(self) -> None:
        record = demo_order_record_from_result(_intent(), _demo_result(error_message="send boom"))

        self.assertEqual(record["metadata"]["error_message"], "send boom")

    def test_demo_order_record_from_result_combines_intent_and_lifecycle_metadata(self) -> None:
        intent = _intent_with_metadata({"strategy": "manual"})
        result = _demo_result(metadata={"request": {"symbol": "XAUUSD"}})

        record = demo_order_record_from_result(intent, result)

        self.assertEqual(record["metadata"]["intent"], {"strategy": "manual"})
        self.assertEqual(record["metadata"]["lifecycle"], {"request": {"symbol": "XAUUSD"}})

    def test_append_demo_order_record_jsonl_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "demo_order_records.jsonl"

            append_demo_order_record_jsonl(_demo_record(), path)

            self.assertTrue(path.exists())

    def test_append_demo_order_record_jsonl_writes_one_line_per_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.jsonl"

            append_demo_order_record_jsonl(_demo_record(), path)
            append_demo_order_record_jsonl(_demo_record(ticket=456), path)

            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_append_demo_order_record_csv_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "demo_order_records.csv"

            append_demo_order_record_csv(_demo_record(), path)

            self.assertTrue(path.exists())

    def test_append_demo_order_record_csv_writes_header_for_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.csv"

            append_demo_order_record_csv(_demo_record(), path)
            rows = path.read_text(encoding="utf-8-sig").splitlines()

            self.assertEqual(rows[0].split(","), list(DEMO_ORDER_RECORD_FIELDS))

    def test_append_demo_order_record_csv_append_does_not_duplicate_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.csv"

            append_demo_order_record_csv(_demo_record(), path)
            append_demo_order_record_csv(_demo_record(ticket=456), path)
            rows = path.read_text(encoding="utf-8-sig").splitlines()

            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0].split(","), list(DEMO_ORDER_RECORD_FIELDS))
            self.assertNotEqual(rows[1].split(","), list(DEMO_ORDER_RECORD_FIELDS))

    def test_append_demo_order_record_csv_reasons_json_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.csv"

            append_demo_order_record_csv(_demo_record(reasons=["เหตุผล"]), path)
            with path.open("r", encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle))

            self.assertEqual(json.loads(row["reasons"]), ["เหตุผล"])

    def test_append_demo_order_record_csv_metadata_json_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.csv"

            append_demo_order_record_csv(_demo_record(metadata={"note": "ทดสอบ"}), path)
            with path.open("r", encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle))

            self.assertEqual(json.loads(row["metadata"]), {"note": "ทดสอบ"})

    def test_write_demo_order_record_writes_csv_and_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "demo_order_records.csv"
            jsonl_path = Path(directory) / "demo_order_records.jsonl"

            record = write_demo_order_record(_intent(), _demo_result(), csv_path=csv_path, jsonl_path=jsonl_path)

            self.assertTrue(csv_path.exists())
            self.assertTrue(jsonl_path.exists())
            self.assertEqual(record["ticket"], 123)

    def test_load_demo_order_records_jsonl_missing_file_empty_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records = load_demo_order_records_jsonl(Path(directory) / "missing.jsonl")

        self.assertEqual(records, ())

    def test_load_demo_order_records_jsonl_skips_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.jsonl"
            path.write_text("\n" + json.dumps(_demo_record()) + "\n\n", encoding="utf-8")

            records = load_demo_order_records_jsonl(path)

            self.assertEqual(len(records), 1)

    def test_load_demo_order_records_jsonl_reads_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.jsonl"
            append_demo_order_record_jsonl(_demo_record(), path)

            records = load_demo_order_records_jsonl(path)

            self.assertEqual(records[0]["ticket"], 123)

    def test_load_demo_order_records_jsonl_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.jsonl"
            path.write_text("{not-json}\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_demo_order_records_jsonl(path)

    def test_demo_order_jsonl_utf8_thai_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo_order_records.jsonl"
            record = _demo_record(comment="ส่งสำเร็จ", reasons=["เหตุผล"], metadata={"note": "ภาษาไทย"})

            append_demo_order_record_jsonl(record, path)
            loaded = load_demo_order_records_jsonl(path)[0]

            self.assertEqual(loaded["comment"], "ส่งสำเร็จ")
            self.assertEqual(loaded["reasons"], ["เหตุผล"])
            self.assertEqual(loaded["metadata"], {"note": "ภาษาไทย"})
            self.assertNotIn("à¸", path.read_text(encoding="utf-8"))

    def test_map_mt5_position_dict_all_fields(self) -> None:
        position = map_mt5_position_to_demo_position(_position_dict(), _FakeMt5PositionModule())

        self.assertEqual(position.ticket, 123)
        self.assertEqual(position.symbol, "XAUUSD")
        self.assertEqual(position.volume, 0.01)
        self.assertEqual(position.position_type, 0)
        self.assertEqual(position.action, "BUY")
        self.assertEqual(position.price_open, 100.0)
        self.assertEqual(position.stop_loss, 99.0)
        self.assertEqual(position.take_profit, 102.0)
        self.assertEqual(position.magic, 21001)
        self.assertEqual(position.comment, "demo")
        self.assertEqual(position.time, 123456)

    def test_map_mt5_position_object_all_fields(self) -> None:
        position = map_mt5_position_to_demo_position(SimpleNamespace(**_position_dict()), _FakeMt5PositionModule())

        self.assertEqual(position.ticket, 123)
        self.assertEqual(position.symbol, "XAUUSD")
        self.assertEqual(position.volume, 0.01)
        self.assertEqual(position.action, "BUY")

    def test_map_mt5_position_missing_symbol_empty(self) -> None:
        position = map_mt5_position_to_demo_position({}, _FakeMt5PositionModule())

        self.assertEqual(position.symbol, "")

    def test_map_mt5_position_missing_volume_zero(self) -> None:
        position = map_mt5_position_to_demo_position({}, _FakeMt5PositionModule())

        self.assertEqual(position.volume, 0.0)

    def test_map_mt5_position_numeric_fields_cast(self) -> None:
        position = map_mt5_position_to_demo_position(
            {
                "ticket": "123",
                "symbol": "XAUUSD",
                "volume": "0.01",
                "type": "0",
                "price_open": "100.0",
                "sl": "99.0",
                "tp": "102.0",
                "magic": "21001",
                "time": "123456",
            },
            _FakeMt5PositionModule(),
        )

        self.assertEqual(position.ticket, 123)
        self.assertEqual(position.volume, 0.01)
        self.assertEqual(position.price_open, 100.0)
        self.assertEqual(position.stop_loss, 99.0)
        self.assertEqual(position.take_profit, 102.0)
        self.assertEqual(position.magic, 21001)
        self.assertEqual(position.time, 123456)

    def test_map_mt5_position_buy_action(self) -> None:
        position = map_mt5_position_to_demo_position({"type": 0}, _FakeMt5PositionModule())

        self.assertEqual(position.action, "BUY")

    def test_map_mt5_position_sell_action(self) -> None:
        position = map_mt5_position_to_demo_position({"type": 1}, _FakeMt5PositionModule())

        self.assertEqual(position.action, "SELL")

    def test_map_mt5_position_unknown_action_none(self) -> None:
        position = map_mt5_position_to_demo_position({"type": 9}, _FakeMt5PositionModule())

        self.assertIsNone(position.action)

    def test_fetch_demo_open_positions_no_symbol_calls_without_args(self) -> None:
        mt5 = _FakeMt5PositionsModule([_position_dict()])

        fetch_demo_open_positions(mt5)

        self.assertEqual(mt5.calls, [None])

    def test_fetch_demo_open_positions_with_symbol_calls_symbol_arg(self) -> None:
        mt5 = _FakeMt5PositionsModule([_position_dict()])

        fetch_demo_open_positions(mt5, symbol="XAUUSD")

        self.assertEqual(mt5.calls, ["XAUUSD"])

    def test_fetch_demo_open_positions_none_returns_empty_tuple(self) -> None:
        mt5 = _FakeMt5PositionsModule(None)

        self.assertEqual(fetch_demo_open_positions(mt5), ())

    def test_fetch_demo_open_positions_maps_tuple(self) -> None:
        mt5 = _FakeMt5PositionsModule([_position_dict(), {**_position_dict(), "ticket": 456, "type": 1}])

        positions = fetch_demo_open_positions(mt5)

        self.assertEqual(len(positions), 2)
        self.assertIsInstance(positions[0], DemoOpenPosition)
        self.assertEqual(positions[1].action, "SELL")

    def test_fetch_demo_open_positions_missing_reader_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing MT5 positions reader: positions_get"):
            fetch_demo_open_positions(SimpleNamespace())

    def test_fetch_demo_open_positions_reader_raise_runtime_error(self) -> None:
        mt5 = _FakeMt5PositionsModule(error=RuntimeError("positions boom"))

        with self.assertRaisesRegex(RuntimeError, "failed to read MT5 open positions"):
            fetch_demo_open_positions(mt5)

    def test_validate_demo_position_limits_below_max_approved(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_position_limits((), config)

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_position_limits_equal_max_rejected(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_position_limits((_open_position(),), config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("max open positions reached",))

    def test_validate_demo_position_limits_above_max_rejected(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_position_limits((_open_position(), _open_position(ticket=456)), config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("max open positions reached",))

    def test_validate_demo_same_symbol_action_no_positions_approved(self) -> None:
        guard = validate_demo_same_symbol_action_guard(_intent(), ())

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_same_symbol_action_same_action_rejected(self) -> None:
        guard = validate_demo_same_symbol_action_guard(_intent(), (_open_position(action="BUY"),))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("same symbol action position already open",))

    def test_validate_demo_same_symbol_action_opposite_rejected_by_default(self) -> None:
        guard = validate_demo_same_symbol_action_guard(_intent(), (_open_position(action="SELL"),))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("same symbol position already open",))

    def test_validate_demo_same_symbol_action_opposite_allowed_when_disabled(self) -> None:
        guard = validate_demo_same_symbol_action_guard(
            _intent(),
            (_open_position(action="SELL"),),
            reject_opposite_action=False,
        )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_same_symbol_action_different_symbol_approved(self) -> None:
        guard = validate_demo_same_symbol_action_guard(_intent(), (_open_position(symbol="EURUSD", action="BUY"),))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_same_symbol_action_none_action_rejected_conservative(self) -> None:
        guard = validate_demo_same_symbol_action_guard(_intent(), (_open_position(action=None),))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("same symbol position already open",))

    def test_validate_demo_same_symbol_action_allow_pyramiding_approves_same_action_under_limit(self) -> None:
        guard = validate_demo_same_symbol_action_guard(
            _intent(),
            (_open_position(action="BUY"),),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_pyramiding_guard_disabled_same_symbol_action_rejected(self) -> None:
        guard = validate_demo_pyramiding_guard(
            _intent(),
            (_open_position(action="BUY"),),
            (),
            allow_pyramiding=False,
            max_same_symbol_positions=2,
        )

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("same symbol action position already open",))

    def test_validate_demo_pyramiding_guard_enabled_same_symbol_action_under_limit_approved(self) -> None:
        guard = validate_demo_pyramiding_guard(
            _intent(),
            (_open_position(action="BUY"),),
            (),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_pyramiding_guard_enabled_over_same_symbol_limit_rejected(self) -> None:
        guard = validate_demo_pyramiding_guard(
            _intent(),
            (_open_position(ticket=1, action="BUY"), _open_position(ticket=2, action="BUY")),
            (),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("max same symbol positions reached",))

    def test_validate_demo_pyramiding_guard_opposite_action_rejected(self) -> None:
        guard = validate_demo_pyramiding_guard(
            _intent(),
            (_open_position(action="SELL"),),
            (),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("opposite symbol position already open",))

    def test_validate_demo_pyramiding_guard_duplicate_signal_still_rejected(self) -> None:
        guard = validate_demo_pyramiding_guard(
            _gate_intent(),
            (_open_position(action="BUY"),),
            ({"signal_id": "sig-1"},),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id",))

    def test_validate_demo_pyramiding_guard_duplicate_candle_still_rejected(self) -> None:
        record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

        guard = validate_demo_pyramiding_guard(
            _gate_intent(),
            (_open_position(action="BUY"),),
            (record,),
            allow_pyramiding=True,
            max_same_symbol_positions=2,
        )

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate execution candle",))

    def test_validate_demo_open_position_guards_no_issues_approved(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_open_position_guards(_intent(), (), config)

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_open_position_guards_max_positions_rejected(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_open_position_guards(_intent(), (_open_position(symbol="EURUSD"),), config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("max open positions reached",))

    def test_validate_demo_open_position_guards_same_symbol_action_rejected(self) -> None:
        config = replace(default_demo_execution_config(Path("logs/forward_validation")), max_open_positions=2)

        guard = validate_demo_open_position_guards(_intent(), (_open_position(action="BUY"),), config)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("same symbol action position already open",))

    def test_validate_demo_open_position_guards_combines_reasons(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))

        guard = validate_demo_open_position_guards(_intent(), (_open_position(action="BUY"),), config)

        self.assertFalse(guard.approved)
        self.assertEqual(
            guard.reasons,
            ("max open positions reached", "same symbol action position already open"),
        )

    def test_validate_demo_open_position_guards_does_not_call_positions_reader(self) -> None:
        config = default_demo_execution_config(Path("logs/forward_validation"))
        mt5 = _FakeMt5PositionsModule([_position_dict()])

        validate_demo_open_position_guards(_intent(), (), config)

        self.assertEqual(mt5.calls, [])

    def test_validate_demo_duplicate_signal_id_missing_required_rejected(self) -> None:
        guard = validate_demo_duplicate_signal_id(_intent(signal_id=None), (), require_signal_id=True)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("signal id is required",))

    def test_validate_demo_duplicate_signal_id_missing_not_required_approved(self) -> None:
        guard = validate_demo_duplicate_signal_id(_intent(signal_id=None), (), require_signal_id=False)

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_signal_id_empty_records_approved(self) -> None:
        guard = validate_demo_duplicate_signal_id(_intent(signal_id="sig-1"), ())

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_signal_id_top_level_rejected(self) -> None:
        guard = validate_demo_duplicate_signal_id(_intent(signal_id="sig-1"), ({"signal_id": "sig-1"},))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id",))

    def test_validate_demo_duplicate_signal_id_metadata_rejected(self) -> None:
        record = {"metadata": {"signal_id": "sig-1"}}

        guard = validate_demo_duplicate_signal_id(_intent(signal_id="sig-1"), (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id",))

    def test_validate_demo_duplicate_signal_id_metadata_intent_rejected(self) -> None:
        record = {"metadata": {"intent": {"signal_id": "sig-1"}}}

        guard = validate_demo_duplicate_signal_id(_intent(signal_id="sig-1"), (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id",))

    def test_validate_demo_duplicate_signal_id_different_approved(self) -> None:
        guard = validate_demo_duplicate_signal_id(_intent(signal_id="sig-1"), ({"signal_id": "sig-2"},))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_candle_missing_required_rejected(self) -> None:
        guard = validate_demo_duplicate_candle(_intent_with_metadata({}), (), require_candle_time=True)

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("execution candle time is required",))

    def test_validate_demo_duplicate_candle_missing_not_required_approved(self) -> None:
        guard = validate_demo_duplicate_candle(_intent_with_metadata({}), (), require_candle_time=False)

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_candle_empty_records_approved(self) -> None:
        guard = validate_demo_duplicate_candle(_intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"}), ())

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_candle_metadata_rejected(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

        guard = validate_demo_duplicate_candle(intent, (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate execution candle",))

    def test_validate_demo_duplicate_candle_metadata_intent_rejected(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "XAUUSD", "metadata": {"intent": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}}

        guard = validate_demo_duplicate_candle(intent, (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate execution candle",))

    def test_validate_demo_duplicate_candle_metadata_lifecycle_rejected(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "XAUUSD", "metadata": {"lifecycle": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}}

        guard = validate_demo_duplicate_candle(intent, (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate execution candle",))

    def test_validate_demo_duplicate_candle_same_time_different_symbol_approved(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "EURUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

        guard = validate_demo_duplicate_candle(intent, (record,))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_candle_same_symbol_different_time_approved(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:01:00"}}

        guard = validate_demo_duplicate_candle(intent, (record,))

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_guards_no_duplicates_approved(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})

        guard = validate_demo_duplicate_guards(intent, ())

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_duplicate_guards_duplicate_signal_rejected(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})

        guard = validate_demo_duplicate_guards(intent, ({"signal_id": "sig-1"},))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id",))

    def test_validate_demo_duplicate_guards_duplicate_candle_rejected(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

        guard = validate_demo_duplicate_guards(intent, (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate execution candle",))

    def test_validate_demo_duplicate_guards_combines_reasons(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})
        record = {
            "symbol": "XAUUSD",
            "signal_id": "sig-1",
            "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"},
        }

        guard = validate_demo_duplicate_guards(intent, (record,))

        self.assertFalse(guard.approved)
        self.assertEqual(guard.reasons, ("duplicate signal id", "duplicate execution candle"))

    def test_validate_demo_duplicate_guards_does_not_read_files(self) -> None:
        intent = _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})

        guard = validate_demo_duplicate_guards(intent, ())

        self.assertTrue(guard.approved)

    def test_validate_demo_execution_gate_all_pass_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_execution_gate_env_disabled_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution is not enabled", guard.reasons)

    def test_validate_demo_execution_gate_stop_file_active_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            config = replace(default_demo_execution_config(Path(directory)), max_open_positions=2)
            config.stop_demo_execution_path.touch()

            guard = validate_demo_execution_gate(
                _gate_intent(),
                config,
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution stop file active", guard.reasons)

    def test_validate_demo_execution_gate_account_mode_none_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode=None,
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo account confirmation is required", guard.reasons)

    def test_validate_demo_execution_gate_account_mode_live_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="live",
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("live account is not allowed", guard.reasons)

    def test_validate_demo_execution_gate_spread_none_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=None,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("spread points are required", guard.reasons)

    def test_validate_demo_execution_gate_spread_too_high_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=31.0,
                positions=(),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("spread exceeds max spread", guard.reasons)

    def test_validate_demo_execution_gate_position_limit_reached_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                default_demo_execution_config(Path(directory)),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(symbol="EURUSD"),),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("max open positions reached", guard.reasons)

    def test_validate_demo_execution_gate_same_symbol_action_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="BUY"),),
                records=(),
            )

        self.assertFalse(guard.approved)
        self.assertIn("same symbol action position already open", guard.reasons)

    def test_validate_demo_execution_gate_allow_pyramiding_same_action_under_limit_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=3),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="BUY"),),
                records=(),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertTrue(guard.approved)
        self.assertEqual(guard.reasons, ())

    def test_validate_demo_execution_gate_allow_pyramiding_same_symbol_limit_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=3),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(ticket=1, action="BUY"), _open_position(ticket=2, action="BUY")),
                records=(),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertFalse(guard.approved)
        self.assertIn("max same symbol positions reached", guard.reasons)

    def test_validate_demo_execution_gate_allow_pyramiding_opposite_action_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=3),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="SELL"),),
                records=(),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertFalse(guard.approved)
        self.assertIn("opposite symbol position already open", guard.reasons)

    def test_validate_demo_execution_gate_allow_pyramiding_duplicate_signal_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=3),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="BUY"),),
                records=({"signal_id": "sig-1"},),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertFalse(guard.approved)
        self.assertIn("duplicate signal id", guard.reasons)

    def test_validate_demo_execution_gate_allow_pyramiding_duplicate_candle_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=3),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="BUY"),),
                records=(record,),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertFalse(guard.approved)
        self.assertIn("duplicate execution candle", guard.reasons)

    def test_validate_demo_execution_gate_allow_pyramiding_max_open_positions_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=1),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(_open_position(action="BUY"),),
                records=(),
                allow_pyramiding=True,
                max_same_symbol_positions=2,
            )

        self.assertFalse(guard.approved)
        self.assertIn("max open positions reached", guard.reasons)

    def test_validate_demo_execution_gate_duplicate_signal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=({"signal_id": "sig-1"},),
            )

        self.assertFalse(guard.approved)
        self.assertIn("duplicate signal id", guard.reasons)

    def test_validate_demo_execution_gate_duplicate_candle_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            record = {"symbol": "XAUUSD", "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"}}

            guard = validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(record,),
            )

        self.assertFalse(guard.approved)
        self.assertIn("duplicate execution candle", guard.reasons)

    def test_validate_demo_execution_gate_multiple_failures_combines_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            config = default_demo_execution_config(Path(directory))
            config.stop_all_trading_path.touch()
            record = {
                "symbol": "XAUUSD",
                "signal_id": "sig-1",
                "metadata": {"latest_execution_candle_time": "2026-01-01T00:00:00"},
            }

            guard = validate_demo_execution_gate(
                _gate_intent(),
                config,
                DemoExecutionState(),
                account_mode=None,
                spread_points=None,
                positions=(_open_position(action="BUY"),),
                records=(record,),
            )

        self.assertFalse(guard.approved)
        self.assertIn("demo execution is not enabled", guard.reasons)
        self.assertIn("global trading stop file active", guard.reasons)
        self.assertIn("demo account confirmation is required", guard.reasons)
        self.assertIn("spread points are required", guard.reasons)
        self.assertIn("max open positions reached", guard.reasons)
        self.assertIn("same symbol action position already open", guard.reasons)
        self.assertIn("duplicate signal id", guard.reasons)
        self.assertIn("duplicate execution candle", guard.reasons)

    def test_validate_demo_execution_gate_does_not_call_positions_reader(self) -> None:
        mt5 = _FakeMt5PositionsModule([_position_dict()])
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(),
            )

        self.assertEqual(mt5.calls, [])

    def test_validate_demo_execution_gate_does_not_load_records_or_send_or_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DEMO_EXECUTION_ENABLED": "true"}, clear=True):
            validate_demo_execution_gate(
                _gate_intent(),
                replace(default_demo_execution_config(Path(directory)), max_open_positions=2),
                DemoExecutionState(),
                account_mode="demo",
                spread_points=10.0,
                positions=(),
                records=(),
            )

            self.assertFalse((Path(directory) / "demo_order_records.jsonl").exists())
            self.assertFalse((Path(directory) / "demo_order_records.csv").exists())

    def test_required_mt5_constant_existing_returns_value(self) -> None:
        self.assertEqual(_required_mt5_constant(_FakeMt5Constants(), "ORDER_TYPE_BUY"), 0)

    def test_required_mt5_constant_missing_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing MT5 constant: MISSING"):
            _required_mt5_constant(_FakeMt5Constants(), "MISSING")

    def test_optional_mt5_constant_existing_returns_value(self) -> None:
        self.assertEqual(_optional_mt5_constant(_FakeMt5Constants(), "ORDER_TYPE_SELL"), 1)

    def test_optional_mt5_constant_missing_returns_none(self) -> None:
        self.assertIsNone(_optional_mt5_constant(_FakeMt5Constants(), "MISSING"))

    def test_mt5_order_type_from_demo_type_buy(self) -> None:
        self.assertEqual(_mt5_order_type_from_demo_type("BUY", _FakeMt5Constants()), 0)

    def test_mt5_order_type_from_demo_type_sell(self) -> None:
        self.assertEqual(_mt5_order_type_from_demo_type("SELL", _FakeMt5Constants()), 1)

    def test_mt5_order_type_from_demo_type_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "demo request type must be BUY or SELL"):
            _mt5_order_type_from_demo_type("WAIT", _FakeMt5Constants())

    def test_mt5_order_type_from_demo_type_missing_buy_raises(self) -> None:
        mt5 = _FakeMt5Constants(ORDER_TYPE_BUY=None)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: ORDER_TYPE_BUY"):
            _mt5_order_type_from_demo_type("BUY", mt5)

    def test_mt5_order_type_from_demo_type_missing_sell_raises(self) -> None:
        mt5 = _FakeMt5Constants(ORDER_TYPE_SELL=None)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: ORDER_TYPE_SELL"):
            _mt5_order_type_from_demo_type("SELL", mt5)

    def test_mt5_filling_type_uses_ioc_when_available(self) -> None:
        self.assertEqual(_mt5_filling_type(_FakeMt5Constants()), 10)

    def test_mt5_filling_type_fallback_fok_when_ioc_missing(self) -> None:
        self.assertEqual(_mt5_filling_type(_FakeMt5Constants(ORDER_FILLING_IOC=None)), 11)

    def test_mt5_filling_type_fallback_return_when_ioc_fok_missing(self) -> None:
        self.assertEqual(
            _mt5_filling_type(_FakeMt5Constants(ORDER_FILLING_IOC=None, ORDER_FILLING_FOK=None)),
            12,
        )

    def test_mt5_filling_type_missing_all_raises(self) -> None:
        mt5 = _FakeMt5Constants(
            ORDER_FILLING_IOC=None,
            ORDER_FILLING_FOK=None,
            ORDER_FILLING_RETURN=None,
        )

        with self.assertRaisesRegex(ValueError, "missing MT5 filling constant"):
            _mt5_filling_type(mt5)

    def test_mt5_symbol_filling_type_flag_1_uses_fok(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=1, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1)

        self.assertEqual(_mt5_symbol_filling_type(mt5, "XAUUSD.iux"), 0)

    def test_mt5_symbol_filling_type_flag_2_uses_ioc(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=2, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1)

        self.assertEqual(_mt5_symbol_filling_type(mt5, "XAUUSD.iux"), 1)

    def test_mt5_symbol_filling_type_flag_3_uses_fok_first(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=3, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1)

        self.assertEqual(_mt5_symbol_filling_type(mt5, "XAUUSD.iux"), 0)

    def test_mt5_symbol_filling_type_symbol_info_none_returns_none(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=None)

        self.assertIsNone(_mt5_symbol_filling_type(mt5, "XAUUSD.iux"))

    def test_mt5_symbol_filling_type_symbol_info_raise_returns_none(self) -> None:
        mt5 = _FakeMt5Constants(symbol_info_error=RuntimeError("symbol boom"))

        self.assertIsNone(_mt5_symbol_filling_type(mt5, "XAUUSD.iux"))

    def test_mt5_symbol_filling_type_missing_fok_constant_raises(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=1, ORDER_FILLING_FOK=None)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: ORDER_FILLING_FOK"):
            _mt5_symbol_filling_type(mt5, "XAUUSD.iux")

    def test_build_mt5_order_request_buy_maps_type(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(order_type="BUY"), _FakeMt5Constants())

        self.assertEqual(request["type"], 0)

    def test_build_mt5_order_request_sell_maps_type(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(order_type="SELL"), _FakeMt5Constants())

        self.assertEqual(request["type"], 1)

    def test_build_mt5_order_request_adds_action_deal(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["action"], 100)

    def test_build_mt5_order_request_adds_order_time_gtc(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["type_time"], 20)

    def test_build_mt5_order_request_adds_selected_type_filling(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["type_filling"], 10)

    def test_build_mt5_order_request_symbol_filling_flag_1_uses_fok(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=1, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1)

        request = build_mt5_order_request_from_demo_request(_demo_request(), mt5)

        self.assertEqual(request["type_filling"], 0)

    def test_build_mt5_order_request_symbol_info_none_fallback_policy(self) -> None:
        mt5 = _FakeMt5Constants(symbol_filling_mode=None)

        request = build_mt5_order_request_from_demo_request(_demo_request(), mt5)

        self.assertEqual(request["type_filling"], 10)

    def test_build_mt5_order_request_symbol_info_raise_fallback_policy(self) -> None:
        mt5 = _FakeMt5Constants(symbol_info_error=RuntimeError("symbol boom"))

        request = build_mt5_order_request_from_demo_request(_demo_request(), mt5)

        self.assertEqual(request["type_filling"], 10)

    def test_build_mt5_order_request_preserves_symbol(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["symbol"], "XAUUSD")

    def test_build_mt5_order_request_preserves_volume(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["volume"], 0.01)

    def test_build_mt5_order_request_preserves_price(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["price"], 100.0)

    def test_build_mt5_order_request_preserves_sl(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["sl"], 99.0)

    def test_build_mt5_order_request_preserves_tp(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["tp"], 102.0)

    def test_build_mt5_order_request_preserves_deviation(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["deviation"], 20)

    def test_build_mt5_order_request_preserves_magic(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["magic"], 21001)

    def test_build_mt5_order_request_preserves_comment(self) -> None:
        request = build_mt5_order_request_from_demo_request(_demo_request(), _FakeMt5Constants())

        self.assertEqual(request["comment"], "SignalBot demo execution")

    def test_build_mt5_order_request_invalid_type_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "demo request type must be BUY or SELL"):
            build_mt5_order_request_from_demo_request(_demo_request(order_type="WAIT"), _FakeMt5Constants())

    def test_build_mt5_order_request_missing_action_deal_raises(self) -> None:
        mt5 = _FakeMt5Constants(TRADE_ACTION_DEAL=None)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: TRADE_ACTION_DEAL"):
            build_mt5_order_request_from_demo_request(_demo_request(), mt5)

    def test_build_mt5_order_request_missing_order_time_gtc_raises(self) -> None:
        mt5 = _FakeMt5Constants(ORDER_TIME_GTC=None)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: ORDER_TIME_GTC"):
            build_mt5_order_request_from_demo_request(_demo_request(), mt5)

    def test_build_mt5_order_request_missing_filling_constants_raises(self) -> None:
        mt5 = _FakeMt5Constants(
            ORDER_FILLING_IOC=None,
            ORDER_FILLING_FOK=None,
            ORDER_FILLING_RETURN=None,
        )

        with self.assertRaisesRegex(ValueError, "missing MT5 filling constant"):
            build_mt5_order_request_from_demo_request(_demo_request(), mt5)

    def test_build_mt5_demo_send_fn_returns_callable(self) -> None:
        self.assertTrue(callable(build_mt5_demo_send_fn(_FakeMt5SenderModule())))

    def test_build_mt5_demo_send_fn_calls_fake_order_send_once(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request())

        self.assertEqual(mt5.order_send_calls, 1)

    def test_build_mt5_demo_send_fn_order_send_request_has_action(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request())

        self.assertEqual(mt5.sent_requests[0]["action"], 100)

    def test_build_mt5_demo_send_fn_request_buy_type(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request(order_type="BUY"))

        self.assertEqual(mt5.sent_requests[0]["type"], 0)

    def test_build_mt5_demo_send_fn_request_sell_type(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request(order_type="SELL"))

        self.assertEqual(mt5.sent_requests[0]["type"], 1)

    def test_build_mt5_demo_send_fn_request_has_type_time(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request())

        self.assertEqual(mt5.sent_requests[0]["type_time"], 20)

    def test_build_mt5_demo_send_fn_request_has_type_filling(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request())

        self.assertEqual(mt5.sent_requests[0]["type_filling"], 10)

    def test_build_mt5_demo_send_fn_request_preserves_trade_fields(self) -> None:
        mt5 = _FakeMt5SenderModule()
        send_fn = build_mt5_demo_send_fn(mt5)

        send_fn(_demo_request())
        request = mt5.sent_requests[0]

        self.assertEqual(request["symbol"], "XAUUSD")
        self.assertEqual(request["volume"], 0.01)
        self.assertEqual(request["price"], 100.0)
        self.assertEqual(request["sl"], 99.0)
        self.assertEqual(request["tp"], 102.0)
        self.assertEqual(request["deviation"], 20)
        self.assertEqual(request["magic"], 21001)
        self.assertEqual(request["comment"], "SignalBot demo execution")

    def test_build_mt5_demo_send_fn_missing_order_send_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing MT5 sender: order_send"):
            build_mt5_demo_send_fn(_FakeMt5Constants())

    def test_build_mt5_demo_send_fn_missing_mapping_constants_raise_through_callable(self) -> None:
        mt5 = _FakeMt5SenderModule(ORDER_TYPE_BUY=None)
        send_fn = build_mt5_demo_send_fn(mt5)

        with self.assertRaisesRegex(ValueError, "missing MT5 constant: ORDER_TYPE_BUY"):
            send_fn(_demo_request(order_type="BUY"))

    def test_build_mt5_demo_send_fn_exception_maps_to_failed_in_send_demo_order(self) -> None:
        mt5 = _FakeMt5SenderModule(send_error=RuntimeError("send boom"))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertEqual(result.lifecycle_record.stage, "demo_order_failed")
        self.assertEqual(result.error_message, "send boom")

    def test_send_demo_order_with_build_mt5_demo_send_fn_accepts_retcode(self) -> None:
        mt5 = _FakeMt5SenderModule(send_result=SimpleNamespace(retcode=10009))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertTrue(result.accepted)

    def test_send_demo_order_with_build_mt5_demo_send_fn_rejects_retcode(self) -> None:
        mt5 = _FakeMt5SenderModule(send_result=SimpleNamespace(retcode=10030))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertFalse(result.accepted)

    def test_send_demo_order_with_build_mt5_demo_send_fn_accepted_stage(self) -> None:
        mt5 = _FakeMt5SenderModule(send_result=SimpleNamespace(retcode=10009))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertEqual(result.lifecycle_record.stage, "demo_order_accepted")

    def test_send_demo_order_with_build_mt5_demo_send_fn_rejected_stage(self) -> None:
        mt5 = _FakeMt5SenderModule(send_result=SimpleNamespace(retcode=10030))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertEqual(result.lifecycle_record.stage, "demo_order_rejected")

    def test_send_demo_order_with_build_mt5_demo_send_fn_failed_stage(self) -> None:
        mt5 = _FakeMt5SenderModule(send_error=RuntimeError("send boom"))

        result = send_demo_order(_intent(), mt5, send_fn=build_mt5_demo_send_fn(mt5))

        self.assertEqual(result.lifecycle_record.stage, "demo_order_failed")

    def test_source_has_no_forbidden_execution_terms(self) -> None:
        source = _source_text()
        imports = _source_imports()

        self.assertNotIn("order_send", source)
        self.assertNotIn("TRADE_ACTION_", source)
        self.assertNotIn("trading_signal_order", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)
        self.assertNotIn("process_auto_trade", source)
        self.assertFalse(any(import_name.endswith("auto_trade") for import_name in imports))
        self.assertNotIn("positions_get", source)
        self.assertNotIn("orders_get", source)
        self.assertNotIn("MetaTrader5", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build_demo_send_dry_run_result(_intent(), _FakeMt5(SimpleNamespace(server="Broker Demo")))

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


class _FakeMt5:
    def __init__(self, account_info=None, error: Exception | None = None) -> None:
        self._account_info = account_info
        self._error = error

    def account_info(self):
        if self._error is not None:
            raise self._error
        return self._account_info


class _FakeSender:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def __call__(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class _FakeMt5Constants:
    def __init__(
        self,
        *,
        TRADE_ACTION_DEAL=100,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        ORDER_TIME_GTC=20,
        ORDER_FILLING_IOC=10,
        ORDER_FILLING_FOK=11,
        ORDER_FILLING_RETURN=12,
        symbol_filling_mode: int | None = None,
        symbol_info_error: Exception | None = None,
    ) -> None:
        self._values = {
            "TRADE_ACTION_DEAL": TRADE_ACTION_DEAL,
            "ORDER_TYPE_BUY": ORDER_TYPE_BUY,
            "ORDER_TYPE_SELL": ORDER_TYPE_SELL,
            "ORDER_TIME_GTC": ORDER_TIME_GTC,
            "ORDER_FILLING_IOC": ORDER_FILLING_IOC,
            "ORDER_FILLING_FOK": ORDER_FILLING_FOK,
            "ORDER_FILLING_RETURN": ORDER_FILLING_RETURN,
        }
        self.symbol_filling_mode = symbol_filling_mode
        self.symbol_info_error = symbol_info_error

    def __getattr__(self, name: str):
        if name not in self._values or self._values[name] is None:
            raise AttributeError(name)
        return self._values[name]

    def symbol_info(self, symbol: str):
        if self.symbol_info_error is not None:
            raise self.symbol_info_error
        if self.symbol_filling_mode is None:
            return None
        return SimpleNamespace(filling_mode=self.symbol_filling_mode)


class _FakeMt5SenderModule(_FakeMt5Constants):
    def __init__(
        self,
        *,
        send_result=None,
        send_error: Exception | None = None,
        **constants,
    ) -> None:
        super().__init__(**constants)
        self._account_info = SimpleNamespace(server="Broker Demo")
        self.send_result = send_result if send_result is not None else SimpleNamespace(retcode=10009)
        self.send_error = send_error
        self.order_send_calls = 0
        self.sent_requests: list[dict[str, object]] = []

    def account_info(self):
        return self._account_info

    def order_send(self, request):
        self.order_send_calls += 1
        self.sent_requests.append(request)
        if self.send_error is not None:
            raise self.send_error
        return self.send_result


class _FakeMt5PositionModule:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1


class _FakeMt5PositionsModule(_FakeMt5PositionModule):
    def __init__(self, positions=None, error: Exception | None = None) -> None:
        self.positions = positions
        self.error = error
        self.calls: list[str | None] = []

    def __getattr__(self, name: str):
        if name == "positions" + "_get":
            return self._read_positions
        raise AttributeError(name)

    def _read_positions(self, symbol: str | None = None):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return self.positions


def _intent(
    *,
    action: str = "BUY",
    order_type: str = "DEMO_BUY",
    volume: float = 0.01,
    signal_id: str | None = "sig-1",
) -> DemoOrderIntent:
    return DemoOrderIntent(
        symbol="XAUUSD",
        action=action,
        order_type=order_type,
        volume=volume,
        price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        comment="SignalBot demo execution",
        magic=21001,
        signal_id=signal_id,
        source_stage="approved",
        metadata={"source": "unit-test"},
    )


def _position_dict() -> dict[str, object]:
    return {
        "ticket": 123,
        "symbol": "XAUUSD",
        "volume": 0.01,
        "type": 0,
        "price_open": 100.0,
        "sl": 99.0,
        "tp": 102.0,
        "magic": 21001,
        "comment": "demo",
        "time": 123456,
    }


def _open_position(
    *,
    ticket: int | None = 123,
    symbol: str = "XAUUSD",
    action: str | None = "BUY",
) -> DemoOpenPosition:
    return DemoOpenPosition(
        ticket=ticket,
        symbol=symbol,
        volume=0.01,
        position_type=0 if action == "BUY" else 1 if action == "SELL" else None,
        action=action,
        price_open=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        magic=21001,
        comment="demo",
        time=123456,
    )


def _gate_intent() -> DemoOrderIntent:
    return _intent_with_metadata({"latest_execution_candle_time": "2026-01-01T00:00:00"})


def _intent_with_metadata(metadata: dict[str, object]) -> DemoOrderIntent:
    return DemoOrderIntent(
        symbol="XAUUSD",
        action="BUY",
        order_type="DEMO_BUY",
        volume=0.01,
        price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        comment="SignalBot demo execution",
        magic=21001,
        signal_id="sig-1",
        source_stage="approved",
        metadata=metadata,
    )


def _demo_result(
    *,
    accepted: bool = True,
    stage: str = "demo_order_accepted",
    reasons: tuple[str, ...] = (),
    retcode: int | None = 10009,
    comment: str | None = "accepted",
    ticket: int | None = 123,
    metadata: dict[str, object] | None = None,
    error_message: str | None = None,
) -> DemoSendResult:
    lifecycle = build_demo_request_lifecycle_record(
        stage,
        _intent(),
        approved=accepted,
        reasons=reasons,
        account_mode="demo",
        metadata=metadata,
    )
    lifecycle = lifecycle.__class__(
        timestamp=lifecycle.timestamp,
        stage=lifecycle.stage,
        symbol=lifecycle.symbol,
        action=lifecycle.action,
        volume=lifecycle.volume,
        approved=lifecycle.approved,
        reasons=lifecycle.reasons,
        account_mode=lifecycle.account_mode,
        mt5_retcode=retcode,
        mt5_comment=comment,
        ticket=ticket,
        metadata=lifecycle.metadata,
    )
    return DemoSendResult(
        attempted=True,
        accepted=accepted,
        lifecycle_record=lifecycle,
        request={"symbol": "XAUUSD"},
        error_message=error_message,
    )


def _demo_record(
    *,
    ticket: int | None = 123,
    comment: str | None = "accepted",
    reasons: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    record = demo_order_record_from_result(_intent(), _demo_result(ticket=ticket, comment=comment))
    record["reasons"] = [] if reasons is None else reasons
    record["metadata"] = {} if metadata is None else metadata
    return record


def _demo_request(*, order_type: str = "BUY") -> dict[str, object]:
    return {
        "symbol": "XAUUSD",
        "type": order_type,
        "volume": 0.01,
        "price": 100.0,
        "sl": 99.0,
        "tp": 102.0,
        "deviation": 20,
        "magic": 21001,
        "comment": "SignalBot demo execution",
    }


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
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "demo_mt5_sender.py"


if __name__ == "__main__":
    unittest.main()
