from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.demo_execution import DemoOrderCandidate
from trading_signal_bot.price_action_signal_engine import (
    PACandle,
    PASignalCandidate,
    _directional_candidate,
    fetch_pa_candles_from_mt5,
    price_action_candidate_to_demo_order_candidate,
    run_price_action_backtest,
    write_price_action_backtest_csv,
    write_price_action_backtest_html,
    write_price_action_backtest_jsonl,
    build_price_action_signal_candidate,
    build_topdown_price_action_signal,
)


class PriceActionSignalEngineTest(unittest.TestCase):
    def test_buy_candidate_from_breakout_and_bullish_close(self) -> None:
        candidate = build_price_action_signal_candidate("XAUUSD", "M5", _buy_breakout_candles())

        self.assertEqual(candidate.action, "BUY")
        self.assertEqual(candidate.entry, 106.0)
        self.assertLess(candidate.stop_loss, candidate.entry)
        self.assertGreater(candidate.take_profit, candidate.entry)
        self.assertGreaterEqual(candidate.risk_reward, 1.5)
        self.assertIn("bullish breakout close", candidate.reasons)

    def test_sell_candidate_from_breakdown_and_bearish_close(self) -> None:
        candidate = build_price_action_signal_candidate("XAUUSD", "M5", _sell_breakdown_candles())

        self.assertEqual(candidate.action, "SELL")
        self.assertEqual(candidate.entry, 94.0)
        self.assertGreater(candidate.stop_loss, candidate.entry)
        self.assertLess(candidate.take_profit, candidate.entry)
        self.assertGreaterEqual(candidate.risk_reward, 1.5)
        self.assertIn("bearish breakdown close", candidate.reasons)

    def test_sideway_market_returns_wait(self) -> None:
        candidate = build_price_action_signal_candidate("XAUUSD", "M5", _sideway_candles())

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("no breakout or breakdown confirmation", candidate.reasons)

    def test_low_risk_reward_returns_wait(self) -> None:
        candles = _buy_breakout_candles()

        candidate = build_price_action_signal_candidate("XAUUSD", "M5", candles, min_risk_reward=100.0)

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("risk reward below minimum", candidate.reasons)

    def test_no_candle_confirmation_returns_wait(self) -> None:
        candles = _buy_breakout_candles(confirm_close=105.2, confirm_open=105.1, confirm_high=106.0)

        candidate = build_price_action_signal_candidate("XAUUSD", "M5", candles)

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("no candle confirmation", candidate.reasons)

    def test_buy_sl_tp_direction_is_valid(self) -> None:
        candidate = build_price_action_signal_candidate("XAUUSD", "M5", _buy_breakout_candles())

        self.assertLess(candidate.stop_loss, candidate.entry)
        self.assertGreater(candidate.take_profit, candidate.entry)

    def test_sell_sl_tp_direction_is_valid(self) -> None:
        candidate = build_price_action_signal_candidate("XAUUSD", "M5", _sell_breakdown_candles())

        self.assertGreater(candidate.stop_loss, candidate.entry)
        self.assertLess(candidate.take_profit, candidate.entry)

    def test_topdown_waits_when_execution_buy_conflicts_with_higher_timeframe_sell(self) -> None:
        candidate = build_topdown_price_action_signal(
            "XAUUSD",
            {
                "H1": _sell_breakdown_candles(),
                "M5": _buy_breakout_candles(),
            },
        )

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("higher timeframe conflict", candidate.reasons)

    def test_strict_without_breakout_returns_wait(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bullish_pullback_rejection_candles(),
            signal_mode="STRICT",
        )

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("no breakout confirmation", candidate.reasons)

    def test_normal_pullback_rejection_returns_buy(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bullish_pullback_rejection_candles(),
            signal_mode="NORMAL",
        )

        self.assertEqual(candidate.action, "BUY")
        self.assertIn("bullish pullback rejection", candidate.reasons)
        self.assertLess(candidate.stop_loss, candidate.entry)

    def test_normal_bearish_rejection_returns_sell(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bearish_pullback_rejection_candles(),
            signal_mode="NORMAL",
        )

        self.assertEqual(candidate.action, "SELL")
        self.assertIn("bearish pullback rejection", candidate.reasons)
        self.assertGreater(candidate.stop_loss, candidate.entry)

    def test_normal_bullish_engulfing_returns_buy(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bullish_engulfing_candles(),
            signal_mode="NORMAL",
        )

        self.assertEqual(candidate.action, "BUY")
        self.assertIn("bullish engulfing confirmation", candidate.reasons)

    def test_normal_bearish_engulfing_returns_sell(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bearish_engulfing_candles(),
            signal_mode="NORMAL",
        )

        self.assertEqual(candidate.action, "SELL")
        self.assertIn("bearish engulfing confirmation", candidate.reasons)

    def test_aggressive_rejection_gets_lower_confidence_than_normal(self) -> None:
        normal = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bullish_pullback_rejection_candles(),
            signal_mode="NORMAL",
        )
        aggressive = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _aggressive_bullish_rejection_candles(),
            signal_mode="AGGRESSIVE",
        )

        self.assertEqual(aggressive.action, "BUY")
        self.assertLess(aggressive.confidence_score, normal.confidence_score)
        self.assertIn("aggressive bullish rejection", aggressive.reasons)

    def test_normal_rr_low_returns_wait_with_detailed_reason(self) -> None:
        candidate = build_price_action_signal_candidate(
            "XAUUSD",
            "M5",
            _bullish_pullback_rejection_candles(),
            min_risk_reward=100.0,
            signal_mode="NORMAL",
        )

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("rr below minimum", candidate.reasons)

    def test_no_valid_sl_tp_returns_wait(self) -> None:
        candidate = _directional_candidate(
            "XAUUSD",
            "M5",
            _zero_risk_rejection_candles(),
            "BUY",
            100.0,
            100.0,
            1.5,
            ("unit-test",),
            confidence_score=0.5,
        )

        self.assertEqual(candidate.action, "WAIT")
        self.assertIn("no valid stop loss or take profit", candidate.reasons)

    def test_backtest_records_win(self) -> None:
        result = run_price_action_backtest("XAUUSD", "M5", _buy_breakout_candles() + (_candle(9, 106, 112, 105.5, 111),))

        self.assertEqual(result.total_trades, 1)
        self.assertEqual(result.wins, 1)
        self.assertEqual(result.losses, 0)
        self.assertGreater(result.net_r, 0)

    def test_backtest_records_loss(self) -> None:
        result = run_price_action_backtest("XAUUSD", "M5", _buy_breakout_candles() + (_candle(9, 106, 106.2, 102.0, 103),))

        self.assertEqual(result.total_trades, 1)
        self.assertEqual(result.wins, 0)
        self.assertEqual(result.losses, 1)
        self.assertLess(result.net_r, 0)

    def test_backtest_uses_only_prior_candles_for_candidate(self) -> None:
        candles = _sideway_candles() + (_candle(9, 100, 100.5, 99.5, 100.0),)

        result = run_price_action_backtest("XAUUSD", "M5", candles)

        self.assertEqual(result.total_trades, 0)

    def test_writers_create_csv_jsonl_and_html(self) -> None:
        result = run_price_action_backtest("XAUUSD", "M5", _buy_breakout_candles() + (_candle(9, 106, 112, 105.5, 111),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            csv_path = output / "backtest_trades.csv"
            jsonl_path = output / "backtest_trades.jsonl"
            html_path = output / "backtest_report.html"

            write_price_action_backtest_csv(result, csv_path)
            write_price_action_backtest_jsonl(result, jsonl_path)
            write_price_action_backtest_html(result, html_path)

            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["action"], "BUY")
            self.assertEqual(json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])["outcome"], "win")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Price Action Backtest", html)
            self.assertIn("win", html)

    def test_fake_mt5_fetch_uses_copy_rates_from_pos(self) -> None:
        mt5 = _FakeMt5Rates()

        candles = fetch_pa_candles_from_mt5(mt5, "XAUUSD", "M5", 2)

        self.assertEqual(mt5.calls, [("XAUUSD", "TIMEFRAME_M5", 0, 2)])
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].close, 101.0)

    def test_candidate_can_convert_to_demo_order_candidate(self) -> None:
        pa_candidate = build_price_action_signal_candidate("XAUUSD", "M5", _buy_breakout_candles())

        demo_candidate = price_action_candidate_to_demo_order_candidate(pa_candidate, volume=0.01)

        self.assertIsInstance(demo_candidate, DemoOrderCandidate)
        self.assertEqual(demo_candidate.action, "BUY")
        self.assertEqual(demo_candidate.entry, pa_candidate.entry)
        self.assertEqual(demo_candidate.metadata["latest_execution_candle_time"], pa_candidate.latest_execution_candle_time)

    def test_wait_candidate_does_not_convert_to_demo_order_candidate(self) -> None:
        pa_candidate = build_price_action_signal_candidate("XAUUSD", "M5", _sideway_candles())

        with self.assertRaisesRegex(ValueError, "WAIT candidate cannot be converted"):
            price_action_candidate_to_demo_order_candidate(pa_candidate, volume=0.01)

    def test_source_has_no_order_sender_or_order_intent_file(self) -> None:
        source = _source_path().read_text(encoding="utf-8")

        self.assertNotIn("order" + "_send", source)
        self.assertNotIn("trading_signal_order", source)

    def test_order_intent_files_not_created(self) -> None:
        self.assertFalse(Path("trading_signal_order.csv").exists())
        self.assertFalse((Path("logs") / "trading_signal_order.csv").exists())


