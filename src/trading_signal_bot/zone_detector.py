from __future__ import annotations

from dataclasses import dataclass

from .models import Candle


SUPPLY = "SUPPLY"
DEMAND = "DEMAND"
NEAR_SUPPLY = "NEAR_SUPPLY"
NEAR_DEMAND = "NEAR_DEMAND"
MID_ZONE = "MID_ZONE"
OUTSIDE = "OUTSIDE"


@dataclass(frozen=True)
class PriceZone:
    kind: str
    low: float
    high: float
    origin_timestamp: str
    strength: float
    timeframe: str | None = None


@dataclass(frozen=True)
class SupportResistance:
    support: float | None
    resistance: float | None


@dataclass(frozen=True)
class PriceLocationResult:
    location: str
    nearest_zone: PriceZone | None
    distance_to_zone: float | None
    is_mid_zone: bool


def detect_supply_zones(
    candles: list[Candle],
    lookback: int = 80,
    impulse_ratio: float = 1.5,
    timeframe: str | None = None,
) -> list[PriceZone]:
    return _detect_impulse_zones(
        candles=candles,
        kind=SUPPLY,
        lookback=lookback,
        impulse_ratio=impulse_ratio,
        timeframe=timeframe,
    )


def detect_demand_zones(
    candles: list[Candle],
    lookback: int = 80,
    impulse_ratio: float = 1.5,
    timeframe: str | None = None,
) -> list[PriceZone]:
    return _detect_impulse_zones(
        candles=candles,
        kind=DEMAND,
        lookback=lookback,
        impulse_ratio=impulse_ratio,
        timeframe=timeframe,
    )


def nearest_levels(
    candles: list[Candle],
    lookback: int = 40,
) -> SupportResistance:
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if not candles:
        return SupportResistance(support=None, resistance=None)

    recent = candles[-lookback:]
    return SupportResistance(
        support=min(candle.low for candle in recent),
        resistance=max(candle.high for candle in recent),
    )


def classify_price_location(
    price: float,
    zones: list[PriceZone],
    support_resistance: SupportResistance,
    proximity: float,
) -> PriceLocationResult:
    if proximity < 0:
        raise ValueError("proximity must be greater than or equal to 0")

    nearest_zone, distance = _nearest_zone(price, zones)
    if nearest_zone and distance is not None and distance <= proximity:
        if nearest_zone.kind == SUPPLY:
            return PriceLocationResult(
                location=NEAR_SUPPLY,
                nearest_zone=nearest_zone,
                distance_to_zone=distance,
                is_mid_zone=False,
            )
        if nearest_zone.kind == DEMAND:
            return PriceLocationResult(
                location=NEAR_DEMAND,
                nearest_zone=nearest_zone,
                distance_to_zone=distance,
                is_mid_zone=False,
            )

    if _is_mid_zone(price, support_resistance, proximity):
        return PriceLocationResult(
            location=MID_ZONE,
            nearest_zone=nearest_zone,
            distance_to_zone=distance,
            is_mid_zone=True,
        )

    return PriceLocationResult(
        location=OUTSIDE,
        nearest_zone=nearest_zone,
        distance_to_zone=distance,
        is_mid_zone=False,
    )


def _detect_impulse_zones(
    candles: list[Candle],
    kind: str,
    lookback: int,
    impulse_ratio: float,
    timeframe: str | None,
) -> list[PriceZone]:
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if impulse_ratio <= 0:
        raise ValueError("impulse_ratio must be greater than 0")
    if len(candles) < 3:
        return []

    recent = candles[-lookback:]
    if len(recent) < 3:
        return []

    average_range = _average_range(recent)
    if average_range <= 0:
        return []

    zones: list[PriceZone] = []
    for index in range(1, len(recent)):
        base = recent[index - 1]
        impulse = recent[index]
        impulse_size = impulse.high - impulse.low
        if impulse_size < average_range * impulse_ratio:
            continue
        if kind == SUPPLY and impulse.close >= impulse.open:
            continue
        if kind == DEMAND and impulse.close <= impulse.open:
            continue

        zone_low, zone_high = _zone_bounds(base)
        zones.append(
            PriceZone(
                kind=kind,
                low=zone_low,
                high=zone_high,
                origin_timestamp=base.timestamp,
                strength=impulse_size / average_range,
                timeframe=timeframe,
            )
        )

    return zones


def _zone_bounds(candle: Candle) -> tuple[float, float]:
    body_low = min(candle.open, candle.close)
    body_high = max(candle.open, candle.close)
    wick_mid_low = min(candle.low, body_low)
    wick_mid_high = max(candle.high, body_high)
    return min(wick_mid_low, wick_mid_high), max(wick_mid_low, wick_mid_high)


def _average_range(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.high - candle.low for candle in candles) / len(candles)


def _nearest_zone(price: float, zones: list[PriceZone]) -> tuple[PriceZone | None, float | None]:
    if not zones:
        return None, None

    distances = [(_distance_to_zone(price, zone), zone) for zone in zones]
    distance, zone = min(distances, key=lambda item: item[0])
    return zone, distance


def _distance_to_zone(price: float, zone: PriceZone) -> float:
    if zone.low <= price <= zone.high:
        return 0.0
    if price < zone.low:
        return zone.low - price
    return price - zone.high


def _is_mid_zone(price: float, support_resistance: SupportResistance, proximity: float) -> bool:
    support = support_resistance.support
    resistance = support_resistance.resistance
    if support is None or resistance is None or resistance <= support:
        return False
    if price <= support + proximity or price >= resistance - proximity:
        return False

    midpoint = support + ((resistance - support) / 2)
    middle_band = (resistance - support) * 0.25
    return abs(price - midpoint) <= middle_band
