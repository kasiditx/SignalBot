from __future__ import annotations

import unittest

from trading_signal_bot.market_structure import analyze_market_structure
from trading_signal_bot.models import Candle, SignalAction, TrendDirection


class MarketStructureTest(unittest.TestCase):
    def test_detects_uptrend_from_higher_highs_and_higher_lows(self) -> None:
        result = analyze_market_structure(_uptrend_candles(), lookback=1, min_swings=4)

        self.assertEqual(result.trend, TrendDirection.BULLISH)
        self.assertIn("HH/HL", result.structure_label)

    def test_detects_downtrend_from_lower_highs_and_lower_lows(self) -> None:
        result = analyze_market_structure(_downtrend_candles(), lookback=1, min_swings=4)

        self.assertEqual(result.trend, TrendDirection.BEARISH)
        self.assertIn("LH/LL", result.structure_label)

    def test_returns_sideways_when_swings_are_unclear(self) -> None:
        result = analyze_market_structure(_sideway_candles(), lookback=1, min_swings=4)

        self.assertEqual(result.trend, TrendDirection.SIDEWAYS)

    def test_short_candle_list_does_not_crash(self) -> None:
        result = analyze_market_structure(_candles_from_ohlc([(10, 11, 9, 10.5)]), lookback=1, min_swings=4)

        self.assertEqual(result.trend, TrendDirection.SIDEWAYS)
        self.assertEqual(result.swings, ())

    def test_returns_latest_swing_high_and_low(self) -> None:
        result = analyze_market_structure(_uptrend_candles(), lookback=1, min_swings=4)

        self.assertIsNotNone(result.latest_swing_high)
        self.assertIsNotNone(result.latest_swing_low)
        self.assertEqual(result.latest_swing_high.price, 14.0)
        self.assertEqual(result.latest_swing_low.price, 11.2)

    def test_close_above_latest_swing_high_flags_bullish_bos(self) -> None:
        candles = _uptrend_candles()
        candles.append(_candle(len(candles), 13.8, 15.3, 13.4, 14.8))

        result = analyze_market_structure(candles, lookback=1, min_swings=4)

        self.assertTrue(result.has_bos)
        self.assertEqual(result.bos_direction, SignalAction.BUY)

    def test_close_below_latest_swing_low_flags_bearish_bos(self) -> None:
        candles = _downtrend_candles()
        candles.append(_candle(len(candles), 10.2, 10.6, 8.4, 8.8))

        result = analyze_market_structure(candles, lookback=1, min_swings=4)

        self.assertTrue(result.has_bos)
        self.assertEqual(result.bos_direction, SignalAction.SELL)

    def test_bos_against_trend_flags_basic_choch(self) -> None:
        candles = _downtrend_candles()
        candles.append(_candle(len(candles), 11.0, 13.8, 10.8, 13.4))

        result = analyze_market_structure(candles, lookback=1, min_swings=4)

        self.assertTrue(result.has_choch)
        self.assertEqual(result.choch_direction, SignalAction.BUY)


def _uptrend_candles() -> list[Candle]:
    return _candles_from_ohlc(
        [
            (10.0, 11.0, 9.0, 10.2),
            (10.2, 12.0, 9.8, 11.5),
            (11.5, 11.7, 9.5, 10.2),
            (10.2, 13.0, 10.0, 12.4),
            (12.4, 12.6, 10.5, 11.1),
            (11.1, 14.0, 12.5, 13.4),
            (13.4, 13.6, 11.2, 12.2),
            (12.2, 13.2, 11.8, 12.8),
        ]
    )


def _downtrend_candles() -> list[Candle]:
    return _candles_from_ohlc(
        [
            (14.0, 14.2, 13.0, 13.6),
            (13.6, 15.0, 13.2, 14.4),
            (13.4, 13.6, 12.5, 12.8),
            (12.8, 14.0, 12.7, 13.6),
            (12.4, 12.6, 11.5, 11.9),
            (11.9, 13.0, 11.8, 12.5),
            (11.8, 12.4, 10.5, 11.0),
            (11.0, 12.0, 10.8, 11.4),
        ]
    )


def _sideway_candles() -> list[Candle]:
    return _candles_from_ohlc(
        [
            (10.0, 11.0, 9.0, 10.5),
            (10.5, 12.0, 9.5, 11.0),
            (11.0, 11.2, 9.0, 9.8),
            (9.8, 11.8, 9.4, 11.2),
            (11.2, 11.5, 8.8, 9.6),
            (9.6, 11.4, 9.3, 10.8),
            (10.8, 11.0, 9.2, 10.0),
            (10.0, 11.0, 9.5, 10.4),
        ]
    )


def _candles_from_ohlc(values: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [_candle(index, open_, high, low, close) for index, (open_, high, low, close) in enumerate(values)]


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=f"2026-05-18 00:{index:02d}",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000 + index,
    )


if __name__ == "__main__":
    unittest.main()
