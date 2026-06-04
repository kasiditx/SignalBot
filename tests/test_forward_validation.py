from __future__ import annotations

import ast
import csv
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from trading_signal_bot.forward_validation import (
    CSV_FIELDNAMES,
    FORWARD_CSV_ENCODING,
    FORWARD_JSONL_ENCODING,
    ForwardSummary,
    ForwardValidationConfig,
    ForwardValidationInput,
    ForwardValidationRecord,
    ForwardValidationResult,
    append_forward_record_jsonl,
    forward_record_from_pipeline_result,
    load_forward_records_jsonl,
    run_forward_validation,
    summarize_forward_records_daily,
    summarize_forward_records_weekly,
    write_forward_record,
    write_forward_record_csv,
)
from trading_signal_bot.models import SignalAction


class ForwardValidationCoreTest(unittest.TestCase):
    def test_create_forward_validation_input_with_all_fields(self) -> None:
        validation_input = _validation_input(metadata={"source": "unit-test"})

        self.assertEqual(validation_input.symbol, "XAUUSD")
        self.assertEqual(validation_input.mode, "paper")
        self.assertEqual(validation_input.action, SignalAction.BUY)
        self.assertEqual(validation_input.entry, 100.0)
        self.assertEqual(validation_input.stop_loss, 99.0)
        self.assertEqual(validation_input.tp1, 101.0)
        self.assertEqual(validation_input.tp2, 102.0)
        self.assertEqual(validation_input.risk_reward, 1.5)
        self.assertEqual(validation_input.current_price, 100.1)
        self.assertEqual(validation_input.spread_points, 20.0)
        self.assertEqual(validation_input.session, "London")
        self.assertEqual(validation_input.metadata["source"], "unit-test")

    def test_create_forward_validation_config_with_all_fields(self) -> None:
        config = _config(Path("logs/forward"))

        self.assertEqual(config.record_csv_path, Path("logs/forward/records.csv"))
        self.assertEqual(config.record_jsonl_path, Path("logs/forward/records.jsonl"))
        self.assertEqual(config.daily_summary_path, Path("logs/forward/daily.csv"))
        self.assertEqual(config.weekly_summary_path, Path("logs/forward/weekly.csv"))
        self.assertTrue(config.write_csv)
        self.assertTrue(config.write_jsonl)

    def test_create_forward_validation_record_with_all_fields(self) -> None:
        record = _record()

        self.assertEqual(record.timestamp, "2026-05-21T00:00:00+00:00")
        self.assertEqual(record.symbol, "XAUUSD")
        self.assertEqual(record.mode, "paper")
        self.assertEqual(record.action, "BUY")
        self.assertEqual(record.stage, "approved")
        self.assertTrue(record.approved)
        self.assertEqual(record.reasons, ())
        self.assertFalse(record.order_sent)
        self.assertFalse(record.order_intent_written)

    def test_create_forward_validation_result_with_all_fields(self) -> None:
        record = _record()
        pipeline_result = _pipeline_result()
        result = ForwardValidationResult(
            record=record,
            pipeline_result=pipeline_result,
            write_success=True,
            error_message=None,
        )

        self.assertEqual(result.record, record)
        self.assertEqual(result.pipeline_result, pipeline_result)
        self.assertTrue(result.write_success)
        self.assertIsNone(result.error_message)

    def test_create_forward_summary_with_all_fields(self) -> None:
        summary = ForwardSummary(
            total_runs=3,
            approved_count=1,
            rejected_count=2,
            no_trade_count=1,
            execution_reject_count=1,
            risk_reject_count=0,
            paper_intent_count=1,
            journal_failures=1,
            order_sent_count=0,
            order_intent_written_count=0,
            reason_summary={"spread high": 1},
        )

        self.assertEqual(summary.total_runs, 3)
        self.assertEqual(summary.reason_summary["spread high"], 1)

    def test_forward_record_maps_pipeline_result_fields(self) -> None:
        record = forward_record_from_pipeline_result(
            _validation_input(metadata={"run_id": "abc"}),
            _pipeline_result(),
            timestamp="2026-05-21T00:00:00+00:00",
        )

        self.assertEqual(record.stage, "approved")
        self.assertTrue(record.approved)
        self.assertEqual(record.reasons, ("ready",))
        self.assertTrue(record.execution_plan_present)
        self.assertTrue(record.risk_decision_present)
        self.assertTrue(record.journal_success)
        self.assertFalse(record.order_sent)
        self.assertFalse(record.order_intent_written)
        self.assertEqual(record.metadata["run_id"], "abc")

    def test_forward_record_journal_success_false_when_any_journal_result_fails(self) -> None:
        record = forward_record_from_pipeline_result(
            _validation_input(),
            _pipeline_result(journal_results=(_JournalResult(True), _JournalResult(False))),
        )

        self.assertFalse(record.journal_success)

    def test_forward_record_handles_string_reason(self) -> None:
        record = forward_record_from_pipeline_result(
            _validation_input(),
            _pipeline_result(reasons="single reason"),
        )

        self.assertEqual(record.reasons, ("single reason",))

    def test_live_mode_guard_ignores_pipeline_result(self) -> None:
        result = run_forward_validation(
            _validation_input(mode="live"),
            _pipeline_result(stage="approved", approved=True, reasons=("should be ignored",)),
            _config(Path(tempfile.mkdtemp())),
        )

        self.assertEqual(result.record.stage, "mode_validation")
        self.assertFalse(result.record.approved)
        self.assertEqual(result.record.reasons, ("live mode is not allowed",))
        self.assertFalse(result.record.order_sent)
        self.assertFalse(result.record.order_intent_written)

    def test_write_forward_record_csv_writes_header_and_serialized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "records.csv"

            write_forward_record_csv(_record(reasons=("reason1", "reason2"), metadata={"a": 1}), path)

            rows = _csv_rows(path)
            self.assertEqual(_csv_header(path), list(CSV_FIELDNAMES))
            self.assertEqual(rows[0]["reasons"], "reason1 | reason2")
            self.assertEqual(json.loads(rows[0]["metadata"]), {"a": 1})

    def test_append_forward_record_jsonl_writes_parseable_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "records.jsonl"

            append_forward_record_jsonl(_record(reasons=("reason1",), metadata={"a": 1}), path)

            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["reasons"], ["reason1"])
            self.assertEqual(payload["metadata"], {"a": 1})

    def test_append_forward_record_jsonl_preserves_thai_reason_roundtrip(self) -> None:
        thai_reason = "ยังไม่มีสัญญาณเข้าเทรด"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "records.jsonl"

            append_forward_record_jsonl(_record(reasons=(thai_reason,), metadata={"note": thai_reason}), path)

            content = path.read_text(encoding=FORWARD_JSONL_ENCODING)
            records = load_forward_records_jsonl(path)

        self.assertIn(thai_reason, content)
        self.assertNotIn("à¸", content)
        self.assertEqual(records[0].reasons, (thai_reason,))
        self.assertEqual(records[0].metadata["note"], thai_reason)

    def test_write_forward_record_csv_preserves_thai_reason_with_utf8_sig(self) -> None:
        thai_reason = "ยังไม่มีสัญญาณเข้าเทรด"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "records.csv"

            write_forward_record_csv(_record(reasons=(thai_reason,), metadata={"note": thai_reason}), path)

            content = path.read_text(encoding=FORWARD_CSV_ENCODING)

        self.assertIn(thai_reason, content)
        self.assertNotIn("à¸", content)

    def test_write_forward_record_writes_enabled_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))

            success = write_forward_record(_record(), config)

            self.assertTrue(success)
            self.assertTrue(config.record_csv_path.exists())
            self.assertTrue(config.record_jsonl_path.exists())

    def test_write_forward_record_can_write_csv_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory), write_csv=True, write_jsonl=False)

            success = write_forward_record(_record(), config)

            self.assertTrue(success)
            self.assertTrue(config.record_csv_path.exists())
            self.assertFalse(config.record_jsonl_path.exists())

    def test_write_forward_record_can_write_jsonl_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory), write_csv=False, write_jsonl=True)

            success = write_forward_record(_record(), config)

            self.assertTrue(success)
            self.assertFalse(config.record_csv_path.exists())
            self.assertTrue(config.record_jsonl_path.exists())

    def test_write_forward_record_returns_false_when_all_outputs_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory), write_csv=False, write_jsonl=False)

            self.assertFalse(write_forward_record(_record(), config))

    def test_summarize_forward_records_daily_counts_records(self) -> None:
        summary = summarize_forward_records_daily(tuple(_summary_records()))

        self.assertEqual(summary.total_runs, 5)
        self.assertEqual(summary.approved_count, 1)
        self.assertEqual(summary.rejected_count, 4)
        self.assertEqual(summary.no_trade_count, 1)
        self.assertEqual(summary.execution_reject_count, 1)
        self.assertEqual(summary.risk_reject_count, 1)
        self.assertEqual(summary.paper_intent_count, 1)
        self.assertEqual(summary.journal_failures, 1)
        self.assertEqual(summary.order_sent_count, 0)
        self.assertEqual(summary.order_intent_written_count, 0)
        self.assertEqual(summary.reason_summary["spread high"], 1)

    def test_summarize_forward_records_weekly_uses_same_aggregation(self) -> None:
        summary = summarize_forward_records_weekly(tuple(_summary_records()))

        self.assertEqual(summary.total_runs, 5)
        self.assertEqual(summary.approved_count, 1)
        self.assertEqual(summary.reason_summary["cooldown active"], 1)

    def test_run_forward_validation_writes_approved_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_forward_validation(_validation_input(), _pipeline_result(), _config(Path(directory)))

            self.assertTrue(result.record.approved)
            self.assertTrue(result.write_success)
            self.assertIsNone(result.error_message)

    def test_run_forward_validation_writes_rejected_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline_result = _pipeline_result(stage="execution_policy", approved=False, reasons=("spread high",))

            result = run_forward_validation(_validation_input(), pipeline_result, _config(Path(directory)))

            self.assertFalse(result.record.approved)
            self.assertEqual(result.record.reasons, ("spread high",))
            self.assertTrue(result.write_success)
            self.assertIsNone(result.error_message)

    def test_source_has_no_forbidden_auto_trade_terms(self) -> None:
        source = _source_text()
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("trading_signal_order", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_forward_validation(_validation_input(), _pipeline_result(), _config(Path(directory) / "forward"))

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_forward_validation(_validation_input(), _pipeline_result(), _config(Path(directory) / "forward"))

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


@dataclass(frozen=True)
class _JournalResult:
    success: bool


def _validation_input(
    *,
    mode: str = "paper",
    metadata: dict[str, object] | None = None,
) -> ForwardValidationInput:
    return ForwardValidationInput(
        symbol="XAUUSD",
        mode=mode,
        action=SignalAction.BUY,
        entry=100.0,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        risk_reward=1.5,
        current_price=100.1,
        spread_points=20.0,
        session="London",
        metadata=metadata or {},
    )


def _config(
    base: Path,
    *,
    write_csv: bool = True,
    write_jsonl: bool = True,
) -> ForwardValidationConfig:
    return ForwardValidationConfig(
        record_csv_path=base / "records.csv",
        record_jsonl_path=base / "records.jsonl",
        daily_summary_path=base / "daily.csv",
        weekly_summary_path=base / "weekly.csv",
        write_csv=write_csv,
        write_jsonl=write_jsonl,
    )


def _record(
    *,
    stage: str = "approved",
    approved: bool = True,
    reasons: tuple[str, ...] = (),
    journal_success: bool = True,
    metadata: dict[str, object] | None = None,
) -> ForwardValidationRecord:
    return ForwardValidationRecord(
        timestamp="2026-05-21T00:00:00+00:00",
        symbol="XAUUSD",
        mode="paper",
        action="BUY",
        stage=stage,
        approved=approved,
        reasons=reasons,
        entry=100.0,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        risk_reward=1.5,
        execution_plan_present=True,
        risk_decision_present=True,
        order_sent=False,
        order_intent_written=False,
        journal_success=journal_success,
        metadata=metadata or {},
    )


def _pipeline_result(
    *,
    stage: str = "approved",
    approved: bool = True,
    reasons: tuple[str, ...] | str = ("ready",),
    journal_results: tuple[_JournalResult, ...] = (_JournalResult(True),),
) -> SimpleNamespace:
    return SimpleNamespace(
        stage=stage,
        approved=approved,
        reasons=reasons,
        execution_plan=object(),
        risk_decision=object(),
        journal_results=journal_results,
    )


def _summary_records() -> list[ForwardValidationRecord]:
    return [
        _record(stage="approved", approved=True),
        _record(stage="no_trade_filter", approved=False, reasons=("middle zone",)),
        _record(stage="execution_policy", approved=False, reasons=("spread high",)),
        _record(stage="risk_manager", approved=False, reasons=("cooldown active",)),
        _record(stage="signal_error", approved=False, reasons=("bad signal",), journal_success=False),
    ]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding=FORWARD_CSV_ENCODING, newline="") as file:
        return list(csv.DictReader(file))


def _csv_header(path: Path) -> list[str]:
    with path.open("r", encoding=FORWARD_CSV_ENCODING, newline="") as file:
        return next(csv.reader(file))


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
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "forward_validation.py"


if __name__ == "__main__":
    unittest.main()
