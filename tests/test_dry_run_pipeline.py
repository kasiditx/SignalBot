from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.dry_run_pipeline import (
    DryRunMarketInput,
    DryRunPipelineConfig,
    DryRunTradeInput,
    run_dry_run_pipeline,
)
from trading_signal_bot.execution_policy import ExecutionPolicyLimits
from trading_signal_bot.journal import JournalWriterConfig
from trading_signal_bot.models import Candle, SignalAction
from trading_signal_bot.risk_manager import DailyRiskState, PositionSizingInput, RiskLimits


class DryRunPipelineTest(unittest.TestCase):
    def test_live_mode_rejects_at_mode_validation(self) -> None:
        with _paths() as paths:
            result = _run(paths, trade_input=_trade(mode="live"))

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "mode_validation")
            self.assertIn("live mode is rejected", result.reasons[0])
            self.assertEqual(_events(paths.jsonl_path)[0]["event_type"], "ERROR")

    def test_missing_m1_candles_rejects_at_market_data(self) -> None:
        with _paths() as paths:
            result = _run(paths, candles={}, trade_input=_trade())

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "market_data")
            self.assertIn("No candles for execution timeframe M1", result.reasons)

    def test_missing_action_finishes_at_no_trade(self) -> None:
        with _paths() as paths:
            result = _run(paths, trade_input=_trade(action=None))

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "no_trade_filter")
            self.assertTrue(any("No trade action candidate" in reason for reason in result.reasons))
            self.assertIn("NO_TRADE", _event_types(paths.jsonl_path))

    def test_mid_zone_rejects_at_no_trade_filter(self) -> None:
        with _paths() as paths:
            result = _run(paths, market_input=_market(current_price=105.0))

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "no_trade_filter")
            self.assertTrue(any("middle of the zone" in reason for reason in result.reasons))

    def test_high_spread_rejects_at_execution_policy(self) -> None:
        with _paths() as paths:
            result = _run(paths, market_input=_market(spread_points=999.0))

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "execution_policy")
            self.assertIn("Spread is above maximum allowed", result.reasons)
            self.assertIn("EXECUTION_POLICY_REJECT", _event_types(paths.jsonl_path))

    def test_open_candle_rejects_at_execution_policy(self) -> None:
        with _paths() as paths:
            result = _run(paths, trade_input=_trade(candle_closed=False))

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "execution_policy")
            self.assertIn("Execution candle must be closed", result.reasons)

    def test_fakeout_rejects_before_risk(self) -> None:
        with _paths() as paths:
            result = _run(paths, candles=_candles_with_fakeout())

            self.assertFalse(result.approved)
            self.assertIn(result.stage, {"no_trade_filter", "execution_policy"})
            self.assertTrue(any("fakeout" in reason.lower() for reason in result.reasons))

    def test_low_rr_rejects_with_reason(self) -> None:
        with _paths() as paths:
            result = _run(paths, trade_input=_trade(risk_reward=1.2))

            self.assertFalse(result.approved)
            self.assertTrue(any("Risk/reward" in reason for reason in result.reasons))

    def test_risk_rejects_consecutive_losses(self) -> None:
        with _paths() as paths:
            state = DailyRiskState("2026-05-18", 0, 3, 3, 0.0)

            result = _run(paths, risk_state=state)

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "risk_manager")
            self.assertIn("Max consecutive losses reached", result.reasons)
            self.assertIn("RISK_MANAGER_REJECT", _event_types(paths.jsonl_path))

    def test_risk_rejects_same_direction_stack(self) -> None:
        with _paths() as paths:
            state = DailyRiskState("2026-05-18", 0, 0, 0, 0.0, open_directions=(SignalAction.BUY,))

            result = _run(paths, risk_state=state)

            self.assertFalse(result.approved)
            self.assertEqual(result.stage, "risk_manager")
            self.assertIn("Open position already exists in the same direction", result.reasons)

    def test_approved_path_returns_plan_and_risk_decision(self) -> None:
        with _paths() as paths:
            result = _run(paths)

            self.assertTrue(result.approved)
            self.assertEqual(result.stage, "approved")
            self.assertIsNotNone(result.execution_plan)
            self.assertIsNotNone(result.risk_decision)

    def test_approved_path_execution_plan_has_expected_levels(self) -> None:
        with _paths() as paths:
            result = _run(paths)

            self.assertEqual(result.execution_plan.entry, 100.0)
            self.assertEqual(result.execution_plan.stop_loss, 99.0)
            self.assertEqual(result.execution_plan.tp1, 101.0)
            self.assertEqual(result.execution_plan.tp2, 102.0)

    def test_approved_path_risk_decision_has_volume(self) -> None:
        with _paths() as paths:
            result = _run(paths)

            self.assertTrue(result.risk_decision.approved)
            self.assertEqual(result.risk_decision.volume, 0.1)

    def test_approved_path_logs_execution_plan_and_paper_intent(self) -> None:
        with _paths() as paths:
            result = _run(paths)

            self.assertTrue(result.approved)
            event_types = _event_types(paths.jsonl_path)
            self.assertIn("EXECUTION_PLAN_APPROVED", event_types)
            self.assertIn("PAPER_ORDER_INTENT", event_types)

    def test_paper_order_intent_metadata_marks_no_real_order(self) -> None:
        with _paths() as paths:
            _run(paths)

            event = _event_by_type(paths.jsonl_path, "PAPER_ORDER_INTENT")
            self.assertFalse(event["metadata"]["order_sent"])
            self.assertFalse(event["metadata"]["order_intent_written"])

    def test_paper_order_intent_metadata_order_sent_is_false(self) -> None:
        with _paths() as paths:
            _run(paths)

            event = _event_by_type(paths.jsonl_path, "PAPER_ORDER_INTENT")
            self.assertIs(event["metadata"]["order_sent"], False)

    def test_paper_order_intent_metadata_order_intent_written_is_false(self) -> None:
        with _paths() as paths:
            _run(paths)

            event = _event_by_type(paths.jsonl_path, "PAPER_ORDER_INTENT")
            self.assertIs(event["metadata"]["order_intent_written"], False)

    def test_journal_results_are_included(self) -> None:
        with _paths() as paths:
            result = _run(paths)

            self.assertTrue(result.journal_results)
            self.assertTrue(all(item.success for item in result.journal_results))

    def test_writes_jsonl_and_csv_to_temp_directory(self) -> None:
        with _paths(write_csv=True, write_jsonl=True) as paths:
            result = _run(paths)

            self.assertTrue(result.approved)
            self.assertTrue(paths.csv_path.exists())
            self.assertTrue(paths.jsonl_path.exists())

    def test_does_not_create_mt5_order_intent_file(self) -> None:
        with _paths() as paths:
            order_path = paths.directory / "trading_signal_order.csv"

            _run(paths)

            self.assertFalse(order_path.exists())


