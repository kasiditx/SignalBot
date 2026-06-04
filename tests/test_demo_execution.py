from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_signal_bot.demo_execution import (
    DemoExecutionConfig,
    DemoExecutionState,
    DemoOrderCandidate,
    DemoOrderIntent,
    _demo_order_type,
    build_demo_lifecycle_record,
    build_demo_order_intent,
    default_demo_execution_config,
    demo_stop_active,
    is_demo_mode,
    validate_demo_execution_allowed,
    validate_demo_order_candidate,
    validate_demo_risk_state,
)


class DemoExecutionGuardTest(unittest.TestCase):
    def test_default_demo_execution_config_values(self) -> None:
        output_dir = Path("logs/demo_execution")

        config = default_demo_execution_config(output_dir)

        self.assertEqual(config.mode, "demo")
        self.assertEqual(config.allowed_symbols, ())
        self.assertEqual(config.max_lot, 0.01)
        self.assertEqual(config.max_trades_per_day, 3)
        self.assertEqual(config.max_open_positions, 1)
        self.assertEqual(config.max_daily_loss_percent, 2.0)
        self.assertEqual(config.max_spread_points, 30.0)
        self.assertTrue(config.require_stop_loss)
        self.assertEqual(config.cooldown_minutes, 30)
        self.assertEqual(config.max_consecutive_losses, 2)

    def test_default_demo_execution_config_stop_demo_execution_path(self) -> None:
        output_dir = Path("logs/demo_execution")

        config = default_demo_execution_config(output_dir)

        self.assertEqual(config.stop_demo_execution_path, output_dir / "STOP_DEMO_EXECUTION")

    def test_default_demo_execution_config_stop_all_trading_path(self) -> None:
        output_dir = Path("logs/demo_execution")

        config = default_demo_execution_config(output_dir)

        self.assertEqual(config.stop_all_trading_path, output_dir / "STOP_ALL_TRADING")

    def test_is_demo_mode_accepts_demo(self) -> None:
        self.assertTrue(is_demo_mode("demo"))

    def test_is_demo_mode_accepts_paper_demo(self) -> None:
        self.assertTrue(is_demo_mode("paper_demo"))

    def test_is_demo_mode_accepts_mt5_demo(self) -> None:
        self.assertTrue(is_demo_mode("mt5_demo"))

    def test_is_demo_mode_rejects_live(self) -> None:
        self.assertFalse(is_demo_mode("live"))

    def test_is_demo_mode_rejects_real(self) -> None:
        self.assertFalse(is_demo_mode("real"))

    def test_is_demo_mode_rejects_production(self) -> None:
        self.assertFalse(is_demo_mode("production"))

    def test_is_demo_mode_is_case_insensitive(self) -> None:
        self.assertTrue(is_demo_mode("MT5_DEMO"))

    def test_demo_stop_active_without_stop_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active, reasons = demo_stop_active(_config(Path(directory)))

        self.assertFalse(active)
        self.assertEqual(reasons, ())

    def test_demo_stop_active_demo_stop_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))
            config.stop_demo_execution_path.write_text("stop", encoding="utf-8")

            active, reasons = demo_stop_active(config)

        self.assertTrue(active)
        self.assertEqual(reasons, ("demo execution stop file active",))

    def test_demo_stop_active_global_stop_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))
            config.stop_all_trading_path.write_text("stop", encoding="utf-8")

            active, reasons = demo_stop_active(config)

        self.assertTrue(active)
        self.assertEqual(reasons, ("global trading stop file active",))

    def test_demo_stop_active_both_stop_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))
            config.stop_demo_execution_path.write_text("stop", encoding="utf-8")
            config.stop_all_trading_path.write_text("stop", encoding="utf-8")

            active, reasons = demo_stop_active(config)

        self.assertTrue(active)
        self.assertEqual(
            reasons,
            ("demo execution stop file active", "global trading stop file active"),
        )

    def test_validate_demo_order_candidate_valid_buy(self) -> None:
        result = validate_demo_order_candidate(_candidate(action="BUY"), _config(Path("logs/demo")))

        self.assertTrue(result.approved)
        self.assertEqual(result.reasons, ())

    def test_validate_demo_order_candidate_valid_sell(self) -> None:
        result = validate_demo_order_candidate(_candidate(action="SELL"), _config(Path("logs/demo")))

        self.assertTrue(result.approved)
        self.assertEqual(result.reasons, ())

    def test_validate_demo_order_candidate_rejects_symbol_not_in_allowlist(self) -> None:
        result = validate_demo_order_candidate(
            _candidate(symbol="EURUSD"),
            _config(Path("logs/demo"), allowed_symbols=("XAUUSD",)),
        )

        self.assertEqual(result.reasons, ("symbol is not allowed",))

    def test_validate_demo_order_candidate_rejects_invalid_action(self) -> None:
        result = validate_demo_order_candidate(_candidate(action="WAIT"), _config(Path("logs/demo")))

        self.assertIn("action must be BUY or SELL", result.reasons)

    def test_validate_demo_order_candidate_rejects_non_positive_volume(self) -> None:
        result = validate_demo_order_candidate(_candidate(volume=0), _config(Path("logs/demo")))

        self.assertIn("volume must be greater than zero", result.reasons)

    def test_validate_demo_order_candidate_rejects_volume_above_max_lot(self) -> None:
        result = validate_demo_order_candidate(_candidate(volume=0.02), _config(Path("logs/demo"), max_lot=0.01))

        self.assertIn("volume exceeds max lot", result.reasons)

    def test_validate_demo_order_candidate_rejects_missing_stop_loss_when_required(self) -> None:
        result = validate_demo_order_candidate(_candidate(stop_loss=None), _config(Path("logs/demo")))

        self.assertIn("stop loss is required", result.reasons)

    def test_validate_demo_order_candidate_rejects_missing_take_profit(self) -> None:
        result = validate_demo_order_candidate(_candidate(take_profit=None), _config(Path("logs/demo")))

        self.assertIn("take profit is required", result.reasons)

    def test_validate_demo_order_candidate_rejects_missing_risk_reward(self) -> None:
        result = validate_demo_order_candidate(_candidate(risk_reward=None), _config(Path("logs/demo")))

        self.assertIn("risk reward must be greater than zero", result.reasons)

    def test_validate_demo_order_candidate_rejects_non_positive_risk_reward(self) -> None:
        result = validate_demo_order_candidate(_candidate(risk_reward=0), _config(Path("logs/demo")))

        self.assertIn("risk reward must be greater than zero", result.reasons)

    def test_validate_demo_risk_state_rejects_max_trades_per_day(self) -> None:
        result = validate_demo_risk_state(DemoExecutionState(trades_today=3), _config(Path("logs/demo")))

        self.assertIn("max trades per day reached", result.reasons)

    def test_validate_demo_risk_state_rejects_max_open_positions(self) -> None:
        result = validate_demo_risk_state(DemoExecutionState(open_positions=1), _config(Path("logs/demo")))

        self.assertIn("max open positions reached", result.reasons)

    def test_validate_demo_risk_state_rejects_max_daily_loss(self) -> None:
        result = validate_demo_risk_state(DemoExecutionState(daily_loss_percent=2.0), _config(Path("logs/demo")))

        self.assertIn("max daily loss reached", result.reasons)

    def test_validate_demo_risk_state_rejects_max_consecutive_losses(self) -> None:
        result = validate_demo_risk_state(DemoExecutionState(consecutive_losses=2), _config(Path("logs/demo")))

        self.assertIn("max consecutive losses reached", result.reasons)

    def test_validate_demo_risk_state_rejects_active_cooldown(self) -> None:
        cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        result = validate_demo_risk_state(DemoExecutionState(cooldown_until=cooldown_until), _config(Path("logs/demo")))

        self.assertIn("cooldown active", result.reasons)

    def test_validate_demo_risk_state_approves_normal_state(self) -> None:
        result = validate_demo_risk_state(DemoExecutionState(), _config(Path("logs/demo")))

        self.assertTrue(result.approved)
        self.assertEqual(result.reasons, ())

    def test_validate_demo_execution_allowed_rejects_live_mode(self) -> None:
        result = validate_demo_execution_allowed(
            _candidate(),
            DemoExecutionState(),
            _config(Path("logs/demo"), mode="live"),
            "demo",
            10.0,
        )

        self.assertIn("demo execution mode is required", result.reasons)

    def test_validate_demo_execution_allowed_rejects_live_account_mode(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "live", 10.0)

        self.assertIn("live account is not allowed", result.reasons)

    def test_validate_demo_execution_allowed_rejects_real_account_mode(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "real", 10.0)

        self.assertIn("live account is not allowed", result.reasons)

    def test_validate_demo_execution_allowed_accepts_demo_account_mode(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "demo", 10.0)

        self.assertTrue(result.approved)

    def test_validate_demo_execution_allowed_rejects_missing_account_mode(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), None, 10.0)

        self.assertIn("demo account confirmation is required", result.reasons)

    def test_validate_demo_execution_allowed_rejects_stop_file_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))
            config.stop_demo_execution_path.write_text("stop", encoding="utf-8")

            result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), config, "demo", 10.0)

        self.assertIn("demo execution stop file active", result.reasons)

    def test_validate_demo_execution_allowed_rejects_missing_spread(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "demo", None)

        self.assertIn("spread points are required", result.reasons)

    def test_validate_demo_execution_allowed_rejects_high_spread(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "demo", 31.0)

        self.assertIn("spread exceeds max spread", result.reasons)

    def test_validate_demo_execution_allowed_combines_candidate_reasons(self) -> None:
        result = validate_demo_execution_allowed(
            _candidate(volume=0, take_profit=None),
            DemoExecutionState(),
            _config(Path("logs/demo")),
            "demo",
            10.0,
        )

        self.assertIn("volume must be greater than zero", result.reasons)
        self.assertIn("take profit is required", result.reasons)

    def test_validate_demo_execution_allowed_combines_risk_state_reasons(self) -> None:
        result = validate_demo_execution_allowed(
            _candidate(),
            DemoExecutionState(trades_today=3, open_positions=1),
            _config(Path("logs/demo")),
            "demo",
            10.0,
        )

        self.assertIn("max trades per day reached", result.reasons)
        self.assertIn("max open positions reached", result.reasons)

    def test_validate_demo_execution_allowed_approves_valid_inputs(self) -> None:
        result = validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path("logs/demo")), "demo", 10.0)

        self.assertTrue(result.approved)
        self.assertEqual(result.reasons, ())

    def test_build_demo_lifecycle_record_fields(self) -> None:
        guard_result = validate_demo_order_candidate(_candidate(), _config(Path("logs/demo")))

        record = build_demo_lifecycle_record(
            "guard_checked",
            _candidate(),
            guard_result,
            account_mode="demo",
            metadata={"source": "unit-test"},
        )

        self.assertTrue(datetime.fromisoformat(record.timestamp))
        self.assertEqual(record.stage, "guard_checked")
        self.assertEqual(record.symbol, "XAUUSD")
        self.assertEqual(record.action, "BUY")
        self.assertEqual(record.volume, 0.01)
        self.assertEqual(record.approved, guard_result.approved)
        self.assertEqual(record.reasons, guard_result.reasons)
        self.assertEqual(record.account_mode, "demo")
        self.assertEqual(record.metadata["source"], "unit-test")

    def test_create_demo_order_intent_with_all_fields(self) -> None:
        intent = DemoOrderIntent(
            symbol="XAUUSD",
            action="BUY",
            order_type="DEMO_BUY",
            volume=0.01,
            price=100.0,
            stop_loss=99.0,
            take_profit=102.0,
            risk_reward=2.0,
            comment="comment",
            magic=123,
            signal_id="sig-1",
            source_stage="approved",
            metadata={"source": "unit-test"},
        )

        self.assertEqual(intent.symbol, "XAUUSD")
        self.assertEqual(intent.order_type, "DEMO_BUY")
        self.assertEqual(intent.metadata["source"], "unit-test")

    def test_demo_order_intent_metadata_is_preserved(self) -> None:
        metadata = {"source": "unit-test"}
        intent = DemoOrderIntent(
            symbol="XAUUSD",
            action="BUY",
            order_type="DEMO_BUY",
            volume=0.01,
            price=100.0,
            stop_loss=99.0,
            take_profit=102.0,
            risk_reward=2.0,
            comment="comment",
            magic=123,
            signal_id="sig-1",
            source_stage="approved",
            metadata=metadata,
        )

        self.assertEqual(intent.metadata, metadata)

    def test_demo_order_type_maps_buy(self) -> None:
        self.assertEqual(_demo_order_type("BUY"), "DEMO_BUY")

    def test_demo_order_type_maps_sell(self) -> None:
        self.assertEqual(_demo_order_type("SELL"), "DEMO_SELL")

    def test_demo_order_type_invalid_action_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "action must be BUY or SELL"):
            _demo_order_type("WAIT")

    def test_build_demo_order_intent_valid_buy(self) -> None:
        intent = build_demo_order_intent(_candidate(action="BUY"), _approved_guard())

        self.assertEqual(intent.action, "BUY")
        self.assertEqual(intent.order_type, "DEMO_BUY")

    def test_build_demo_order_intent_valid_sell(self) -> None:
        intent = build_demo_order_intent(_candidate(action="SELL"), _approved_guard())

        self.assertEqual(intent.action, "SELL")
        self.assertEqual(intent.order_type, "DEMO_SELL")

    def test_build_demo_order_intent_rejects_unapproved_guard(self) -> None:
        guard_result = validate_demo_order_candidate(_candidate(volume=0), _config(Path("logs/demo")))

        with self.assertRaisesRegex(ValueError, "demo guard must be approved before building intent"):
            build_demo_order_intent(_candidate(), guard_result)

    def test_build_demo_order_intent_rejects_invalid_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "action must be BUY or SELL"):
            build_demo_order_intent(_candidate(action="WAIT"), _approved_guard())

    def test_build_demo_order_intent_rejects_missing_stop_loss(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop loss is required"):
            build_demo_order_intent(_candidate(stop_loss=None), _approved_guard())

    def test_build_demo_order_intent_rejects_missing_take_profit(self) -> None:
        with self.assertRaisesRegex(ValueError, "take profit is required"):
            build_demo_order_intent(_candidate(take_profit=None), _approved_guard())

    def test_build_demo_order_intent_rejects_missing_risk_reward(self) -> None:
        with self.assertRaisesRegex(ValueError, "risk reward is required"):
            build_demo_order_intent(_candidate(risk_reward=None), _approved_guard())

    def test_build_demo_order_intent_rejects_non_positive_risk_reward(self) -> None:
        with self.assertRaisesRegex(ValueError, "risk reward is required"):
            build_demo_order_intent(_candidate(risk_reward=0), _approved_guard())

    def test_build_demo_order_intent_default_comment_and_magic(self) -> None:
        intent = build_demo_order_intent(_candidate(), _approved_guard())

        self.assertEqual(intent.comment, "SignalBot demo execution")
        self.assertEqual(intent.magic, 21001)

    def test_build_demo_order_intent_custom_comment_and_magic(self) -> None:
        intent = build_demo_order_intent(
            _candidate(),
            _approved_guard(),
            comment="custom demo",
            magic=999,
        )

        self.assertEqual(intent.comment, "custom demo")
        self.assertEqual(intent.magic, 999)

    def test_build_demo_order_intent_uses_candidate_entry_as_price(self) -> None:
        intent = build_demo_order_intent(_candidate(entry=123.45), _approved_guard())

        self.assertEqual(intent.price, 123.45)

    def test_build_demo_order_intent_preserves_signal_id_and_source_stage(self) -> None:
        intent = build_demo_order_intent(_candidate(signal_id="sig-2", source_stage="realtime_signal"), _approved_guard())

        self.assertEqual(intent.signal_id, "sig-2")
        self.assertEqual(intent.source_stage, "realtime_signal")

    def test_build_demo_order_intent_copies_metadata(self) -> None:
        metadata = {"source": "unit-test"}
        candidate = _candidate(metadata=metadata)

        intent = build_demo_order_intent(candidate, _approved_guard())
        metadata["source"] = "changed"

        self.assertEqual(intent.metadata["source"], "unit-test")

    def test_build_demo_lifecycle_record_demo_intent_created(self) -> None:
        record = build_demo_lifecycle_record(
            "demo_intent_created",
            _candidate(),
            _approved_guard(),
            account_mode="demo",
        )

        self.assertEqual(record.stage, "demo_intent_created")
        self.assertTrue(record.approved)

    def test_build_demo_lifecycle_record_demo_intent_rejected(self) -> None:
        guard_result = validate_demo_order_candidate(_candidate(volume=0), _config(Path("logs/demo")))

        record = build_demo_lifecycle_record(
            "demo_intent_rejected",
            _candidate(volume=0),
            guard_result,
            account_mode="demo",
        )

        self.assertEqual(record.stage, "demo_intent_rejected")
        self.assertFalse(record.approved)
        self.assertEqual(record.reasons, guard_result.reasons)

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

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path(directory)), "demo", 10.0)

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            validate_demo_execution_allowed(_candidate(), DemoExecutionState(), _config(Path(directory)), "demo", 10.0)

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