class _FakeMt5Rates:
    TIMEFRAME_M5 = "TIMEFRAME_M5"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, int, int]] = []

    def copy_rates_from_pos(self, symbol: str, timeframe: object, start_pos: int, count: int) -> list[dict[str, object]]:
        self.calls.append((symbol, timeframe, start_pos, count))
        return [
            {"time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "tick_volume": 10},
            {"time": 2, "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "tick_volume": 11},
        ]


def _candle(index: int, open_: float, high: float, low: float, close: float) -> PACandle:
    return PACandle(
        time=f"2026-01-01T00:{index:02d}:00",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
    )


def _buy_breakout_candles(
    *,
    confirm_open: float = 103.0,
    confirm_high: float = 107.0,
    confirm_close: float = 106.0,
) -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 102, 99, 101),
        _candle(2, 101, 104, 100, 103),
        _candle(3, 103, 105, 101, 102),
        _candle(4, 102, 103, 98, 99),
        _candle(5, 99, 101, 97, 100),
        _candle(6, 100, 103, 99, 102),
        _candle(7, 102, 104, 101, 103),
        _candle(8, confirm_open, confirm_high, 102.5, confirm_close),
    )


def _sell_breakdown_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 105, 106, 103, 104),
        _candle(2, 104, 105, 101, 102),
        _candle(3, 102, 104, 99, 103),
        _candle(4, 103, 107, 102, 106),
        _candle(5, 106, 108, 104, 105),
        _candle(6, 105, 106, 101, 102),
        _candle(7, 102, 103, 97, 98),
        _candle(8, 98, 99, 93, 94),
    )


