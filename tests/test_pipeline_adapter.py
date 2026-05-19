from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trading_signal_bot.dry_run_pipeline import DryRunMarketInput
from trading_signal_bot.journal import JournalWriterConfig
from trading_signal_bot.models import (
    AutoTradeConfig,
    Candle,
    ExecutionPolicyConfig,
    RiskConfig,
    SignalAction,
    SignalConfig,
)
from trading_signal_bot.pipeline_adapter import (
    DryRunAdapterInput,
    DryRunAdapterResult,
    build_daily_risk_state,
    build_execution_limits,
    build_journal_config,
    build_pipeline_config,
    build_position_sizing_input,
    build_risk_limits,
    run_pipeline_from_configs,
)


class PipelineAdapterTest(unittest.TestCase):
    def test_build_pipeline_config_maps_execution_timeframe(self) -> None:
        config = build_pipeline_config(_signal_config())

        self.assertEqual(config.execution_timeframe, "M1")

    def test_build_pipeline_config_maps_momentum_timeframe(self) -> None:
        config = build_pipeline_config(_signal_config())

        self.assertEqual(config.momentum_timeframe, "M5")

    def test_build_pipeline_config_maps_zone_timeframes(self) -> None:
        config = build_pipeline_config(_signal_config())

        self.assertEqual(config.zone_timeframes, ("M30", "M15"))

    def test_build_pipeline_config_maps_htf_timeframes(self) -> None:
        config = build_pipeline_config(_signal_config())

        self.assertEqual(config.htf_timeframes, ("H4", "H1"))

    def test_build_pipeline_config_maps_minimum_risk_reward(self) -> None:
        config = build_pipeline_config(_signal_config())

        self.assertEqual(config.minimum_risk_reward, 1.5)

    def test_build_execution_limits_maps_max_spread_points(self) -> None:
        limits = build_execution_limits(_signal_config())

        self.assertEqual(limits.max_spread_points, 250)

    def test_build_execution_limits_maps_allowed_sessions(self) -> None:
        limits = build_execution_limits(_signal_config())

        self.assertEqual(limits.allowed_sessions, ("London", "NewYork"))

    def test_build_execution_limits_maps_news_filter(self) -> None:
        limits = build_execution_limits(_signal_config())

        self.assertTrue(limits.enable_news_filter)

    def test_build_execution_limits_maps_management_flags(self) -> None:
        limits = build_execution_limits(_signal_config())

        self.assertFalse(limits.enable_break_even)
        self.assertFalse(limits.enable_trailing_stop)
        self.assertTrue(limits.enable_partial_close)

    def test_build_risk_limits_maps_risk_per_trade(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.risk_per_trade, 0.75)

    def test_build_risk_limits_maps_max_daily_loss(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.max_daily_loss, 2.5)

    def test_build_risk_limits_maps_max_trades_per_day(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.max_trades_per_day, 6)

    def test_build_risk_limits_maps_max_consecutive_losses(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.max_consecutive_losses, 2)

    def test_build_risk_limits_maps_cooldown_minutes(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.cooldown_minutes, 45)

    def test_build_risk_limits_maps_minimum_risk_reward(self) -> None:
        limits = build_risk_limits(_signal_config())

        self.assertEqual(limits.minimum_risk_reward, 1.5)

    def test_build_position_sizing_input_uses_adapter_entry_and_stop_loss(self) -> None:
        sizing = build_position_sizing_input(_auto_trade_config(), _adapter_input(entry=100.5, stop_loss=99.2))

        self.assertEqual(sizing.entry, 100.5)
        self.assertEqual(sizing.stop_loss, 99.2)

    def test_run_pipeline_from_configs_caps_risk_percent_to_signal_limit(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths, signal_config=_signal_config(risk_per_trade=0.75))

            self.assertTrue(result.pipeline_result.approved)
            self.assertEqual(result.pipeline_result.risk_decision.risk_percent, 0.75)

    def test_build_position_sizing_input_maps_broker_volume_fields(self) -> None:
        sizing = build_position_sizing_input(_auto_trade_config(), _adapter_input())

        self.assertEqual(sizing.contract_size, 100.0)
        self.assertEqual(sizing.min_volume, 0.01)
        self.assertEqual(sizing.max_volume, 2.0)
        self.assertEqual(sizing.volume_step, 0.01)

    def test_build_daily_risk_state_creates_blank_state(self) -> None:
        state = build_daily_risk_state()

        self.assertEqual(state.trades_today, 0)
        self.assertEqual(state.losses_today, 0)
        self.assertEqual(state.consecutive_losses, 0)
        self.assertEqual(state.realized_loss_percent, 0.0)
        self.assertEqual(state.open_directions, ())

    def test_build_journal_config_returns_default_audit_paths(self) -> None:
        config = build_journal_config()

        self.assertEqual(config.csv_path, Path("logs/audit_journal.csv"))
        self.assertEqual(config.jsonl_path, Path("logs/audit_journal.jsonl"))
        self.assertTrue(config.write_csv)
        self.assertTrue(config.write_jsonl)

    def test_run_pipeline_from_configs_returns_adapter_result(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths)

            self.assertIsInstance(result, DryRunAdapterResult)

    def test_run_pipeline_from_configs_message_mentions_no_order_sent(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths)

            self.assertIn("No order was sent", result.message)

    def test_run_pipeline_from_configs_message_mentions_no_mt5_intent_written(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths)

            self.assertIn("No MT5 order intent was written", result.message)

    def test_live_mode_message_says_live_is_not_allowed(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths, adapter_input=_adapter_input(mode="live"))

            self.assertFalse(result.pipeline_result.approved)
            self.assertEqual(result.pipeline_result.stage, "mode_validation")
            self.assertIn("Live mode is not allowed", result.message)

    def test_missing_m1_candles_returns_rejected_pipeline_result(self) -> None:
        with _paths() as paths:
            candles = _candles()
            candles.pop("M1")

            result = _run_adapter(paths, candles=candles)

            self.assertFalse(result.pipeline_result.approved)
            self.assertEqual(result.pipeline_result.stage, "market_data")

    def test_result_always_contains_pipeline_result_and_message(self) -> None:
        with _paths() as paths:
            result = _run_adapter(paths)

            self.assertIsNotNone(result.pipeline_result)
            self.assertTrue(result.message)

    def test_source_ast_has_no_auto_trade_import(self) -> None:
        imports = _pipeline_adapter_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))

    def test_source_has_no_process_auto_trade_reference(self) -> None:
        source = _pipeline_adapter_path().read_text(encoding="utf-8")

        self.assertNotIn("process_auto_trade", source)

    def test_run_pipeline_does_not_write_mt5_order_intent_file(self) -> None:
        with _paths() as paths:
            order_file = paths.directory / "trading_signal_order.csv"

            _run_adapter(paths)

            self.assertFalse(order_file.exists())

    def test_run_pipeline_does_not_create_any_order_intent_named_file(self) -> None:
        with _paths() as paths:
            _run_adapter(paths)

            created_files = {path.name for path in paths.directory.rglob("*") if path.is_file()}
            self.assertEqual(created_files, {"audit.csv", "audit.jsonl"})


