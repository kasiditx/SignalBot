from __future__ import annotations

from dataclasses import dataclass

from .models import Candle, SignalAction, TrendDirection


SWING_HIGH = "HIGH"
SWING_LOW = "LOW"


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: str
    price: float
    kind: str


@dataclass(frozen=True)
class InvalidationLevels:
    buy: float | None
    sell: float | None


@dataclass(frozen=True)
class MarketStructureResult:
    trend: TrendDirection
    structure_label: str
    swings: tuple[SwingPoint, ...]
    latest_swing_high: SwingPoint | None
    latest_swing_low: SwingPoint | None
    has_bos: bool
    has_choch: bool
    bos_direction: SignalAction | None
    choch_direction: SignalAction | None
    invalidation: InvalidationLevels


def find_swings(candles: list[Candle], lookback: int = 2) -> list[SwingPoint]:
    if lookback < 1:
        raise ValueError("lookback must be at least 1")

    required_candles = (lookback * 2) + 1
    if len(candles) < required_candles:
        return []

    swings: list[SwingPoint] = []
    for index in range(lookback, len(candles) - lookback):
        window = candles[index - lookback : index + lookback + 1]
        candle = candles[index]
        is_swing_high = candle.high == max(item.high for item in window)
        is_swing_low = candle.low == min(item.low for item in window)

        if is_swing_high and _is_unique_extreme(candle.high, [item.high for item in window]):
            swings.append(SwingPoint(index=index, timestamp=candle.timestamp, price=candle.high, kind=SWING_HIGH))
        if is_swing_low and _is_unique_extreme(candle.low, [item.low for item in window]):
            swings.append(SwingPoint(index=index, timestamp=candle.timestamp, price=candle.low, kind=SWING_LOW))

    return swings


def analyze_market_structure(
    candles: list[Candle],
    lookback: int = 2,
    min_swings: int = 4,
) -> MarketStructureResult:
    swings = tuple(find_swings(candles, lookback))
    latest_high = _latest_swing(swings, SWING_HIGH)
    latest_low = _latest_swing(swings, SWING_LOW)
    invalidation = latest_invalidation_levels_from_swings(latest_high, latest_low)

    if len(candles) < 2 or len(swings) < min_swings:
        return MarketStructureResult(
            trend=TrendDirection.SIDEWAYS,
            structure_label="Sideway/unclear: not enough confirmed swings",
            swings=swings,
            latest_swing_high=latest_high,
            latest_swing_low=latest_low,
            has_bos=False,
            has_choch=False,
            bos_direction=None,
            choch_direction=None,
            invalidation=invalidation,
        )

    high_points = [swing for swing in swings if swing.kind == SWING_HIGH]
    low_points = [swing for swing in swings if swing.kind == SWING_LOW]
    trend, label = _classify_trend(high_points, low_points)
    bos_direction = _break_of_structure(candles[-1], latest_high, latest_low)
    choch_direction = _change_of_character(trend, bos_direction)

    return MarketStructureResult(
        trend=trend,
        structure_label=label,
        swings=swings,
        latest_swing_high=latest_high,
        latest_swing_low=latest_low,
        has_bos=bos_direction is not None,
        has_choch=choch_direction is not None,
        bos_direction=bos_direction,
        choch_direction=choch_direction,
        invalidation=invalidation,
    )


def latest_invalidation_levels(structure: MarketStructureResult) -> InvalidationLevels:
    return latest_invalidation_levels_from_swings(structure.latest_swing_high, structure.latest_swing_low)


def latest_invalidation_levels_from_swings(
    latest_swing_high: SwingPoint | None,
    latest_swing_low: SwingPoint | None,
) -> InvalidationLevels:
    return InvalidationLevels(
        buy=latest_swing_low.price if latest_swing_low else None,
        sell=latest_swing_high.price if latest_swing_high else None,
    )


def _latest_swing(swings: tuple[SwingPoint, ...], kind: str) -> SwingPoint | None:
    for swing in reversed(swings):
        if swing.kind == kind:
            return swing
    return None


def _classify_trend(
    high_points: list[SwingPoint],
    low_points: list[SwingPoint],
) -> tuple[TrendDirection, str]:
    if len(high_points) < 2 or len(low_points) < 2:
        return TrendDirection.SIDEWAYS, "Sideway/unclear: not enough high and low swings"

    previous_high, latest_high = high_points[-2], high_points[-1]
    previous_low, latest_low = low_points[-2], low_points[-1]
    higher_high = latest_high.price > previous_high.price
    higher_low = latest_low.price > previous_low.price
    lower_high = latest_high.price < previous_high.price
    lower_low = latest_low.price < previous_low.price

    if higher_high and higher_low:
        return TrendDirection.BULLISH, "Uptrend: HH/HL confirmed"
    if lower_high and lower_low:
        return TrendDirection.BEARISH, "Downtrend: LH/LL confirmed"
    return TrendDirection.SIDEWAYS, "Sideway/unclear: mixed swing structure"


def _break_of_structure(
    latest: Candle,
    latest_swing_high: SwingPoint | None,
    latest_swing_low: SwingPoint | None,
) -> SignalAction | None:
    if latest_swing_high and latest.close > latest_swing_high.price:
        return SignalAction.BUY
    if latest_swing_low and latest.close < latest_swing_low.price:
        return SignalAction.SELL
    return None


def _change_of_character(
    trend: TrendDirection,
    bos_direction: SignalAction | None,
) -> SignalAction | None:
    if trend == TrendDirection.BEARISH and bos_direction == SignalAction.BUY:
        return SignalAction.BUY
    if trend == TrendDirection.BULLISH and bos_direction == SignalAction.SELL:
        return SignalAction.SELL
    return None


def _is_unique_extreme(value: float, values: list[float]) -> bool:
    return values.count(value) == 1
