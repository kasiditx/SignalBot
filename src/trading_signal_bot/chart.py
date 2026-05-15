from __future__ import annotations

import struct
import zlib
from pathlib import Path

from .models import Candle, Signal, SignalAction


Color = tuple[int, int, int]

WHITE: Color = (255, 255, 255)
INK: Color = (32, 40, 48)
MUTED: Color = (120, 130, 140)
GRID: Color = (226, 231, 236)
GREEN: Color = (19, 141, 88)
RED: Color = (207, 73, 64)
BLUE: Color = (56, 111, 214)
ORANGE: Color = (232, 149, 58)
PURPLE: Color = (113, 82, 179)
YELLOW: Color = (209, 168, 41)
TEAL: Color = (20, 150, 150)


def render_signal_chart(
    candles: list[Candle],
    signal: Signal,
    output_path: str,
    max_candles: int = 80,
    width: int = 1100,
    height: int = 650,
) -> str:
    selected = candles[-max_candles:]
    if not selected:
        raise ValueError("Cannot render chart without candles")

    image = _blank(width, height, WHITE)
    margin_left = 72
    margin_right = 44
    margin_top = 52
    margin_bottom = 86
    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom

    low = min(min(candle.low for candle in selected), signal.support, signal.levels.stop_loss or signal.support)
    high = max(max(candle.high for candle in selected), signal.resistance, signal.levels.take_profit or signal.resistance)
    padding = (high - low) * 0.08 if high > low else 1
    low -= padding
    high += padding

    _draw_text(image, 32, 20, f"{signal.symbol} {signal.timeframe} | {signal.action.value} | {signal.setup_type}", INK, scale=2)
    _draw_text(image, 32, height - 36, "Not financial advice | Demo/Paper test first", MUTED, scale=1)

    for index in range(6):
        y = plot_top + int((plot_bottom - plot_top) * index / 5)
        _draw_line(image, plot_left, y, plot_right, y, GRID)
        price = high - ((high - low) * index / 5)
        _draw_text(image, 8, y - 6, _format_price(price), MUTED, scale=1)

    candle_count = len(selected)
    slot_width = (plot_right - plot_left) / max(candle_count, 1)
    candle_width = max(3, int(slot_width * 0.55))

    for index, candle in enumerate(selected):
        center_x = int(plot_left + (index + 0.5) * slot_width)
        open_y = _price_to_y(candle.open, low, high, plot_top, plot_bottom)
        close_y = _price_to_y(candle.close, low, high, plot_top, plot_bottom)
        high_y = _price_to_y(candle.high, low, high, plot_top, plot_bottom)
        low_y = _price_to_y(candle.low, low, high, plot_top, plot_bottom)
        color = GREEN if candle.close >= candle.open else RED
        _draw_line(image, center_x, high_y, center_x, low_y, color)
        body_top = min(open_y, close_y)
        body_bottom = max(open_y, close_y)
        if body_bottom == body_top:
            body_bottom += 1
        _fill_rect(image, center_x - candle_width // 2, body_top, center_x + candle_width // 2, body_bottom, color)

    _draw_trend_line(image, selected, low, high, plot_left, plot_right, plot_top, plot_bottom)
    _draw_fibonacci_levels(image, selected, signal, low, high, plot_left, plot_right, plot_top, plot_bottom)
    _draw_level(image, signal.support, low, high, plot_left, plot_right, plot_top, plot_bottom, BLUE, "Support")
    _draw_level(image, signal.resistance, low, high, plot_left, plot_right, plot_top, plot_bottom, ORANGE, "Resistance")
    if signal.levels.stop_loss is not None:
        _draw_level(image, signal.levels.stop_loss, low, high, plot_left, plot_right, plot_top, plot_bottom, RED, "SL")
    if signal.levels.take_profit is not None:
        _draw_level(image, signal.levels.take_profit, low, high, plot_left, plot_right, plot_top, plot_bottom, GREEN, "TP")
    if signal.levels.entry is not None:
        _draw_level(image, signal.levels.entry, low, high, plot_left, plot_right, plot_top, plot_bottom, PURPLE, "Entry")

    latest = selected[-1]
    _draw_text(image, plot_left, plot_bottom + 18, selected[0].timestamp, MUTED, scale=1)
    _draw_text(image, plot_right - 150, plot_bottom + 18, latest.timestamp, MUTED, scale=1)
    _draw_text(image, plot_left, plot_bottom + 42, f"Close { _format_price(latest.close) } | RSI {signal.rsi:.2f} | ATR {_format_price(signal.atr)}", INK, scale=1)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_encode_png(image))
    return str(output)


