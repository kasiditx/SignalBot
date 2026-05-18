from __future__ import annotations

import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path

from trading_signal_bot.data import _file_changed_while_reading, _file_was_recently_modified, load_candles_from_csv


class DataLoaderTest(unittest.TestCase):
    def test_reports_incomplete_csv_rows_without_type_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "candles.csv"
            csv_path.write_text(
                "timestamp,open,high,low,close,volume\n"
                "2026.05.16 00:10,4554.76,4558.07,4554.54,4555.66\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "CSV read failed after retries"):
                load_candles_from_csv(str(csv_path))

    def test_detects_csv_that_changed_during_read(self) -> None:
        before = SimpleNamespace(st_size=100, st_mtime_ns=10)
        after = SimpleNamespace(st_size=120, st_mtime_ns=11)

        self.assertTrue(_file_changed_while_reading(before, after))

    def test_detects_recently_modified_csv_as_transient(self) -> None:
        recent = SimpleNamespace(st_mtime=time.time())

        self.assertTrue(_file_was_recently_modified(recent))


if __name__ == "__main__":
    unittest.main()
