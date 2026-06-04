from __future__ import annotations

import ast
import csv
import json
import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.forward_validation import (
    DAILY_SUMMARY_FIELDNAMES,
    FORWARD_CSV_ENCODING,
    WEEKLY_SUMMARY_FIELDNAMES,
    ForwardValidationConfig,
    ForwardValidationRecord,
    forward_record_from_dict,
    load_forward_records_jsonl,
    write_forward_daily_summary_csv,
    write_forward_summaries,
    write_forward_weekly_summary_csv,
)


class ForwardValidationSummaryWriterTest(unittest.TestCase):
    def test_load_forward_records_jsonl_missing_file_returns_empty_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_forward_records_jsonl(Path(directory) / "missing.jsonl"), ())

    def test_load_forward_records_jsonl_skips_empty_lines_and_parses_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text(
                "\n" + json.dumps(_record_payload("2026-05-21T08:00:00+00:00")) + "\n\n",
                encoding="utf-8",
            )

            records = load_forward_records_jsonl(path)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].symbol, "XAUUSD")

    def test_load_forward_records_jsonl_reads_thai_reason_with_utf8_sig(self) -> None:
        thai_reason = "ยังไม่มีสัญญาณเข้าเทรด"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text(
                json.dumps(
                    _record_payload("2026-05-21T08:00:00+00:00", reasons=[thai_reason]),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )

            records = load_forward_records_jsonl(path)

        self.assertEqual(records[0].reasons, (thai_reason,))

    def test_load_forward_records_jsonl_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text("{bad json}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid JSONL forward record"):
                load_forward_records_jsonl(path)

    def test_forward_record_from_dict_converts_reason_list_to_tuple(self) -> None:
        record = forward_record_from_dict(_record_payload("2026-05-21T08:00:00+00:00", reasons=["a", "b"]))

        self.assertEqual(record.reasons, ("a", "b"))

    def test_forward_record_from_dict_converts_reason_string_to_tuple(self) -> None:
        record = forward_record_from_dict(_record_payload("2026-05-21T08:00:00+00:00", reasons="single"))

        self.assertEqual(record.reasons, ("single",))

    def test_forward_record_from_dict_non_dict_metadata_fallbacks_to_empty_dict(self) -> None:
        payload = _record_payload("2026-05-21T08:00:00+00:00")
        payload["metadata"] = "not a dict"

        record = forward_record_from_dict(payload)

        self.assertEqual(record.metadata, {})

    def test_forward_record_from_dict_missing_timestamp_raises(self) -> None:
        payload = _record_payload("2026-05-21T08:00:00+00:00")
        payload.pop("timestamp")

        with self.assertRaisesRegex(ValueError, "timestamp is required"):
            forward_record_from_dict(payload)

    def test_forward_record_from_dict_invalid_timestamp_type_raises(self) -> None:
        payload = _record_payload("2026-05-21T08:00:00+00:00")
        payload["timestamp"] = 123

        with self.assertRaisesRegex(ValueError, "timestamp is required"):
            forward_record_from_dict(payload)

    def test_write_forward_daily_summary_csv_header_and_group_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "daily_summary.csv"

            write_forward_daily_summary_csv(tuple(_sample_records()), path)

            rows = _csv_rows(path)
            self.assertEqual(_csv_header(path), list(DAILY_SUMMARY_FIELDNAMES))
            self.assertEqual([row["date"] for row in rows], ["2026-05-21", "2026-05-22"])
            first = rows[0]
            self.assertEqual(first["total_runs"], "5")
            self.assertEqual(first["approved_count"], "1")
            self.assertEqual(first["rejected_count"], "4")
            self.assertEqual(first["no_trade_count"], "1")
            self.assertEqual(first["execution_reject_count"], "1")
            self.assertEqual(first["risk_reject_count"], "2")
            self.assertEqual(first["paper_intent_count"], "1")
            self.assertEqual(first["top_reason"], "cooldown active")
            self.assertEqual(json.loads(first["reason_summary"])["cooldown active"], 2)

    def test_write_forward_daily_summary_csv_empty_records_writes_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_summary.csv"

            write_forward_daily_summary_csv((), path)

            self.assertEqual(_csv_header(path), list(DAILY_SUMMARY_FIELDNAMES))
            self.assertEqual(_csv_rows(path), [])

    def test_write_forward_daily_summary_csv_counts_safety_violations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_summary.csv"

            write_forward_daily_summary_csv(
                (
                    _record(order_sent=True),
                    _record(order_intent_written=True),
                ),
                path,
            )

            row = _csv_rows(path)[0]
            self.assertEqual(row["order_sent_count"], "1")
            self.assertEqual(row["order_intent_written_count"], "1")

    def test_write_forward_daily_summary_csv_preserves_thai_reason_with_utf8_sig(self) -> None:
        thai_reason = "ยังไม่มีสัญญาณเข้าเทรด"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_summary.csv"

            write_forward_daily_summary_csv((_record(approved=False, reasons=(thai_reason,)),), path)

            content = path.read_text(encoding=FORWARD_CSV_ENCODING)

        self.assertIn(thai_reason, content)
        self.assertNotIn("à¸", content)

    def test_write_forward_weekly_summary_csv_header_and_iso_week_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "weekly_summary.csv"

            write_forward_weekly_summary_csv(tuple(_sample_records()), path)

            rows = _csv_rows(path)
            self.assertEqual(_csv_header(path), list(WEEKLY_SUMMARY_FIELDNAMES))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["week_start"], "2026-05-18")
            self.assertEqual(row["week_end"], "2026-05-24")
            self.assertEqual(row["iso_year"], "2026")
            self.assertEqual(row["iso_week"], "21")
            self.assertEqual(json.loads(row["reason_summary"])["cooldown active"], 2)

    def test_write_forward_weekly_summary_csv_empty_records_writes_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weekly_summary.csv"

            write_forward_weekly_summary_csv((), path)

            self.assertEqual(_csv_header(path), list(WEEKLY_SUMMARY_FIELDNAMES))
            self.assertEqual(_csv_rows(path), [])

    def test_write_forward_weekly_summary_csv_preserves_thai_reason_with_utf8_sig(self) -> None:
        thai_reason = "ยังไม่มีสัญญาณเข้าเทรด"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weekly_summary.csv"

            write_forward_weekly_summary_csv((_record(approved=False, reasons=(thai_reason,)),), path)

            content = path.read_text(encoding=FORWARD_CSV_ENCODING)

        self.assertIn(thai_reason, content)
        self.assertNotIn("à¸", content)

    def test_write_forward_summaries_writes_daily_and_weekly_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ForwardValidationConfig(
                record_csv_path=Path(directory) / "records.csv",
                record_jsonl_path=Path(directory) / "records.jsonl",
                daily_summary_path=Path(directory) / "daily_summary.csv",
                weekly_summary_path=Path(directory) / "weekly_summary.csv",
            )

            write_forward_summaries(tuple(_sample_records()), config)

            self.assertTrue(config.daily_summary_path.exists())
            self.assertTrue(config.weekly_summary_path.exists())

    def test_source_has_no_forbidden_auto_trade_terms(self) -> None:
        source = _source_text()
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)
        self.assertNotIn("trading_signal_order", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_forward_daily_summary_csv(tuple(_sample_records()), Path(directory) / "daily_summary.csv")

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_forward_weekly_summary_csv(tuple(_sample_records()), Path(directory) / "weekly_summary.csv")

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())