def _draw_trend_line(
    image: list[list[Color]],
    candles: list[Candle],
    low: float,
    high: float,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> None:
    if len(candles) < 12:
        return

    midpoint = len(candles) // 2
    first_half = candles[:midpoint]
    second_half = candles[midpoint:]
    slot_width = (right - left) / len(candles)
    bullish_context = candles[-1].close >= candles[0].close

    if bullish_context:
        first_index = min(range(len(first_half)), key=lambda index: first_half[index].low)
        second_index = midpoint + min(range(len(second_half)), key=lambda index: second_half[index].low)
        first_price = candles[first_index].low
        second_price = candles[second_index].low
        label = "Trend HL"
    else:
        first_index = max(range(len(first_half)), key=lambda index: first_half[index].high)
        second_index = midpoint + max(range(len(second_half)), key=lambda index: second_half[index].high)
        first_price = candles[first_index].high
        second_price = candles[second_index].high
        label = "Trend LH"

    x1 = int(left + (first_index + 0.5) * slot_width)
    x2 = int(left + (second_index + 0.5) * slot_width)
    y1 = _price_to_y(first_price, low, high, top, bottom)
    y2 = _price_to_y(second_price, low, high, top, bottom)
    _draw_line(image, x1, y1, x2, y2, TEAL)
    _draw_text(image, min(x1, x2), min(y1, y2) - 16, label, TEAL, scale=1)


def _draw_fibonacci_levels(
    image: list[list[Color]],
    candles: list[Candle],
    signal: Signal,
    low: float,
    high: float,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> None:
    swing_low = min(candle.low for candle in candles)
    swing_high = max(candle.high for candle in candles)
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return

    if signal.action == SignalAction.SELL:
        levels = (
            ("FIB 38.2", swing_low + swing_range * 0.382),
            ("FIB 50.0", swing_low + swing_range * 0.5),
            ("FIB 61.8", swing_low + swing_range * 0.618),
        )
    else:
        levels = (
            ("FIB 38.2", swing_high - swing_range * 0.382),
            ("FIB 50.0", swing_high - swing_range * 0.5),
            ("FIB 61.8", swing_high - swing_range * 0.618),
        )

    for label, price in levels:
        y = _price_to_y(price, low, high, top, bottom)
        _draw_dashed_line(image, left, y, right, y, YELLOW)
        _draw_text(image, left + 8, y - 12, f"{label} {_format_price(price)}", YELLOW, scale=1)


def _draw_level(
    image: list[list[Color]],
    price: float,
    low: float,
    high: float,
    left: int,
    right: int,
    top: int,
    bottom: int,
    color: Color,
    label: str,
) -> None:
    y = _price_to_y(price, low, high, top, bottom)
    _draw_line(image, left, y, right, y, color)
    _draw_text(image, right - 118, y - 14, f"{label} {_format_price(price)}", color, scale=1)


def _price_to_y(price: float, low: float, high: float, top: int, bottom: int) -> int:
    if high == low:
        return (top + bottom) // 2
    return int(bottom - ((price - low) / (high - low)) * (bottom - top))


def _blank(width: int, height: int, color: Color) -> list[list[Color]]:
    return [[color for _ in range(width)] for _ in range(height)]


def _set_pixel(image: list[list[Color]], x: int, y: int, color: Color) -> None:
    if 0 <= y < len(image) and 0 <= x < len(image[0]):
        image[y][x] = color


def _draw_line(image: list[list[Color]], x1: int, y1: int, x2: int, y2: int, color: Color) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    error = dx + dy
    x = x1
    y = y1
    while True:
        _set_pixel(image, x, y, color)
        if x == x2 and y == y2:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x += sx
        if doubled <= dx:
            error += dx
            y += sy


def _draw_dashed_line(image: list[list[Color]], x1: int, y1: int, x2: int, y2: int, color: Color) -> None:
    dash_length = 8
    gap_length = 6
    current = x1
    while current < x2:
        end = min(current + dash_length, x2)
        _draw_line(image, current, y1, end, y2, color)
        current = end + gap_length


def _fill_rect(image: list[list[Color]], x1: int, y1: int, x2: int, y2: int, color: Color) -> None:
    left = max(0, min(x1, x2))
    right = min(len(image[0]) - 1, max(x1, x2))
    top = max(0, min(y1, y2))
    bottom = min(len(image) - 1, max(y1, y2))
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            image[y][x] = color


def _draw_text(image: list[list[Color]], x: int, y: int, text: str, color: Color, scale: int = 1) -> None:
    cursor = x
    for character in text:
        glyph = FONT.get(character.upper(), FONT.get("?", []))
        for row_index, row in enumerate(glyph):
            for col_index, pixel in enumerate(row):
                if pixel == "1":
                    _fill_rect(
                        image,
                        cursor + col_index * scale,
                        y + row_index * scale,
                        cursor + ((col_index + 1) * scale) - 1,
                        y + ((row_index + 1) * scale) - 1,
                        color,
                    )
        cursor += 6 * scale


def _encode_png(image: list[list[Color]]) -> bytes:
    height = len(image)
    width = len(image[0])
    raw = bytearray()
    for row in image:
        raw.append(0)
        for red, green, blue in row:
            raw.extend([red, green, blue])

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
        + chunk(b"IEND", b"")
    )


def _format_price(value: float) -> str:
    return f"{value:.5f}".rstrip("0").rstrip(".")


FONT: dict[str, list[str]] = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "?": ["11110", "00001", "00010", "00100", "00100", "00000", "00100"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
}
