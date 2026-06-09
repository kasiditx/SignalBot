from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from trading_signal_bot.config import load_env_file, load_signal_config
from trading_signal_bot.time_utils import parse_candle_timestamp


DEFAULT_OUTPUT_DIR = Path("logs/data_coverage")


@dataclass(frozen=True)
class CoverageRow:
    timeframe: str
    path: str
    rows: int
    first: str
    last: str
    requested_start_1m: str
    has_1m: bool
    requested_start_2m: str
    has_2m: bool
    requested_start_3m: str
    has_3m: bool
    requested_start_6m: str
    has_6m: bool
    requested_start_12m: str
    has_12m: bool


def main() -> int:
    load_env_file()
    output_dir = Path(os.getenv("DATA_COVERAGE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_signal_config()
    paths = {
        timeframe: path
        for timeframe, path in config.timeframe_paths.items()
        if path
    }
    if config.csv_path:
        paths.setdefault(config.execution_timeframe, config.csv_path)

    rows: list[CoverageRow] = []
    for timeframe in sorted(paths):
        path = Path(paths[timeframe])
        rows.append(_coverage_for_path(timeframe, path))

    csv_path = output_dir / "real_data_coverage.csv"
    json_path = output_dir / "real_data_coverage.json"
    _write_csv(csv_path, rows)
    _write_json(json_path, rows)
    print(_format_rows(rows, csv_path, json_path))
    return 0


def _coverage_for_path(timeframe: str, path: Path) -> CoverageRow:
    timestamps = _read_timestamps(path)
    if not timestamps:
        empty = ""
        return CoverageRow(timeframe, str(path), 0, empty, empty, empty, False, empty, False, empty, False, empty, False, empty, False)

    first = timestamps[0]
    last = timestamps[-1]
    starts = {months: _start_of_months_back(last, months) for months in (1, 2, 3, 6, 12)}
    return CoverageRow(
        timeframe=timeframe,
        path=str(path),
        rows=len(timestamps),
        first=first.isoformat(timespec="minutes"),
        last=last.isoformat(timespec="minutes"),
        requested_start_1m=starts[1].isoformat(timespec="minutes"),
        has_1m=first <= starts[1],
        requested_start_2m=starts[2].isoformat(timespec="minutes"),
        has_2m=first <= starts[2],
        requested_start_3m=starts[3].isoformat(timespec="minutes"),
        has_3m=first <= starts[3],
        requested_start_6m=starts[6].isoformat(timespec="minutes"),
        has_6m=first <= starts[6],
        requested_start_12m=starts[12].isoformat(timespec="minutes"),
        has_12m=first <= starts[12],
    )


def _read_timestamps(path: Path) -> list[datetime]:
    if not path.exists():
        return []
    timestamps: list[datetime] = []
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            timestamp = row.get("timestamp")
            if timestamp:
                timestamps.append(parse_candle_timestamp(timestamp))
    return timestamps


def _start_of_months_back(latest_time: datetime, months: int) -> datetime:
    year = latest_time.year
    month = latest_time.month - months
    while month <= 0:
        month += 12
        year -= 1
    return latest_time.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _write_csv(path: Path, rows: list[CoverageRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(CoverageRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(path: Path, rows: list[CoverageRow]) -> None:
    path.write_text(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")


def _format_rows(rows: list[CoverageRow], csv_path: Path, json_path: Path) -> str:
    lines = ["Real Data Coverage"]
    for row in rows:
        lines.append(
            "- "
            f"{row.timeframe}: rows={row.rows}, {row.first} -> {row.last}, "
            f"1m={row.has_1m}, 2m={row.has_2m}, 3m={row.has_3m}, "
            f"6m={row.has_6m}, 12m={row.has_12m}"
        )
    lines.extend(["", f"CSV: {csv_path}", f"JSON: {json_path}"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
