from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from typing import Any

from .models import Candle


SUPPORTED_REALTIME_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4")


@dataclass(frozen=True)
class RealtimeMarketTick:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    timestamp: datetime | None
    point: float | None
    errors: tuple[str, ...]


@dataclass(frozen=True)
class RealtimeMarketSnapshot:
    symbol: str
    candles_by_timeframe: dict[str, list[Candle]]
    bid: float | None
    ask: float | None
    current_price: float | None
    spread_points: float | None
    timestamp: datetime
    market_open: bool
    errors: tuple[str, ...]


def normalize_realtime_timeframe(timeframe: str) -> str:
    normalized = timeframe.strip().upper()
    if normalized not in SUPPORTED_REALTIME_TIMEFRAMES:
        allowed = ", ".join(SUPPORTED_REALTIME_TIMEFRAMES)
        raise ValueError(f"Unsupported realtime timeframe: {timeframe}. Expected one of: {allowed}")
    return normalized


def import_mt5_module() -> object:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc
    return mt5


def mt5_timeframe_code(timeframe: str, mt5_module: object | None = None) -> object:
    normalized = normalize_realtime_timeframe(timeframe)
    if mt5_module is None:
        return normalized

    constant_name = f"TIMEFRAME_{normalized}"
    constant = getattr(mt5_module, constant_name, None)
    if constant is None:
        raise ValueError(f"MT5 module is missing timeframe constant: {constant_name}")
    return constant


def ensure_mt5_initialized(mt5_module: object) -> tuple[bool, str | None]:
    initialized = bool(mt5_module.initialize())
    if initialized:
        return True, None

    last_error = getattr(mt5_module, "last_error", None)
    if callable(last_error):
        return False, f"MT5 initialize failed: {last_error()}"
    return False, "MT5 initialize failed"


def fetch_mt5_candles(
    symbol: str,
    timeframe: str,
    count: int,
    mt5_module: object | None = None,
) -> list[Candle]:
    if count <= 0:
        raise ValueError("candle count must be greater than zero")

    mt5 = mt5_module or import_mt5_module()
    timeframe_code = mt5_timeframe_code(timeframe, mt5)
    rates = mt5.copy_rates_from_pos(symbol, timeframe_code, 0, count)
    if rates is None:
        return []
    return [mt5_rate_to_candle(rate) for rate in rates]


def fetch_mt5_tick(
    symbol: str,
    mt5_module: object | None = None,
) -> RealtimeMarketTick:
    mt5 = mt5_module or import_mt5_module()
    tick = mt5.symbol_info_tick(symbol)
    symbol_info = mt5.symbol_info(symbol)
    errors: tuple[str, ...] = ()

    if tick is None:
        errors += ("missing MT5 tick",)
    if symbol_info is None:
        errors += ("missing MT5 symbol info",)

    bid = _optional_float_from_object(tick, "bid")
    ask = _optional_float_from_object(tick, "ask")
    last = _optional_float_from_object(tick, "last")
    timestamp = _optional_datetime_from_object(tick, "time")
    point = _optional_float_from_object(symbol_info, "point")

    if point is None or point <= 0:
        errors += ("missing MT5 point",)

    return RealtimeMarketTick(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        timestamp=timestamp,
        point=point,
        errors=errors,
    )


def fetch_realtime_market_snapshot(
    symbol: str,
    timeframes: tuple[str, ...],
    candle_count: int,
    mt5_module: object | None = None,
) -> RealtimeMarketSnapshot:
    if candle_count <= 0:
        raise ValueError("candle count must be greater than zero")

    try:
        mt5 = mt5_module or import_mt5_module()
    except RuntimeError as exc:
        return _market_data_error_snapshot(symbol, str(exc))

    initialized, init_error = ensure_mt5_initialized(mt5)
    if not initialized:
        return _market_data_error_snapshot(symbol, init_error or "MT5 initialize failed")

    candles_by_timeframe: dict[str, list[Candle]] = {}
    errors: tuple[str, ...] = ()
    for timeframe in timeframes:
        normalized = normalize_realtime_timeframe(timeframe)
        try:
            candles = fetch_mt5_candles(symbol, normalized, candle_count, mt5)
        except Exception as exc:
            candles_by_timeframe[normalized] = []
            errors += (f"failed to map candles for {normalized}: {exc}",)
            continue

        candles_by_timeframe[normalized] = candles
        if not candles:
            errors += (f"missing candles for {normalized}",)

    tick = fetch_mt5_tick(symbol, mt5)
    has_candles = any(candles_by_timeframe.values())
    has_tick = "missing MT5 tick" not in tick.errors
    market_open = has_candles and has_tick
    return build_realtime_snapshot(
        symbol=symbol,
        candles_by_timeframe=candles_by_timeframe,
        tick=tick,
        market_open=market_open,
        errors=errors,
    )


