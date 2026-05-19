from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.journal import (
    EVENT_NO_TRADE,
    EVENT_SIGNAL_GENERATED,
    JournalWriterConfig,
    append_csv_event,
    append_jsonl_event,
    create_journal_event,
    journal_event_to_dict,
    validate_event_type,
    write_journal_event,
)


class JournalTest(unittest.TestCase):
    def test_create_minimal_event(self) -> None:
        event = create_journal_event(EVENT_SIGNAL_GENERATED, timestamp="2026-05-18T09:00:00Z")

        self.assertEqual(event.event_type, EVENT_SIGNAL_GENERATED)
        self.assertEqual(event.timestamp, "2026-05-18T09:00:00Z")

    def test_create_full_event(self) -> None:
        event = _event()

        self.assertEqual(event.symbol, "XAUUSD")
        self.assertEqual(event.entry, 100.0)
        self.assertEqual(event.reasons, ("MID_ZONE", "RR below minimum"))
        self.assertEqual(event.metadata["session"], "London")

    def test_timestamp_auto_generates_when_missing(self) -> None:
        event = create_journal_event(EVENT_SIGNAL_GENERATED)

        self.assertTrue(event.timestamp)
        self.assertIn("+00:00", event.timestamp)

    def test_default_reasons_is_empty_tuple(self) -> None:
        event = create_journal_event(EVENT_SIGNAL_GENERATED, timestamp="2026-05-18T09:00:00Z")

        self.assertEqual(event.reasons, ())

    def test_default_metadata_is_empty_dict(self) -> None:
        event = create_journal_event(EVENT_SIGNAL_GENERATED, timestamp="2026-05-18T09:00:00Z")

        self.assertEqual(event.metadata, {})

    def test_validate_event_type_accepts_valid_type(self) -> None:
        validate_event_type(EVENT_NO_TRADE)

    def test_validate_event_type_rejects_invalid_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid journal event type"):
            validate_event_type("BAD_EVENT")

    def test_journal_event_to_dict_returns_all_fields(self) -> None:
        event_dict = journal_event_to_dict(_event())

        expected_keys = {
            "timestamp",
            "event_type",
            "symbol",
            "timeframe",
            "action",
            "mode",
            "htf_bias",
            "execution_trend",
            "structure_label",
            "price_location",
            "candle_confirmation_summary",
            "entry",
            "stop_loss",
            "tp1",
            "tp2",
            "risk_reward",
            "volume",
            "approved",
            "reasons",
            "error_message",
            "trade_result",
            "pnl",
            "metadata",
        }
        self.assertEqual(set(event_dict), expected_keys)

    def test_reasons_are_list_in_dict_output(self) -> None:
        event_dict = journal_event_to_dict(_event())

        self.assertEqual(event_dict["reasons"], ["MID_ZONE", "RR below minimum"])

    def test_metadata_is_preserved_in_dict_output(self) -> None:
        event_dict = journal_event_to_dict(_event())

        self.assertEqual(event_dict["metadata"], {"session": "London", "spread_points": 250})

    def test_append_csv_event_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            append_csv_event(_event(), path)

            self.assertTrue(path.exists())
            self.assertEqual(len(_csv_rows(path)), 1)

    def test_csv_has_header_when_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            append_csv_event(_event(), path)

            header = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(header.startswith("timestamp,event_type,symbol"))

    def test_appending_second_csv_event_does_not_duplicate_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            append_csv_event(_event(), path)
            append_csv_event(_event(symbol="EURUSD"), path)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0].count("timestamp"), 1)
            self.assertNotIn("timestamp,event_type", lines[1])

    def test_csv_reasons_are_readable_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            append_csv_event(_event(), path)

            self.assertEqual(_csv_rows(path)[0]["reasons"], "MID_ZONE | RR below minimum")

    def test_csv_metadata_is_json_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            append_csv_event(_event(), path)

            metadata = json.loads(_csv_rows(path)[0]["metadata"])
            self.assertEqual(metadata["session"], "London")

    def test_append_jsonl_event_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"

            append_jsonl_event(_event(), path)

            self.assertTrue(path.exists())
            self.assertEqual(len(_jsonl_rows(path)), 1)

    def test_jsonl_appends_multiple_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"

            append_jsonl_event(_event(), path)
            append_jsonl_event(_event(symbol="EURUSD"), path)

            rows = _jsonl_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["symbol"], "EURUSD")

    def test_jsonl_lines_parse_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"

            append_jsonl_event(_event(), path)

            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["event_type"], EVENT_NO_TRADE)

    def test_write_journal_event_csv_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"

            result = write_journal_event(
                _event(),
                JournalWriterConfig(csv_path=path, write_csv=True, write_jsonl=False),
            )

            self.assertTrue(result.success)
            self.assertTrue(result.csv_written)
            self.assertFalse(result.jsonl_written)
            self.assertTrue(path.exists())

    def test_write_journal_event_jsonl_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"

            result = write_journal_event(
                _event(),
                JournalWriterConfig(jsonl_path=path, write_csv=False, write_jsonl=True),
            )

            self.assertTrue(result.success)
            self.assertFalse(result.csv_written)
            self.assertTrue(result.jsonl_written)
            self.assertTrue(path.exists())

    def test_write_journal_event_csv_and_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "audit.csv"
            jsonl_path = Path(directory) / "audit.jsonl"

            result = write_journal_event(_event(), JournalWriterConfig(csv_path=csv_path, jsonl_path=jsonl_path))

            self.assertTrue(result.success)
            self.assertTrue(result.csv_written)
            self.assertTrue(result.jsonl_written)

    def test_write_journal_event_with_all_outputs_disabled_returns_error(self) -> None:
        result = write_journal_event(_event(), JournalWriterConfig(write_csv=False, write_jsonl=False))

        self.assertFalse(result.success)
        self.assertIn("At least one journal output", result.error_message)

    def test_write_journal_event_missing_csv_path_returns_error(self) -> None:
        result = write_journal_event(_event(), JournalWriterConfig(write_csv=True, write_jsonl=False))

        self.assertFalse(result.success)
        self.assertIn("CSV journal path is required", result.error_message)

    def test_write_journal_event_missing_jsonl_path_returns_error(self) -> None:
        result = write_journal_event(_event(), JournalWriterConfig(write_csv=False, write_jsonl=True))

        self.assertFalse(result.success)
        self.assertIn("JSONL journal path is required", result.error_message)

    def test_parent_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "audit.csv"

            append_csv_event(_event(), path)

            self.assertTrue(path.exists())

    def test_unserializable_metadata_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            event = create_journal_event(EVENT_NO_TRADE, metadata={"bad": object()})

            result = write_journal_event(
                event,
                JournalWriterConfig(jsonl_path=path, write_csv=False, write_jsonl=True),
            )

            self.assertFalse(result.success)
            self.assertIn("not JSON serializable", result.error_message)


def _event(symbol: str = "XAUUSD"):
    return create_journal_event(
        EVENT_NO_TRADE,
        timestamp="2026-05-18T09:00:00Z",
        symbol=symbol,
        timeframe="M1",
        action="BUY",
        mode="paper",
        htf_bias="BULLISH",
        execution_trend="BULLISH",
        structure_label="Uptrend: HH/HL confirmed",
        price_location="MID_ZONE",
        candle_confirmation_summary="No clear candle confirmation",
        entry=100.0,
        stop_loss=99.0,
        tp1=101.0,
        tp2=102.0,
        risk_reward=1.5,
        volume=0.1,
        approved=False,
        reasons=("MID_ZONE", "RR below minimum"),
        error_message=None,
        trade_result=None,
        pnl=None,
        metadata={"session": "London", "spread_points": 250},
    )


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _jsonl_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
