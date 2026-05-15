from __future__ import annotations

from .models import Candle


def ema(values: list[float], period: int) -> list[float]:
    if period <= 1:
        raise ValueError("EMA period must be greater than 1")
    if len(values) < period:
        raise ValueError("Not enough values for EMA period")

    multiplier = 2 / (period + 1)
    first_average = sum(values[:period]) / period
    results: list[float] = [first_average]
    for value in values[period:]:
        results.append((value - results[-1]) * multiplier + results[-1])
    return results


def rsi(values: list[float], period: int) -> list[float]:
    if period <= 1:
        raise ValueError("RSI period must be greater than 1")
    if len(values) <= period:
        raise ValueError("Not enough values for RSI period")

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    results = [_rsi_from_averages(average_gain, average_loss)]

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        results.append(_rsi_from_averages(average_gain, average_loss))

    return results


def atr(candles: list[Candle], period: int) -> list[float]:
    if period <= 1:
        raise ValueError("ATR period must be greater than 1")
    if len(candles) <= period:
        raise ValueError("Not enough candles for ATR period")

    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_ranges.append(candle.high - candle.low)
            continue
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )

    first_atr = sum(true_ranges[1 : period + 1]) / period
    results = [first_atr]
    for true_range in true_ranges[period + 1 :]:
        results.append(((results[-1] * (period - 1)) + true_range) / period)
    return results


def _rsi_from_averages(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))
