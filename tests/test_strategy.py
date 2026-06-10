from __future__ import annotations

import unittest

from trading_signal_bot.models import Candle, SignalAction, SignalConfig
from trading_signal_bot.strategy import generate_signal


class StrategyTest(unittest.TestCase):
    def test_generates_buy_signal_for_orderly_uptrend(self) -> None:
        candles = _breakout_candles(direction="buy")

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.setup_type, "Bullish body-close breakout")
        self.assertIsNotNone(signal.levels.stop_loss)
        self.assertIsNotNone(signal.levels.take_profit)
        self.assertLess(signal.levels.stop_loss, signal.levels.entry)
        self.assertGreater(signal.levels.take_profit, signal.levels.entry)
        self.assertLessEqual(signal.levels.risk_reward, 1.5)

    def test_generates_sell_signal_for_orderly_downtrend(self) -> None:
        candles = _breakout_candles(direction="sell")

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.setup_type, "Bearish body-close breakdown")
        self.assertGreater(signal.levels.stop_loss, signal.levels.entry)
        self.assertLess(signal.levels.take_profit, signal.levels.entry)

    def test_waits_on_wick_sweep_instead_of_treating_it_as_breakout(self) -> None:
        candles = _range_candles(count=60)
        previous_resistance = max(candle.high for candle in candles[-20:])
        candles.append(
            Candle(
                timestamp="2026-05-03 12:00",
                open=101.0,
                high=previous_resistance + 1.5,
                low=100.8,
                close=previous_resistance - 0.2,
                volume=2000,
            )
        )

        signal = generate_signal(candles, _config())

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "Liquidity sweep above resistance")

    def test_rejects_insufficient_candles(self) -> None:
        with self.assertRaises(ValueError):
            generate_signal(_candles(start=100.0, step=0.1, count=5), _config())

    def test_asian_breakout_generates_buy_after_london_open(self) -> None:
        candles = _asian_breakout_candles("buy")

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.strategy_name, "Asian Range Breakout XAUUSD")
        self.assertEqual(signal.setup_type, "Asian range bullish breakout")
        self.assertEqual(signal.levels.risk_reward, 2.0)
        self.assertLess(signal.levels.stop_loss, signal.levels.entry)
        self.assertGreater(signal.levels.take_profit, signal.levels.entry)

    def test_asian_breakout_generates_sell_after_london_open(self) -> None:
        candles = _asian_breakout_candles("sell")

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.strategy_name, "Asian Range Breakout XAUUSD")
        self.assertEqual(signal.setup_type, "Asian range bearish breakout")
        self.assertGreater(signal.levels.stop_loss, signal.levels.entry)
        self.assertLess(signal.levels.take_profit, signal.levels.entry)

    def test_asian_breakout_waits_before_london_window(self) -> None:
        candles = _asian_range_only_candles()

        signal = generate_signal(candles, _config(trade_mode="asian_breakout", timeframe="M5"))

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "Building Asian range")

    def test_h4_breakout_retest_generates_buy_after_retest_confirmation(self) -> None:
        candles = _h4_retest_candles("buy")

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5"),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.BUY)
        self.assertEqual(signal.strategy_name, "H4 Zone Breakout Retest XAUUSD")
        self.assertEqual(signal.setup_type, "H4 bullish breakout retest")
        self.assertLess(signal.levels.stop_loss, signal.levels.entry)
        self.assertGreater(signal.levels.take_profit, signal.levels.entry)

    def test_h4_breakout_retest_generates_sell_after_retest_confirmation(self) -> None:
        candles = _h4_retest_candles("sell")

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5"),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.SELL)
        self.assertEqual(signal.setup_type, "H4 bearish breakout retest")
        self.assertGreater(signal.levels.stop_loss, signal.levels.entry)
        self.assertLess(signal.levels.take_profit, signal.levels.entry)

    def test_h4_breakout_retest_waits_when_price_chases_breakout_without_retest(self) -> None:
        candles = _h4_breakout_without_retest_candles()

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5"),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "Waiting for H4 breakout retest")

    def test_h4_breakout_retest_expires_when_retest_is_too_late(self) -> None:
        candles = _h4_retest_candles("buy", retest_timestamp="2026-05-01 09:10")

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5", h4_retest_max_wait_seconds=60),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "H4 retest signal expired")

    def test_h4_breakout_retest_requires_pivot_and_momentum_filter(self) -> None:
        candles = _h4_retest_candles("buy", low_volume=True)

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5"),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "H4 retest filter rejected")

    def test_h4_breakout_retest_rejects_buy_when_rsi_momentum_is_bearish(self) -> None:
        candles = _h4_retest_candles("buy", bearish_momentum=True)

        signal = generate_signal(
            candles,
            _config(trade_mode="h4_breakout_retest", timeframe="M5"),
            _h4_retest_timeframes(candles),
        )

        self.assertEqual(signal.action, SignalAction.WAIT)
        self.assertEqual(signal.setup_type, "H4 retest filter rejected")