def _run_adapter(
    paths,
    candles: dict[str, list[Candle]] | None = None,
    signal_config: SignalConfig | None = None,
    auto_trade_config: AutoTradeConfig | None = None,
    adapter_input: DryRunAdapterInput | None = None,
) -> DryRunAdapterResult:
    with patch(
        "trading_signal_bot.pipeline_adapter.build_journal_config",
        return_value=JournalWriterConfig(csv_path=paths.csv_path, jsonl_path=paths.jsonl_path),
    ):
        return run_pipeline_from_configs(
            candles_by_timeframe=candles if candles is not None else _candles(),
            signal_config=signal_config or _signal_config(risk_per_trade=1.0),
            auto_trade_config=auto_trade_config or _auto_trade_config(),
            adapter_input=adapter_input or _adapter_input(),
            market_input=_market(),
        )


def _signal_config(
    risk_reward: float = 1.5,
    risk_per_trade: float = 0.75,
) -> SignalConfig:
    return SignalConfig(
        symbol="XAUUSD",
        timeframe="M5",
        csv_path="data/XAUUSD_M5.csv",
        fast_ema_period=9,
        slow_ema_period=21,
        rsi_period=14,
        atr_period=14,
        atr_multiplier=1.5,
        body_break_atr_ratio=0.25,
        risk_reward=risk_reward,
        min_candles=50,
        max_candle_age_minutes=5,
        multi_timeframe_enabled=True,
        timeframe_paths={},
        dry_run=True,
        send_wait=False,
        execution_timeframe="M1",
        momentum_timeframe="M5",
        zone_timeframes=("M30", "M15"),
        htf_timeframes=("H4", "H1"),
        risk_config=RiskConfig(
            risk_per_trade=risk_per_trade,
            max_daily_loss=2.5,
            max_trades_per_day=6,
            max_consecutive_losses=2,
            cooldown_minutes=45,
        ),
        execution_policy_config=ExecutionPolicyConfig(
            max_spread_points=250,
            allowed_sessions=("London", "NewYork"),
            enable_news_filter=True,
            enable_break_even=False,
            enable_trailing_stop=False,
            enable_partial_close=True,
        ),
    )


