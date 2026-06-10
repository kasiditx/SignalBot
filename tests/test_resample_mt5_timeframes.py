from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.resample_mt5_timeframes_from_m5 import Candle, _read_m5_candles, _resample


class ResampleMt5TimeframesTest(unittest.TestCase):
    def test_resamples_complete_m5_groups_to_m15(self) -> None:
        candles = [
            Candle(_time("2026-06-01 08:00"), 100.0, 101.0, 99.5, 100.5, 10),
            Candle(_time("2026-06-01 08:05"), 100.5, 102.0, 100.0, 101.5, 20),
            Candle(_time("2026-06-01 08:10"), 101.5, 102.5, 101.0, 102.0, 30),
        ]

        resampled = _resample(candles, 15)

        self.assertEqual(len(resampled), 1)
        candle = resampled[0]
        self.assertEqual(candle.timestamp, _time("2026-06-01 08:00"))
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.high, 102.5)
        self.assertEqual(candle.low, 99.5)
        self.assertEqual(candle.close, 102.0)
        self.assertEqual(candle.volume, 60)

    def test_skips_incomplete_m5_groups(self) -> None:
        candles = [
            Candle(_time("2026-06-01 08:00"), 100.0, 101.0, 99.5, 100.5, 10),
            Candle(_time("2026-06-01 08:10"), 101.5, 102.5, 101.0, 102.0, 30),
        ]

        self.assertEqual(_resample(candles, 15), [])

    def test_retries_when_m5_csv_has_partial_row_during_write(self) -> None:
        expected = [Candle(_time("2026-06-01 08:00"), 100.0, 101.0, 99.5, 100.5, 10)]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mt5_ohlcv_M5.csv"
            path.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")

            with (
                patch(
                    "scripts.resample_mt5_timeframes_from_m5._read_candles_once",
                    side_effect=[ValueError("partial row"), expected],
                ),
                patch("scripts.resample_mt5_timeframes_from_m5.time.sleep"),
            ):
                self.assertEqual(_read_m5_candles(path), expected)


def _time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()