def mt5_rate_to_candle(rate: object) -> Candle:
    timestamp = _rate_value(rate, "time")
    open_price = _required_float(rate, "open")
    high = _required_float(rate, "high")
    low = _required_float(rate, "low")
    close = _required_float(rate, "close")
    volume = _optional_volume(rate)

    return Candle(
        timestamp=_format_rate_timestamp(timestamp),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def calculate_current_price(
    bid: float | None,
    ask: float | None,
    last: float | None = None,
) -> float | None:
    if last is not None and last > 0:
        return float(last)
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2
    return None


def calculate_spread_points(
    bid: float | None,
    ask: float | None,
    point: float | None,
) -> float | None:
    if bid is None or ask is None or point is None:
        return None
    if bid <= 0 or ask <= 0 or point <= 0:
        return None
    if ask < bid:
        return None
    return (float(ask) - float(bid)) / float(point)


def build_realtime_snapshot(
    symbol: str,
    candles_by_timeframe: dict[str, list[Candle]],
    tick: RealtimeMarketTick,
    market_open: bool = True,
    errors: tuple[str, ...] = (),
) -> RealtimeMarketSnapshot:
    current_price = calculate_current_price(tick.bid, tick.ask, tick.last)
    spread_points = calculate_spread_points(tick.bid, tick.ask, tick.point)
    combined_errors = tuple(tick.errors) + tuple(errors)
    if current_price is None:
        combined_errors += ("missing current price",)
    if spread_points is None:
        combined_errors += ("missing spread points",)

    return RealtimeMarketSnapshot(
        symbol=symbol,
        candles_by_timeframe=candles_by_timeframe,
        bid=tick.bid,
        ask=tick.ask,
        current_price=current_price,
        spread_points=spread_points,
        timestamp=tick.timestamp or datetime.now(timezone.utc),
        market_open=market_open,
        errors=combined_errors,
    )


def _rate_value(rate: object, key: str) -> Any:
    if isinstance(rate, dict):
        return rate.get(key)
    try:
        return rate[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return getattr(rate, key, None)


def _required_float(rate: object, key: str) -> float:
    value = _rate_value(rate, key)
    if value is None:
        raise ValueError(f"MT5 rate is missing required field: {key}")
    return float(value)


def _optional_volume(rate: object) -> float:
    for key in ("tick_volume", "real_volume", "volume"):
        value = _rate_value(rate, key)
        if value is not None:
            return float(value)
    return 0.0


def _format_rate_timestamp(value: object) -> str:
    if value is None:
        raise ValueError("MT5 rate is missing required field: time")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Real):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return str(value)


def _optional_float_from_object(source: object | None, key: str) -> float | None:
    if source is None:
        return None
    value = _rate_value(source, key)
    if value is None:
        return None
    return float(value)


def _optional_datetime_from_object(source: object | None, key: str) -> datetime | None:
    if source is None:
        return None
    value = _rate_value(source, key)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, Real):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


def _market_data_error_snapshot(symbol: str, error: str) -> RealtimeMarketSnapshot:
    return RealtimeMarketSnapshot(
        symbol=symbol,
        candles_by_timeframe={},
        bid=None,
        ask=None,
        current_price=None,
        spread_points=None,
        timestamp=datetime.now(timezone.utc),
        market_open=False,
        errors=(error,),
    )