def _auto_trade_config(risk_percent: float = 1.5) -> AutoTradeConfig:
    return AutoTradeConfig(
        enabled=False,
        mode="paper",
        order_file="trading_signal_order.csv",
        journal_file="paper_journal.csv",
        account_balance=1000.0,
        risk_percent=risk_percent,
        contract_size=100.0,
        min_volume=0.01,
        max_volume=2.0,
        volume_step=0.01,
        allow_min_volume=False,
        magic_number=123456,
        comment="dry-run-test",
    )


def _adapter_input(
    action: SignalAction | None = SignalAction.BUY,
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    tp1: float | None = 101.0,
    tp2: float | None = 102.0,
    risk_reward: float | None = 1.5,
    mode: str = "paper",
) -> DryRunAdapterInput:
    return DryRunAdapterInput(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        mode=mode,
    )


def _market() -> DryRunMarketInput:
    return DryRunMarketInput(
        current_price=100.0,
        spread_points=100.0,
        atr_value=1.0,
        average_atr=1.0,
        session="London",
        high_impact_news_nearby=False,
    )


def _candles() -> dict[str, list[Candle]]:
    return {
        "M1": _uptrend_candles("M1"),
        "M5": _uptrend_candles("M5"),
        "H4": _uptrend_candles("H4"),
        "H1": _uptrend_candles("H1"),
        "M30": _zone_context_candles("M30"),
        "M15": _zone_context_candles("M15"),
    }


def _uptrend_candles(prefix: str) -> list[Candle]:
    values = [
        (9.0, 10.0, 7.0, 9.0),
        (9.0, 11.0, 8.0, 10.0),
        (10.0, 15.0, 9.0, 14.0),
        (14.0, 12.0, 8.5, 11.0),
        (11.0, 11.0, 8.2, 10.0),
        (10.0, 13.0, 7.5, 12.0),
        (12.0, 14.0, 8.8, 13.0),
        (13.0, 18.0, 9.0, 17.0),
        (17.0, 15.0, 10.0, 14.0),
        (14.0, 14.0, 9.5, 13.0),
        (13.0, 16.0, 9.2, 15.0),
        (15.0, 17.0, 10.0, 16.0),
        (99.2, 101.2, 99.0, 100.8),
        (100.8, 101.4, 100.0, 101.3),
    ]
    return [_candle(prefix, index, open_, high, low, close) for index, (open_, high, low, close) in enumerate(values)]


def _zone_context_candles(prefix: str) -> list[Candle]:
    values = [
        (95.0, 96.0, 94.0, 95.5),
        (95.5, 97.0, 95.0, 96.5),
        (96.5, 98.0, 96.0, 97.5),
        (97.5, 99.0, 97.0, 98.2),
        (98.2, 130.0, 98.0, 125.0),
    ]
    return [_candle(prefix, index, open_, high, low, close) for index, (open_, high, low, close) in enumerate(values)]


def _candle(prefix: str, index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 {prefix}:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


def _pipeline_adapter_imports() -> list[str]:
    tree = ast.parse(_pipeline_adapter_path().read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return imports


def _pipeline_adapter_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "pipeline_adapter.py"


class _paths:
    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.directory: Path
        self.csv_path: Path
        self.jsonl_path: Path

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.csv_path = self.directory / "audit.csv"
        self.jsonl_path = self.directory / "audit.jsonl"
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._tmp is not None:
            self._tmp.cleanup()


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