def _candles(start: float, step: float, count: int) -> list[Candle]:
    candles: list[Candle] = []
    price = start
    for index in range(count):
        open_price = price
        direction = 1 if step >= 0 else -1
        pullback = direction * -0.18 if index % 5 == 0 else 0
        close = price + step + pullback
        high = max(open_price, close) + 0.25
        low = min(open_price, close) - 0.25
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1000 + index,
            )
        )
        price = close
    return candles


def _config(
    trade_mode: str = "high_winrate",
    timeframe: str = "H1",
    h4_retest_max_wait_seconds: int = 86400,
) -> SignalConfig:
    return SignalConfig(
        symbol="TEST",
        timeframe=timeframe,
        csv_path="",
        fast_ema_period=5,
        slow_ema_period=10,
        rsi_period=5,
        atr_period=5,
        atr_multiplier=1.5,
        body_break_atr_ratio=0.20,
        risk_reward=2.0,
        min_candles=30,
        max_candle_age_minutes=180,
        multi_timeframe_enabled=False,
        timeframe_paths={},
        dry_run=True,
        send_wait=False,
        trade_mode=trade_mode,
        h4_retest_max_wait_seconds=h4_retest_max_wait_seconds,
    )


def _h4_retest_timeframes(candles: list[Candle]) -> dict[str, list[Candle]]:
    return {
        "M5": candles,
        "H4": [
            Candle("2026-05-01 00:00", 100.0, 101.0, 99.0, 100.2, 5000),
            Candle("2026-05-01 04:00", 100.2, 100.8, 99.4, 100.0, 5001),
        ],
        "D1": [
            Candle("2026-04-30 00:00", 99.0, 102.0, 98.0, 103.0, 10000),
            Candle("2026-05-01 00:00", 100.0, 105.0, 98.5, 103.0, 11000),
        ],
    }


def _h4_retest_candles(
    direction: str,
    retest_timestamp: str = "2026-05-01 09:10",
    low_volume: bool = False,
    bearish_momentum: bool = False,
) -> list[Candle]:
    candles = _m5_flat_candles("2026-05-01", count=96, high=100.7, low=99.3, close=100.0, volume=1000)
    if direction == "buy":
        if bearish_momentum:
            candles.extend(
                [
                    Candle("2026-05-01 08:00", 100.4, 103.5, 100.3, 103.0, 3200),
                    Candle("2026-05-01 08:05", 103.0, 103.1, 101.4, 102.0, 3000),
                    Candle("2026-05-01 08:10", 102.0, 102.1, 101.2, 101.5, 2900),
                    Candle(retest_timestamp, 101.5, 102.1, 100.9, 101.9, 3600),
                ]
            )
            return candles
        candles.extend(
            [
                Candle("2026-05-01 08:00", 100.4, 102.2, 100.3, 101.8, 3200),
                Candle("2026-05-01 08:05", 101.8, 102.4, 101.4, 102.1, 3000),
                Candle("2026-05-01 08:10", 102.1, 102.6, 101.7, 102.3, 2900),
                Candle(retest_timestamp, 101.2, 102.1, 100.9, 101.9, 900 if low_volume else 3600),
            ]
        )
        return candles
    if direction == "sell":
        candles.extend(
            [
                Candle("2026-05-01 08:00", 99.6, 99.7, 97.8, 98.2, 3200),
                Candle("2026-05-01 08:05", 98.2, 98.6, 97.6, 97.9, 3000),
                Candle("2026-05-01 08:10", 97.9, 98.3, 97.4, 97.7, 2900),
                Candle(retest_timestamp, 98.8, 99.1, 97.9, 98.1, 900 if low_volume else 3600),
            ]
        )
        return candles
    raise ValueError("Unsupported direction")


