from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.data import load_candles_from_csv


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


if __name__ == "__main__":
    unittest.main()
