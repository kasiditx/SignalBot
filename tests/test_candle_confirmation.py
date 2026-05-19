from __future__ import annotations

import unittest

from trading_signal_bot.candle_confirmation import analyze_candle_confirmation, is_body_breakout, is_fakeout
from trading_signal_bot.models import Candle, SignalAction


class CandleConfirmationTest(unittest.TestCase):
    def test_detects_bullish_engulfing(self) -> None:
        result = analyze_candle_confirmation(
            [
                _candle(0, 101.0, 101.2, 99.8, 100.0),
                _candle(1, 99.8, 102.0, 99.5, 101.5),
            ]
        )

        self.assertTrue(result.bullish_engulfing)
        self.assertEqual(result.direction, SignalAction.BUY)

    def test_detects_bearish_engulfing(self) -> None:
        result = analyze_candle_confirmation(
            [
                _candle(0, 100.0, 101.4, 99.8, 101.0),
                _candle(1, 101.2, 101.5, 99.0, 99.5),
            ]
        )

        self.assertTrue(result.bearish_engulfing)
        self.assertEqual(result.direction, SignalAction.SELL)

    def test_detects_pin_bar(self) -> None:
        result = analyze_candle_confirmation([_candle(0, 100.0, 100.3, 97.0, 100.1)], atr_value=2.0)

        self.assertTrue(result.pin_bar)
        self.assertEqual(result.direction, SignalAction.BUY)

    def test_detects_rejection_wick(self) -> None:
        result = analyze_candle_confirmation([_candle(0, 100.0, 103.0, 99.8, 100.2)])

        self.assertTrue(result.rejection_wick)
        self.assertEqual(result.direction, SignalAction.SELL)

    def test_detects_strong_close(self) -> None:
        result = analyze_candle_confirmation([_candle(0, 100.0, 102.0, 99.0, 101.9)])

        self.assertTrue(result.strong_close)
        self.assertEqual(result.direction, SignalAction.BUY)

    def test_body_breakout_uses_body_not_wick(self) -> None:
        wick_only = _candle(0, 99.5, 101.5, 99.0, 100.2)
        body_close = _candle(1, 100.6, 102.0, 100.4, 101.8)

        self.assertFalse(is_body_breakout(wick_only, 100.5, SignalAction.BUY))
        self.assertTrue(is_body_breakout(body_close, 100.5, SignalAction.BUY))

    def test_detects_fakeout_when_wick_breaks_and_close_returns_inside(self) -> None:
        candle = _candle(0, 100.0, 102.0, 99.5, 100.8)

        self.assertTrue(is_fakeout(candle, support=None, resistance=101.5))
        self.assertTrue(analyze_candle_confirmation([candle], resistance=101.5).fakeout)

    def test_empty_candles_do_not_crash(self) -> None:
        result = analyze_candle_confirmation([])

        self.assertIsNone(result.direction)
        self.assertEqual(result.summary, "No candle data")


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 02:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


if __name__ == "__main__":
    unittest.main()
