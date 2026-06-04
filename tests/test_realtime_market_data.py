from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from trading_signal_bot.models import Candle
from trading_signal_bot.realtime_market_data import (
    RealtimeMarketTick,
    build_realtime_snapshot,
    calculate_current_price,
    calculate_spread_points,
    ensure_mt5_initialized,
    fetch_mt5_candles,
    fetch_mt5_tick,
    fetch_realtime_market_snapshot,
    import_mt5_module,
    mt5_rate_to_candle,
    mt5_timeframe_code,
    normalize_realtime_timeframe,
)


class RealtimeMarketDataTest(unittest.TestCase):
    def test_normalize_realtime_timeframe_supported_values(self) -> None:
        for timeframe in ("M1", "M5", "M15", "M30", "H1", "H4"):
            with self.subTest(timeframe=timeframe):
                self.assertEqual(normalize_realtime_timeframe(timeframe), timeframe)

    def test_normalize_realtime_timeframe_case_insensitive(self) -> None:
        self.assertEqual(normalize_realtime_timeframe("m1"), "M1")
        self.assertEqual(normalize_realtime_timeframe("h1"), "H1")

    def test_normalize_realtime_timeframe_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported realtime timeframe"):
            normalize_realtime_timeframe("D1")

    def test_mt5_timeframe_code_returns_normalized_string(self) -> None:
        self.assertEqual(mt5_timeframe_code("m5"), "M5")

    def test_mt5_timeframe_code_invalid_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported realtime timeframe"):
            mt5_timeframe_code("W1")

    def test_import_mt5_module_missing_package_raises_runtime_error(self) -> None:
        original_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "MetaTrader5":
                raise ImportError("missing")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, "MetaTrader5 package is not installed"):
                import_mt5_module()

    def test_mt5_timeframe_code_maps_fake_mt5_constants(self) -> None:
        fake_mt5 = _fake_mt5()

        self.assertEqual(mt5_timeframe_code("M1", fake_mt5), fake_mt5.TIMEFRAME_M1)
        self.assertEqual(mt5_timeframe_code("M5", fake_mt5), fake_mt5.TIMEFRAME_M5)
        self.assertEqual(mt5_timeframe_code("M15", fake_mt5), fake_mt5.TIMEFRAME_M15)
        self.assertEqual(mt5_timeframe_code("M30", fake_mt5), fake_mt5.TIMEFRAME_M30)
        self.assertEqual(mt5_timeframe_code("H1", fake_mt5), fake_mt5.TIMEFRAME_H1)
        self.assertEqual(mt5_timeframe_code("H4", fake_mt5), fake_mt5.TIMEFRAME_H4)

    def test_mt5_timeframe_code_missing_constant_raises(self) -> None:
        fake_mt5 = SimpleNamespace(TIMEFRAME_M5="M5")

        with self.assertRaisesRegex(ValueError, "missing timeframe constant"):
            mt5_timeframe_code("M1", fake_mt5)

    def test_ensure_mt5_initialized_success(self) -> None:
        self.assertEqual(ensure_mt5_initialized(_fake_mt5(initialize_result=True)), (True, None))

    def test_ensure_mt5_initialized_failure(self) -> None:
        initialized, message = ensure_mt5_initialized(_fake_mt5(initialize_result=False, last_error_value=None))

        self.assertFalse(initialized)
        self.assertIn("MT5 initialize failed", message or "")

    def test_ensure_mt5_initialized_failure_includes_last_error(self) -> None:
        initialized, message = ensure_mt5_initialized(_fake_mt5(initialize_result=False, last_error_value=(1, "bad terminal")))

        self.assertFalse(initialized)
        self.assertIn("MT5 initialize failed", message or "")
        self.assertIn("bad terminal", message or "")

    def test_mt5_rate_to_candle_maps_dict_rate(self) -> None:
        candle = mt5_rate_to_candle(_rate_dict())

        self.assertIsInstance(candle, Candle)
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.high, 101.0)
        self.assertEqual(candle.low, 99.0)
        self.assertEqual(candle.close, 100.5)

    def test_mt5_rate_to_candle_maps_object_attribute_rate(self) -> None:
        candle = mt5_rate_to_candle(SimpleNamespace(**_rate_dict()))

        self.assertEqual(candle.close, 100.5)
        self.assertEqual(candle.volume, 123.0)

    def test_mt5_rate_to_candle_epoch_seconds_becomes_iso_string(self) -> None:
        candle = mt5_rate_to_candle(_rate_dict(time=1_700_000_000))

        self.assertEqual(candle.timestamp, "2023-11-14T22:13:20+00:00")

    def test_mt5_rate_to_candle_uses_tick_volume_first(self) -> None:
        candle = mt5_rate_to_candle(_rate_dict(tick_volume=123, real_volume=456, volume=789))

        self.assertEqual(candle.volume, 123.0)

    def test_mt5_rate_to_candle_fallbacks_to_real_volume(self) -> None:
        rate = _rate_dict(real_volume=456, volume=789)
        rate.pop("tick_volume")

        candle = mt5_rate_to_candle(rate)

        self.assertEqual(candle.volume, 456.0)

    def test_mt5_rate_to_candle_fallbacks_to_volume(self) -> None:
        rate = _rate_dict(volume=789)
        rate.pop("tick_volume")

        candle = mt5_rate_to_candle(rate)

        self.assertEqual(candle.volume, 789.0)

    def test_mt5_rate_to_candle_missing_volume_uses_zero(self) -> None:
        rate = _rate_dict()
        rate.pop("tick_volume")

        candle = mt5_rate_to_candle(rate)

        self.assertEqual(candle.volume, 0.0)

    def test_mt5_rate_to_candle_missing_ohlc_raises(self) -> None:
        for field in ("open", "high", "low", "close"):
            with self.subTest(field=field):
                rate = _rate_dict()
                rate.pop(field)
                with self.assertRaisesRegex(ValueError, f"missing required field: {field}"):
                    mt5_rate_to_candle(rate)

    def test_fetch_mt5_candles_count_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "candle count must be greater than zero"):
            fetch_mt5_candles("XAUUSD", "M1", 0, _fake_mt5())

    def test_fetch_mt5_candles_calls_copy_rates_from_pos(self) -> None:
        fake_mt5 = _fake_mt5(rates=[_rate_dict()])

        fetch_mt5_candles("XAUUSD", "M1", 10, fake_mt5)

        self.assertEqual(fake_mt5.copy_rates_call, ("XAUUSD", fake_mt5.TIMEFRAME_M1, 0, 10))

    def test_fetch_mt5_candles_none_rates_returns_empty_list(self) -> None:
        self.assertEqual(fetch_mt5_candles("XAUUSD", "M1", 10, _fake_mt5(rates=None)), [])

    def test_fetch_mt5_candles_empty_rates_returns_empty_list(self) -> None:
        self.assertEqual(fetch_mt5_candles("XAUUSD", "M1", 10, _fake_mt5(rates=[])), [])

    def test_fetch_mt5_candles_valid_rates_map_to_candles(self) -> None:
        candles = fetch_mt5_candles("XAUUSD", "M1", 10, _fake_mt5(rates=[_rate_dict()]))

        self.assertEqual(len(candles), 1)
        self.assertIsInstance(candles[0], Candle)

    def test_fetch_mt5_candles_invalid_mapping_raises(self) -> None:
        bad_rate = _rate_dict()
        bad_rate.pop("open")

        with self.assertRaisesRegex(ValueError, "missing required field: open"):
            fetch_mt5_candles("XAUUSD", "M1", 10, _fake_mt5(rates=[bad_rate]))

    def test_fetch_mt5_tick_maps_bid_ask_last_and_point(self) -> None:
        tick = fetch_mt5_tick("XAUUSD", _fake_mt5())

        self.assertEqual(tick.bid, 100.0)
        self.assertEqual(tick.ask, 100.2)
        self.assertEqual(tick.last, 100.1)
        self.assertEqual(tick.point, 0.01)

    def test_fetch_mt5_tick_time_becomes_datetime(self) -> None:
        tick = fetch_mt5_tick("XAUUSD", _fake_mt5())

        self.assertEqual(tick.timestamp, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc))

    def test_fetch_mt5_tick_missing_tick_adds_error(self) -> None:
        tick = fetch_mt5_tick("XAUUSD", _fake_mt5(tick=None))

        self.assertIn("missing MT5 tick", tick.errors)

    def test_fetch_mt5_tick_missing_symbol_info_adds_error(self) -> None:
        tick = fetch_mt5_tick("XAUUSD", _fake_mt5(symbol_info=None))

        self.assertIn("missing MT5 symbol info", tick.errors)

    def test_fetch_mt5_tick_missing_or_invalid_point_adds_error(self) -> None:
        missing = fetch_mt5_tick("XAUUSD", _fake_mt5(symbol_info=SimpleNamespace(point=None)))
        invalid = fetch_mt5_tick("XAUUSD", _fake_mt5(symbol_info=SimpleNamespace(point=0.0)))

        self.assertIn("missing MT5 point", missing.errors)
        self.assertIn("missing MT5 point", invalid.errors)

    def test_fetch_mt5_tick_missing_tick_does_not_crash(self) -> None:
        tick = fetch_mt5_tick("XAUUSD", _fake_mt5(tick=None))

        self.assertIsInstance(tick, RealtimeMarketTick)

    def test_calculate_current_price_uses_last_first(self) -> None:
        self.assertEqual(calculate_current_price(100.0, 102.0, last=101.5), 101.5)

    def test_calculate_current_price_uses_midpoint_without_last(self) -> None:
        self.assertEqual(calculate_current_price(100.0, 102.0), 101.0)

    def test_calculate_current_price_missing_bid_or_ask_returns_none(self) -> None:
        self.assertIsNone(calculate_current_price(None, 102.0))
        self.assertIsNone(calculate_current_price(100.0, None))

    def test_calculate_current_price_non_positive_bid_or_ask_returns_none(self) -> None:
        self.assertIsNone(calculate_current_price(0.0, 102.0))
        self.assertIsNone(calculate_current_price(100.0, -1.0))

    def test_calculate_current_price_non_positive_last_fallbacks_to_midpoint(self) -> None:
        self.assertEqual(calculate_current_price(100.0, 102.0, last=0.0), 101.0)

    def test_calculate_spread_points_calculates_spread(self) -> None:
        self.assertEqual(calculate_spread_points(100.0, 100.2, 0.01), 20.000000000000284)

    def test_calculate_spread_points_missing_values_returns_none(self) -> None:
        self.assertIsNone(calculate_spread_points(None, 100.2, 0.01))
        self.assertIsNone(calculate_spread_points(100.0, None, 0.01))
        self.assertIsNone(calculate_spread_points(100.0, 100.2, None))

    def test_calculate_spread_points_non_positive_point_returns_none(self) -> None:
        self.assertIsNone(calculate_spread_points(100.0, 100.2, 0.0))

    def test_calculate_spread_points_ask_below_bid_returns_none(self) -> None:
        self.assertIsNone(calculate_spread_points(100.2, 100.0, 0.01))

    def test_build_realtime_snapshot_calculates_current_price(self) -> None:
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick())

        self.assertEqual(snapshot.current_price, 100.1)

    def test_build_realtime_snapshot_calculates_spread_points(self) -> None:
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick())

        self.assertEqual(snapshot.spread_points, 20.000000000000284)

    def test_build_realtime_snapshot_uses_tick_timestamp(self) -> None:
        timestamp = datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick(timestamp=timestamp))

        self.assertEqual(snapshot.timestamp, timestamp)

    def test_build_realtime_snapshot_missing_tick_timestamp_uses_current_datetime(self) -> None:
        before = datetime.now(timezone.utc)
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick(timestamp=None))
        after = datetime.now(timezone.utc)

        self.assertGreaterEqual(snapshot.timestamp, before)
        self.assertLessEqual(snapshot.timestamp, after)

    def test_build_realtime_snapshot_combines_errors(self) -> None:
        snapshot = build_realtime_snapshot(
            "XAUUSD",
            _candles_by_timeframe(),
            _tick(errors=("tick error",)),
            errors=("external error",),
        )

        self.assertEqual(snapshot.errors, ("tick error", "external error"))

    def test_build_realtime_snapshot_missing_current_price_adds_error(self) -> None:
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick(bid=None, ask=None, last=None))

        self.assertIn("missing current price", snapshot.errors)

    def test_build_realtime_snapshot_missing_spread_points_adds_error(self) -> None:
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick(point=None))

        self.assertIn("missing spread points", snapshot.errors)

    def test_build_realtime_snapshot_preserves_market_open(self) -> None:
        snapshot = build_realtime_snapshot("XAUUSD", _candles_by_timeframe(), _tick(), market_open=False)

        self.assertFalse(snapshot.market_open)

    def test_build_realtime_snapshot_preserves_candles_by_timeframe(self) -> None:
        candles = _candles_by_timeframe()
        snapshot = build_realtime_snapshot("XAUUSD", candles, _tick())

        self.assertIs(snapshot.candles_by_timeframe, candles)

    def test_fetch_realtime_market_snapshot_initialize_failure_returns_closed_snapshot(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(initialize_result=False))

        self.assertFalse(snapshot.market_open)

    def test_fetch_realtime_market_snapshot_initialize_failure_has_error(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(initialize_result=False))

        self.assertTrue(any("MT5 initialize failed" in error for error in snapshot.errors))

    def test_fetch_realtime_market_snapshot_fetches_all_timeframes(self) -> None:
        fake_mt5 = _fake_mt5(rates=[_rate_dict()])

        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1", "M5"), 10, fake_mt5)

        self.assertEqual(set(snapshot.candles_by_timeframe), {"M1", "M5"})
        self.assertEqual(len(snapshot.candles_by_timeframe["M1"]), 1)
        self.assertEqual(len(snapshot.candles_by_timeframe["M5"]), 1)

    def test_fetch_realtime_market_snapshot_missing_candles_adds_error(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(rates=[]))

        self.assertIn("missing candles for M1", snapshot.errors)

    def test_fetch_realtime_market_snapshot_forwards_tick_errors(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(tick=None, rates=[_rate_dict()]))

        self.assertIn("missing MT5 tick", snapshot.errors)

    def test_fetch_realtime_market_snapshot_calculates_current_price(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(rates=[_rate_dict()]))

        self.assertEqual(snapshot.current_price, 100.1)

    def test_fetch_realtime_market_snapshot_calculates_spread_points(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(rates=[_rate_dict()]))

        self.assertEqual(snapshot.spread_points, 20.000000000000284)

    def test_fetch_realtime_market_snapshot_candle_count_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "candle count must be greater than zero"):
            fetch_realtime_market_snapshot("XAUUSD", ("M1",), 0, _fake_mt5())

    def test_fetch_realtime_market_snapshot_invalid_timeframe_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported realtime timeframe"):
            fetch_realtime_market_snapshot("XAUUSD", ("D1",), 10, _fake_mt5())

    def test_fetch_realtime_market_snapshot_handles_missing_tick_info_and_candles(self) -> None:
        snapshot = fetch_realtime_market_snapshot("XAUUSD", ("M1",), 10, _fake_mt5(tick=None, symbol_info=None, rates=[]))

        self.assertFalse(snapshot.market_open)
        self.assertIn("missing candles for M1", snapshot.errors)
        self.assertIn("missing MT5 tick", snapshot.errors)
        self.assertIn("missing MT5 symbol info", snapshot.errors)

    def test_source_has_no_forbidden_terms(self) -> None:
        source = _source_path().read_text(encoding="utf-8")

        self.assertNotIn("order_send", source)
        self.assertNotIn("auto_trade", source)
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("AUTO_TRADE_ORDER_FILE", source)
        self.assertNotIn("trading_signal_order", source)
        self.assertNotIn("positions_get", source)
        self.assertNotIn("orders_get", source)
        self.assertNotIn("TRADE_ACTION_", source)

    def test_does_not_create_root_order_intent_file(self) -> None:
        self.assertFalse(Path("trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        self.assertFalse(Path("logs/trading_signal_order.csv").exists())


def _rate_dict(**overrides: object) -> dict[str, object]:
    rate: dict[str, object] = {
        "time": 1_700_000_000,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "tick_volume": 123,
    }
    rate.update(overrides)
    return rate


def _tick(**overrides: object) -> RealtimeMarketTick:
    tick = RealtimeMarketTick(
        symbol="XAUUSD",
        bid=100.0,
        ask=100.2,
        last=None,
        timestamp=datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc),
        point=0.01,
        errors=(),
    )
    return RealtimeMarketTick(
        symbol=overrides.get("symbol", tick.symbol),
        bid=overrides.get("bid", tick.bid),
        ask=overrides.get("ask", tick.ask),
        last=overrides.get("last", tick.last),
        timestamp=overrides.get("timestamp", tick.timestamp),
        point=overrides.get("point", tick.point),
        errors=overrides.get("errors", tick.errors),
    )


def _candles_by_timeframe() -> dict[str, list[Candle]]:
    return {"M1": [Candle("2026-05-28T09:00:00+00:00", 100.0, 101.0, 99.0, 100.5, 123.0)]}


_DEFAULT = object()


class _FakeMt5:
    TIMEFRAME_M1 = "FAKE_M1"
    TIMEFRAME_M5 = "FAKE_M5"
    TIMEFRAME_M15 = "FAKE_M15"
    TIMEFRAME_M30 = "FAKE_M30"
    TIMEFRAME_H1 = "FAKE_H1"
    TIMEFRAME_H4 = "FAKE_H4"

    def __init__(
        self,
        *,
        initialize_result: bool = True,
        last_error_value: object = (0, "ok"),
        rates: object = _DEFAULT,
        tick: object = _DEFAULT,
        symbol_info: object = _DEFAULT,
    ) -> None:
        self.initialize_result = initialize_result
        self.last_error_value = last_error_value
        self.rates = [_rate_dict()] if rates is _DEFAULT else rates
        self.tick = (
            SimpleNamespace(bid=100.0, ask=100.2, last=100.1, time=1_700_000_000)
            if tick is _DEFAULT
            else tick
        )
        self.info = SimpleNamespace(point=0.01) if symbol_info is _DEFAULT else symbol_info
        self.copy_rates_call = None

    def initialize(self):
        return self.initialize_result

    def last_error(self):
        return self.last_error_value

    def copy_rates_from_pos(self, symbol, timeframe_code, start_pos, count):
        self.copy_rates_call = (symbol, timeframe_code, start_pos, count)
        return self.rates

    def symbol_info_tick(self, symbol):
        return self.tick

    def symbol_info(self, symbol):
        return self.info


def _fake_mt5(**kwargs: object) -> _FakeMt5:
    return _FakeMt5(**kwargs)


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "realtime_market_data.py"


if __name__ == "__main__":
    unittest.main()