def _sample_records() -> list[ForwardValidationRecord]:
    return [
        _record(stage="approved", approved=True, timestamp="2026-05-21T08:00:00+00:00"),
        _record(stage="no_trade_filter", approved=False, reasons=("middle zone",), timestamp="2026-05-21T09:00:00+00:00"),
        _record(stage="execution_policy", approved=False, reasons=("spread high",), timestamp="2026-05-21T10:00:00+00:00"),
        _record(stage="risk_manager", approved=False, reasons=("cooldown active",), timestamp="2026-05-21T11:00:00+00:00"),
        _record(stage="risk_manager", approved=False, reasons=("cooldown active",), timestamp="2026-05-21T12:00:00+00:00"),
        _record(stage="approved", approved=True, timestamp="2026-05-22T08:00:00+00:00"),
    ]


def _record(
    *,
    timestamp: str = "2026-05-21T08:00:00+00:00",
    stage: str = "approved",
    approved: bool = True,
    reasons: tuple[str, ...] = (),
    order_sent: bool = False,
    order_intent_written: bool = False,
) -> ForwardValidationRecord:
    return ForwardValidationRecord(
        timestamp=timestamp,
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
        order_sent=order_sent,
        order_intent_written=order_intent_written,
        journal_success=True,
        metadata={"source": "unit-test"},
    )


def _record_payload(timestamp: str, reasons: object = ("ready",)) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "symbol": "XAUUSD",
        "mode": "paper",
        "action": "BUY",
        "stage": "approved",
        "approved": True,
        "reasons": reasons,
        "entry": 100.0,
        "stop_loss": 99.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "risk_reward": 1.5,
        "execution_plan_present": True,
        "risk_decision_present": True,
        "order_sent": False,
        "order_intent_written": False,
        "journal_success": True,
        "metadata": {"source": "jsonl"},
    }


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