def _run(
    paths,
    candles: dict[str, list[Candle]] | None = None,
    trade_input: DryRunTradeInput | None = None,
    market_input: DryRunMarketInput | None = None,
    risk_state: DailyRiskState | None = None,
):
    return run_dry_run_pipeline(
        candles_by_timeframe=candles if candles is not None else _candles(),
        trade_input=trade_input or _trade(),
        market_input=market_input or _market(),
        execution_limits=ExecutionPolicyLimits(max_entry_deviation=1.0, max_spread_points=500.0),
        risk_limits=RiskLimits(),
        risk_state=risk_state or DailyRiskState("2026-05-18", 0, 0, 0, 0.0),
        sizing=PositionSizingInput(1000.0, 1.0, 100.0, 99.0, 100.0, 0.01, 1.0, 0.01),
        journal_config=JournalWriterConfig(
            csv_path=paths.csv_path,
            jsonl_path=paths.jsonl_path,
            write_csv=paths.write_csv,
            write_jsonl=paths.write_jsonl,
        ),
        pipeline_config=DryRunPipelineConfig(
            structure_lookback=1,
            min_swings=4,
            zone_proximity=0.2,
            zone_lookback=20,
        ),
    )


def _trade(
    action: SignalAction | None = SignalAction.BUY,
    mode: str = "paper",
    risk_reward: float | None = 1.5,
    candle_closed: bool = True,
) -> DryRunTradeInput:
    return DryRunTradeInput(
        symbol="XAUUSD",
        action=action,
        mode=mode,
        entry=100.0,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        risk_reward=risk_reward,
        candle_closed=candle_closed,
    )


def _market(
    current_price: float = 100.0,
    spread_points: float | None = 100.0,
) -> DryRunMarketInput:
    return DryRunMarketInput(
        current_price=current_price,
        spread_points=spread_points,
        atr_value=1.0,
        average_atr=1.0,
        session="London",
        high_impact_news_nearby=False,
    )


def _candles() -> dict[str, list[Candle]]:
    m1 = _uptrend_candles(prefix="M1")
    m5 = _uptrend_candles(prefix="M5")
    h4 = _uptrend_candles(prefix="H4")
    h1 = _uptrend_candles(prefix="H1")
    m30 = _zone_context_candles(prefix="M30")
    m15 = _zone_context_candles(prefix="M15")
    return {"M1": m1, "M5": m5, "H4": h4, "H1": h1, "M30": m30, "M15": m15}


def _candles_with_fakeout() -> dict[str, list[Candle]]:
    candles = _candles()
    candles["M1"] = [
        *candles["M1"][:-1],
        _candle("M1", 99, 100.0, 131.0, 99.0, 100.8),
    ]
    return candles


def _uptrend_candles(prefix: str) -> list[Candle]:
    values = [
        (10.0, 11.0, 9.0, 10.2),
        (10.2, 12.0, 9.8, 11.5),
        (11.5, 11.7, 9.5, 10.2),
        (10.2, 13.0, 10.0, 12.4),
        (12.4, 12.6, 10.5, 11.1),
        (11.1, 14.0, 12.5, 13.4),
        (13.4, 13.6, 11.2, 12.2),
        (12.2, 13.2, 11.8, 12.8),
        (99.2, 101.2, 99.0, 100.8),
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


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _event_types(path: Path) -> list[str]:
    return [str(event["event_type"]) for event in _events(path)]


def _event_by_type(path: Path, event_type: str) -> dict[str, object]:
    for event in _events(path):
        if event["event_type"] == event_type:
            return event
    raise AssertionError(f"Event not found: {event_type}")


class _paths:
    def __init__(self, write_csv: bool = False, write_jsonl: bool = True) -> None:
        self.write_csv = write_csv
        self.write_jsonl = write_jsonl
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


if __name__ == "__main__":
    unittest.main()