def _config(
    output_dir: Path,
    *,
    mode: str = "demo",
    allowed_symbols: tuple[str, ...] = (),
    max_lot: float = 0.01,
) -> DemoExecutionConfig:
    return DemoExecutionConfig(
        mode=mode,
        allowed_symbols=allowed_symbols,
        max_lot=max_lot,
        max_trades_per_day=3,
        max_open_positions=1,
        max_daily_loss_percent=2.0,
        max_spread_points=30.0,
        require_stop_loss=True,
        cooldown_minutes=30,
        max_consecutive_losses=2,
        stop_demo_execution_path=output_dir / "STOP_DEMO_EXECUTION",
        stop_all_trading_path=output_dir / "STOP_ALL_TRADING",
    )


def _candidate(
    *,
    symbol: str = "XAUUSD",
    action: str = "BUY",
    volume: float = 0.01,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    take_profit: float | None = 102.0,
    risk_reward: float | None = 2.0,
    source_stage: str | None = "approved",
    signal_id: str | None = "sig-1",
    metadata: dict[str, object] | None = None,
) -> DemoOrderCandidate:
    return DemoOrderCandidate(
        symbol=symbol,
        action=action,
        volume=volume,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        source_stage=source_stage,
        signal_id=signal_id,
        metadata=metadata or {"source": "unit-test"},
    )


def _approved_guard():
    return validate_demo_order_candidate(_candidate(), _config(Path("logs/demo")))


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
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "demo_execution.py"


if __name__ == "__main__":
    unittest.main()
