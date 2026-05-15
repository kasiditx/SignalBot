from __future__ import annotations

from .data import load_candles_from_csv
from .models import Candle, SignalConfig, TrendDirection


TIMEFRAME_ORDER = ("D1", "H4", "H1", "M30", "M15", "M5")
HIGHER_TIMEFRAMES = ("D1", "H4", "H1")
CONFIRMATION_TIMEFRAMES = ("M30", "M15")
EXECUTION_TIMEFRAME = "M5"


def load_timeframe_candles(config: SignalConfig) -> dict[str, list[Candle]]:
    candles_by_timeframe: dict[str, list[Candle]] = {}
    if not config.multi_timeframe_enabled:
        candles_by_timeframe[config.timeframe] = load_candles_from_csv(config.csv_path)
        return candles_by_timeframe

    for timeframe in TIMEFRAME_ORDER:
        path = config.timeframe_paths.get(timeframe)
        if not path:
            raise ValueError(f"SIGNAL_CSV_PATH_{timeframe} is required when SIGNAL_MULTI_TIMEFRAME=true")
        candles_by_timeframe[timeframe] = load_candles_from_csv(path)

    return candles_by_timeframe


def execution_candles(candles_by_timeframe: dict[str, list[Candle]], config: SignalConfig) -> list[Candle]:
    if config.multi_timeframe_enabled:
        return candles_by_timeframe[EXECUTION_TIMEFRAME]
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
        if timeframe in TIMEFRAME_ORDER
    }


def dominant_bias(trends: dict[str, TrendDirection]) -> TrendDirection:
    bullish = sum(1 for timeframe in HIGHER_TIMEFRAMES if trends.get(timeframe) == TrendDirection.BULLISH)
    bearish = sum(1 for timeframe in HIGHER_TIMEFRAMES if trends.get(timeframe) == TrendDirection.BEARISH)
    if bullish >= 2 and bullish > bearish:
        return TrendDirection.BULLISH
    if bearish >= 2 and bearish > bullish:
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def confirmation_bias(trends: dict[str, TrendDirection]) -> TrendDirection:
    bullish = sum(1 for timeframe in CONFIRMATION_TIMEFRAMES if trends.get(timeframe) == TrendDirection.BULLISH)
    bearish = sum(1 for timeframe in CONFIRMATION_TIMEFRAMES if trends.get(timeframe) == TrendDirection.BEARISH)
    if bullish >= 1 and bullish >= bearish:
        return TrendDirection.BULLISH
    if bearish >= 1 and bearish >= bullish:
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def format_trend_summary(trends: dict[str, TrendDirection]) -> str:
    return " | ".join(
        f"{timeframe}:{trends.get(timeframe, TrendDirection.SIDEWAYS).value}"
        for timeframe in TIMEFRAME_ORDER
    )
