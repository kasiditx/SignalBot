from __future__ import annotations

from .data import load_candles_from_csv
from .models import Candle, SignalConfig, TrendDirection


TIMEFRAME_ORDER = ("H4", "H1", "M30", "M15", "M5", "M1")
HIGHER_TIMEFRAMES = ("H4", "H1")
CONFIRMATION_TIMEFRAMES = ("M30", "M15")
# Backward compatibility for modules that still import this constant directly.
# New routing should use config.execution_timeframe instead.
EXECUTION_TIMEFRAME = "M5"


def load_timeframe_candles(config: SignalConfig) -> dict[str, list[Candle]]:
    candles_by_timeframe: dict[str, list[Candle]] = {}
    if not config.multi_timeframe_enabled:
        execution_timeframe = _execution_timeframe(config)
        candles = load_candles_from_csv(config.csv_path)
        candles_by_timeframe[execution_timeframe] = candles
        if config.timeframe != execution_timeframe:
            candles_by_timeframe[config.timeframe] = candles
        return candles_by_timeframe

    for timeframe in _timeframes_to_load(config):
        path = config.timeframe_paths.get(timeframe)
        if not path:
            if timeframe == _execution_timeframe(config):
                raise ValueError(
                    f"SIGNAL_CSV_PATH_{timeframe} is required for execution timeframe "
                    f"{timeframe} when SIGNAL_MULTI_TIMEFRAME=true"
                )
            raise ValueError(f"SIGNAL_CSV_PATH_{timeframe} is required when SIGNAL_MULTI_TIMEFRAME=true")
        candles_by_timeframe[timeframe] = load_candles_from_csv(path)

    return candles_by_timeframe


def execution_candles(candles_by_timeframe: dict[str, list[Candle]], config: SignalConfig) -> list[Candle]:
    execution_timeframe = _execution_timeframe(config)
    if config.multi_timeframe_enabled:
        if execution_timeframe not in candles_by_timeframe:
            raise ValueError(f"No candles for execution timeframe {execution_timeframe}")
        return candles_by_timeframe[execution_timeframe]
    if execution_timeframe in candles_by_timeframe:
        return candles_by_timeframe[execution_timeframe]
    return candles_by_timeframe[config.timeframe]


def trend_direction(candles: list[Candle]) -> TrendDirection:
    if len(candles) < 14:
        return TrendDirection.SIDEWAYS

    recent = candles[-7:]
    previous = candles[-14:-7]
    recent_high = max(candle.high for candle in recent)
    recent_low = min(candle.low for candle in recent)
    previous_high = max(candle.high for candle in previous)
    previous_low = min(candle.low for candle in previous)
    close_delta = recent[-1].close - recent[0].close
    average_range = sum(candle.high - candle.low for candle in recent) / len(recent)

    if recent_high > previous_high and recent_low > previous_low and close_delta > average_range * 0.25:
        return TrendDirection.BULLISH
    if recent_high < previous_high and recent_low < previous_low and close_delta < -(average_range * 0.25):
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def trend_map(candles_by_timeframe: dict[str, list[Candle]]) -> dict[str, TrendDirection]:
    return {
        timeframe: trend_direction(candles)
        for timeframe, candles in candles_by_timeframe.items()
        if timeframe in _known_timeframes()
    }


def dominant_bias(
    trends: dict[str, TrendDirection],
    htf_timeframes: tuple[str, ...] = HIGHER_TIMEFRAMES,
) -> TrendDirection:
    bullish = sum(1 for timeframe in htf_timeframes if trends.get(timeframe) == TrendDirection.BULLISH)
    bearish = sum(1 for timeframe in htf_timeframes if trends.get(timeframe) == TrendDirection.BEARISH)
    required_votes = max(1, (len(htf_timeframes) // 2) + 1)
    if bullish >= required_votes and bullish > bearish:
        return TrendDirection.BULLISH
    if bearish >= required_votes and bearish > bullish:
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def confirmation_bias(
    trends: dict[str, TrendDirection],
    confirmation_timeframes: tuple[str, ...] = CONFIRMATION_TIMEFRAMES,
) -> TrendDirection:
    bullish = sum(1 for timeframe in confirmation_timeframes if trends.get(timeframe) == TrendDirection.BULLISH)
    bearish = sum(1 for timeframe in confirmation_timeframes if trends.get(timeframe) == TrendDirection.BEARISH)
    if bullish >= 1 and bullish >= bearish:
        return TrendDirection.BULLISH
    if bearish >= 1 and bearish >= bullish:
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def format_trend_summary(
    trends: dict[str, TrendDirection],
    timeframe_order: tuple[str, ...] = TIMEFRAME_ORDER,
) -> str:
    return " | ".join(
        f"{timeframe}:{trends.get(timeframe, TrendDirection.SIDEWAYS).value}"
        for timeframe in timeframe_order
    )


def _execution_timeframe(config: SignalConfig) -> str:
    return getattr(config, "execution_timeframe", EXECUTION_TIMEFRAME)


def _timeframe_order(config: SignalConfig) -> tuple[str, ...]:
    return getattr(config, "timeframe_order", TIMEFRAME_ORDER)


def _timeframes_to_load(config: SignalConfig) -> tuple[str, ...]:
    timeframe_order = _timeframe_order(config)
    execution_timeframe = _execution_timeframe(config)
    if execution_timeframe in timeframe_order:
        return timeframe_order
    return (*timeframe_order, execution_timeframe)


def _known_timeframes() -> set[str]:
    return set(TIMEFRAME_ORDER).union({"D1", EXECUTION_TIMEFRAME})
