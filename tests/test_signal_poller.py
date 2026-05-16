from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_signal_bot.signal_poller import _is_expected_market_closed_stale


class SignalPollerTest(unittest.TestCase):
    def test_treats_recent_friday_close_as_expected_weekend_stale(self) -> None:
        latest = datetime(2026, 5, 15, 21, 55, tzinfo=UTC)
        now = datetime(2026, 5, 16, 9, 1, tzinfo=UTC)

        self.assertTrue(_is_expected_market_closed_stale(latest, now, max_stale_hours=72))

    def test_rejects_very_old_friday_candle_as_unexpected_stale(self) -> None:
        latest = datetime(2026, 3, 20, 3, 40, tzinfo=UTC)
        now = datetime(2026, 5, 16, 9, 1, tzinfo=UTC)

        self.assertFalse(_is_expected_market_closed_stale(latest, now, max_stale_hours=72))


if __name__ == "__main__":
    unittest.main()
