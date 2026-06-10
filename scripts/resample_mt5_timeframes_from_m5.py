from __future__ import annotations

import argparse
import csv
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trading_signal_bot.config import load_env_file, load_signal_config
from trading_signal_bot.time_utils import parse_candle_timestamp


TARGET_TIMEFRAMES = {
    "M15": 15,
    "M30": 30,
}
SOURCE_TIMEFRAME_MINUTES = 5
READ_RETRY_EXCEPTIONS = (KeyError, TypeError, ValueError)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build higher-timeframe MT5 CSV files from real M5 candles."
    )
    parser.add_argument(
        "--timeframes",
        default="M15,M30",
        help="Comma-separated target timeframes to rebuild from M5. Supported: M15,M30",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite configured target CSV files. Existing files are backed up first.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup when --overwrite is used.",
    )
    parser.add_argument(
        "--derived-suffix",
        default="",
        help="Write to a separate derived file by appending this suffix to the configured filename stem.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep rebuilding derived files so forward tests can read fresh higher-timeframe data.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=5.0,
        help="Refresh interval used with --watch.",
    )
    args = parser.parse_args()
    if args.interval_seconds < 1:
        raise SystemExit("--interval-seconds must be at least 1")

    load_env_file()
    config = load_signal_config()
    m5_path = Path(config.timeframe_paths.get("M5") or config.csv_path)
    target_timeframes = _parse_timeframes(args.timeframes)

    backup_existing_files = not args.no_backup
    while True:
        _build_timeframes(
            m5_path=m5_path,
            config_paths=config.timeframe_paths,
            target_timeframes=target_timeframes,
            derived_suffix=args.derived_suffix,
            overwrite=args.overwrite,
            backup_existing_files=backup_existing_files,
        )
        if not args.watch:
            break
        backup_existing_files = False
        time.sleep(args.interval_seconds)

    return 0


def _build_timeframes(
    *,
    m5_path: Path,
    config_paths: dict[str, str],
    target_timeframes: list[str],
    derived_suffix: str,
    overwrite: bool,
    backup_existing_files: bool,
) -> None:
    candles = _read_m5_candles(m5_path)
    if not candles:
        raise SystemExit(f"No M5 candles found: {m5_path}")

    for timeframe in target_timeframes:
        target_path_raw = config_paths.get(timeframe)
        if not target_path_raw:
            raise SystemExit(f"SIGNAL_CSV_PATH_{timeframe} is not configured")

        target_path = _target_path(Path(target_path_raw), derived_suffix)
        resampled = _resample(candles, TARGET_TIMEFRAMES[timeframe])
        if not resampled:
            raise SystemExit(f"No candles produced for {timeframe}")

        if target_path.exists() and not overwrite:
            raise SystemExit(f"{target_path} exists. Re-run with --overwrite to replace it.")

        if target_path.exists() and backup_existing_files:
            backup_path = _backup_path(target_path)
            shutil.copy2(target_path, backup_path)
            print(f"Backup: {backup_path}")

        _write_candles(target_path, resampled)
        print(
            f"{timeframe}: wrote {len(resampled)} candles to {target_path} "
            f"({resampled[0].timestamp.isoformat(timespec='minutes')} -> "
            f"{resampled[-1].timestamp.isoformat(timespec='minutes')})"
        )


def _parse_timeframes(raw_value: str) -> list[str]:
    timeframes = [value.strip().upper() for value in raw_value.split(",") if value.strip()]
    unsupported = [value for value in timeframes if value not in TARGET_TIMEFRAMES]
    if unsupported:
        raise SystemExit(f"Unsupported timeframe(s): {', '.join(unsupported)}")
    return timeframes


def _read_m5_candles(path: Path) -> list[Candle]:
    if not path.exists():
        return []

    last_error: Exception | None = None
    for _ in range(10):
        before = path.stat()
        try:
            candles = _read_candles_once(path)
        except READ_RETRY_EXCEPTIONS as exc:
            last_error = exc
            time.sleep(0.5)
            continue
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return candles
        time.sleep(0.5)

    if last_error is not None:
        raise RuntimeError(f"M5 source file is not readable yet: {path}") from last_error
    raise RuntimeError(f"M5 source file is still changing while reading: {path}")


def _read_candles_once(path: Path) -> list[Candle]:
    candles: list[Candle] = []

    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            _validate_candle_row(row)
            candles.append(
                Candle(
                    timestamp=parse_candle_timestamp(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                )
            )
    return sorted(candles, key=lambda candle: candle.timestamp)


def _validate_candle_row(row: dict[str, str | None]) -> None:
    required_fields = ("timestamp", "open", "high", "low", "close", "volume")
    missing_fields = [field for field in required_fields if not row.get(field)]
    if missing_fields:
        raise ValueError(f"CSV row is incomplete. Missing: {', '.join(missing_fields)}")


def _resample(candles: list[Candle], target_minutes: int) -> list[Candle]:
    expected_count = target_minutes // SOURCE_TIMEFRAME_MINUTES
    grouped: dict[datetime, list[Candle]] = {}
    for candle in candles:
        grouped.setdefault(_floor_time(candle.timestamp, target_minutes), []).append(candle)

    resampled: list[Candle] = []
    for timestamp in sorted(grouped):
        group = sorted(grouped[timestamp], key=lambda candle: candle.timestamp)
        if not _is_complete_group(group, expected_count):
            continue
        resampled.append(
            Candle(
                timestamp=timestamp,
                open=group[0].open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=group[-1].close,
                volume=sum(candle.volume for candle in group),
            )
        )
    return resampled


def _is_complete_group(candles: list[Candle], expected_count: int) -> bool:
    if len(candles) != expected_count:
        return False

    for index in range(1, len(candles)):
        if candles[index].timestamp - candles[index - 1].timestamp != timedelta(minutes=SOURCE_TIMEFRAME_MINUTES):
            return False
    return True


def _floor_time(value: datetime, target_minutes: int) -> datetime:
    minute = (value.minute // target_minutes) * target_minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.broker_export_backup{path.suffix}")


def _target_path(configured_path: Path, derived_suffix: str) -> Path:
    if not derived_suffix:
        return configured_path
    if configured_path.stem.endswith(derived_suffix):
        return configured_path
    return configured_path.with_name(f"{configured_path.stem}{derived_suffix}{configured_path.suffix}")


def _write_candles(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow(
                [
                    candle.timestamp.strftime("%Y.%m.%d %H:%M"),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
            )


if __name__ == "__main__":
    raise SystemExit(main())