def _h4_breakout_without_retest_candles() -> list[Candle]:
    candles = _m5_flat_candles("2026-05-01", count=96, high=100.7, low=99.3, close=100.0, volume=1000)
    candles.extend(
        [
            Candle("2026-05-01 08:00", 100.4, 102.2, 100.3, 101.8, 3200),
            Candle("2026-05-01 08:05", 101.8, 103.0, 101.7, 102.8, 3100),
            Candle("2026-05-01 08:10", 102.8, 103.4, 102.6, 103.2, 3000),
        ]
    )
    return candles


def _m5_flat_candles(date: str, count: int, high: float, low: float, close: float, volume: int) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        hour = index // 12
        minute = (index % 12) * 5
        open_price = close - 0.05 if index % 2 == 0 else close + 0.05
        close_price = close + 0.05 if index % 2 == 0 else close - 0.05
        candles.append(Candle(f"{date} {hour:02d}:{minute:02d}", open_price, high, low, close_price, volume + index))
    return candles


def _range_candles(count: int) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        open_price = 100.0 + (0.15 if index % 2 == 0 else -0.15)
        close = 100.1 if index % 2 == 0 else 99.9
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=101.0,
                low=99.0,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def _breakout_candles(direction: str) -> list[Candle]:
    if direction == "buy":
        candles = _range_candles(59)
        candles.append(
            Candle(
                timestamp="2026-05-03 11:00",
                open=100.4,
                high=102.2,
                low=100.0,
                close=101.8,
                volume=2500,
            )
        )
        return candles

    candles: list[Candle] = []
    for index in range(59):
        open_price = 140.0 + (0.15 if index % 2 == 0 else -0.15)
        close = 140.1 if index % 2 == 0 else 139.9
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {index:02d}:00",
                open=open_price,
                high=141.0,
                low=139.0,
                close=close,
                volume=1000 + index,
            )
        )
    candles.append(
        Candle(
            timestamp="2026-05-03 11:00",
            open=139.6,
            high=140.0,
            low=137.8,
            close=138.2,
            volume=2500,
        )
    )
    return candles


def _asian_range_only_candles() -> list[Candle]:
    candles: list[Candle] = []
    for index in range(90):
        hour = index // 12
        minute = (index % 12) * 5
        open_price = 100.0 + (0.08 if index % 2 == 0 else -0.08)
        close = 100.05 if index % 2 == 0 else 99.95
        candles.append(
            Candle(
                timestamp=f"2026-05-01 {hour:02d}:{minute:02d}",
                open=open_price,
                high=100.4,
                low=99.6,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def _asian_breakout_candles(direction: str) -> list[Candle]:
    candles = _asian_range_only_candles()
    candles.extend(
        [
            Candle(
                timestamp="2026-05-01 07:30",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2000,
            ),
            Candle(
                timestamp="2026-05-01 07:35",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2001,
            ),
            Candle(
                timestamp="2026-05-01 07:40",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2002,
            ),
            Candle(
                timestamp="2026-05-01 07:45",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2003,
            ),
            Candle(
                timestamp="2026-05-01 07:50",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2004,
            ),
            Candle(
                timestamp="2026-05-01 07:55",
                open=100.0,
                high=100.4,
                low=99.6,
                close=100.0,
                volume=2005,
            ),
            Candle(
                timestamp="2026-05-01 08:00",
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=2100,
            ),
        ]
    )

    if direction == "buy":
        candles.append(
            Candle(
                timestamp="2026-05-01 08:05",
                open=100.2,
                high=103.2,
                low=100.0,
                close=102.4,
                volume=3000,
            )
        )
        return candles

    candles.append(
        Candle(
            timestamp="2026-05-01 08:05",
            open=99.8,
            high=100.0,
            low=96.8,
            close=97.6,
            volume=3000,
        )
    )
    return candles


if __name__ == "__main__":
    unittest.main()