def _sideway_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 101, 99, 100.5),
        _candle(2, 100.5, 101.2, 99.3, 100.0),
        _candle(3, 100, 101, 99.2, 100.4),
        _candle(4, 100.4, 101.1, 99.4, 100.1),
        _candle(5, 100.1, 101.0, 99.5, 100.3),
        _candle(6, 100.3, 101.3, 99.6, 100.2),
        _candle(7, 100.2, 101.1, 99.7, 100.5),
        _candle(8, 100.5, 101.2, 99.8, 100.4),
    )


def _bullish_pullback_rejection_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 102, 99, 101),
        _candle(2, 101, 104, 100, 103),
        _candle(3, 103, 105, 101, 104),
        _candle(4, 104, 106, 102, 105),
        _candle(5, 105, 107, 103, 106),
        _candle(6, 106, 108, 104, 107),
        _candle(7, 107, 109, 105, 108),
        _candle(8, 106.2, 108.5, 101.8, 108.0),
    )


def _bearish_pullback_rejection_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 110, 111, 108, 109),
        _candle(2, 109, 110, 106, 107),
        _candle(3, 107, 108, 104, 105),
        _candle(4, 105, 106, 102, 103),
        _candle(5, 103, 104, 100, 101),
        _candle(6, 101, 102, 98, 99),
        _candle(7, 99, 100, 96, 97),
        _candle(8, 98.8, 104.2, 96.5, 97.4),
    )


def _bullish_engulfing_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 102, 99, 101),
        _candle(2, 101, 104, 100, 103),
        _candle(3, 103, 105, 101, 104),
        _candle(4, 104, 106, 102, 105),
        _candle(5, 105, 107, 103, 106),
        _candle(6, 106, 108, 104, 107),
        _candle(7, 107.5, 108, 105.5, 106.2),
        _candle(8, 105.8, 109.5, 105.2, 108.8),
    )


def _bearish_engulfing_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 110, 111, 108, 109),
        _candle(2, 109, 110, 106, 107),
        _candle(3, 107, 108, 104, 105),
        _candle(4, 105, 106, 102, 103),
        _candle(5, 103, 104, 100, 101),
        _candle(6, 101, 102, 98, 99),
        _candle(7, 98.5, 100.2, 98, 99.8),
        _candle(8, 100.1, 100.5, 95.8, 96.8),
    )


def _aggressive_bullish_rejection_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 102, 99, 101),
        _candle(2, 101, 104, 100, 103),
        _candle(3, 103, 105, 101, 104),
        _candle(4, 104, 106, 102, 105),
        _candle(5, 105, 107, 103, 106),
        _candle(6, 106, 108, 104, 107),
        _candle(7, 107, 109, 105, 108),
        _candle(8, 106.8, 107.6, 101.5, 107.2),
    )


def _zero_risk_rejection_candles() -> tuple[PACandle, ...]:
    return (
        _candle(1, 100, 102, 99, 101),
        _candle(2, 101, 104, 100, 103),
        _candle(3, 103, 105, 101, 104),
        _candle(4, 104, 106, 102, 105),
        _candle(5, 105, 107, 103, 106),
        _candle(6, 106, 108, 104, 107),
        _candle(7, 107, 109, 105, 108),
        _candle(8, 101.5, 103.0, 101.5, 101.5),
    )


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "price_action_signal_engine.py"


if __name__ == "__main__":
    unittest.main()
