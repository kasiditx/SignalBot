from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import AutoTradeConfig, Signal, SignalAction


ORDER_FIELDNAMES = [
    "nonce",
    "created_at",
    "symbol",
    "action",
    "volume",
    "entry",
    "stop_loss",
    "take_profit",
    "magic_number",
    "comment",
    "actual_risk_percent",
]


@dataclass(frozen=True)
class AutoTradeResult:
    status: str
    message: str
    nonce: str | None = None
    volume: float | None = None
    order_file: str | None = None


def process_auto_trade(signal: Signal, config: AutoTradeConfig) -> AutoTradeResult:
    if not config.enabled:
        return AutoTradeResult(status="disabled", message="Auto trade is disabled.")
    if signal.action == SignalAction.WAIT:
        return AutoTradeResult(status="skipped", message="WAIT signal is not tradable.")

    entry, stop_loss, take_profit = _required_levels(signal)
    volume = _position_size(
        account_balance=config.account_balance,
        risk_percent=config.risk_percent,
        entry=entry,
        stop_loss=stop_loss,
        contract_size=config.contract_size,
        min_volume=config.min_volume,
        max_volume=config.max_volume,
        volume_step=config.volume_step,
        allow_min_volume=config.allow_min_volume,
    )
    actual_risk_percent = _actual_risk_percent(
        account_balance=config.account_balance,
        entry=entry,
        stop_loss=stop_loss,
        contract_size=config.contract_size,
        volume=volume,
    )
    if config.max_actual_risk_percent > 0 and actual_risk_percent > config.max_actual_risk_percent:
        return AutoTradeResult(
            status="risk_blocked",
            message=(
                "Auto trade skipped because actual risk exceeds "
                f"{_format_float(config.max_actual_risk_percent)}%."
            ),
            volume=volume,
        )
    nonce = _signal_nonce(signal)

    journal_path = Path(config.journal_file)
    if _journal_has_nonce(journal_path, nonce):
        return AutoTradeResult(
            status="duplicate",
            message="Auto trade skipped because this signal nonce was already processed.",
            nonce=nonce,
            volume=volume,
        )
    if config.max_trades_per_day > 0 and _journal_trades_today(journal_path) >= config.max_trades_per_day:
        return AutoTradeResult(
            status="daily_limit",
            message="Auto trade skipped because the daily trade limit has been reached.",
            nonce=nonce,
            volume=volume,
        )

    row = {
        "nonce": nonce,
        "created_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "symbol": signal.symbol,
        "action": signal.action.value,
        "volume": _format_float(volume),
        "entry": _format_float(entry),
        "stop_loss": _format_float(stop_loss),
        "take_profit": _format_float(take_profit),
        "magic_number": str(config.magic_number),
        "comment": _clean_comment(config.comment),
        "actual_risk_percent": _format_float(actual_risk_percent),
    }

    _append_journal(journal_path, row)

    if config.mode == "paper":
        return AutoTradeResult(
            status="paper",
            message="Paper auto trade recorded in journal only.",
            nonce=nonce,
            volume=volume,
        )

    order_path = Path(config.order_file)
    _write_latest_order(order_path, row)
    return AutoTradeResult(
        status="mt5_file",
        message="MT5 order intent file was written.",
        nonce=nonce,
        volume=volume,
        order_file=str(order_path),
    )


def _required_levels(signal: Signal) -> tuple[float, float, float]:
    entry = signal.levels.entry
    stop_loss = signal.levels.stop_loss
    take_profit = signal.levels.take_profit
    if entry is None or stop_loss is None or take_profit is None:
        raise ValueError("Auto trade requires entry, stop loss, and take profit")
    if signal.action == SignalAction.BUY and not (stop_loss < entry < take_profit):
        raise ValueError("Invalid BUY levels: expected stop_loss < entry < take_profit")
    if signal.action == SignalAction.SELL and not (take_profit < entry < stop_loss):
        raise ValueError("Invalid SELL levels: expected take_profit < entry < stop_loss")
    return entry, stop_loss, take_profit


def _position_size(
    account_balance: float,
    risk_percent: float,
    entry: float,
    stop_loss: float,
    contract_size: float,
    min_volume: float,
    max_volume: float,
    volume_step: float,
    allow_min_volume: bool,
) -> float:
    risk_distance = abs(entry - stop_loss)
    if risk_distance <= 0:
        raise ValueError("Stop loss distance must be greater than zero")

    money_at_risk = account_balance * (risk_percent / 100.0)
    raw_volume = money_at_risk / (risk_distance * contract_size)
    if raw_volume < min_volume and not allow_min_volume:
        raise ValueError(
            "Calculated volume is below broker minimum volume; "
            "placing the minimum lot would exceed the configured risk per trade"
        )
    stepped_volume = math.floor(raw_volume / volume_step) * volume_step
    volume = min_volume if stepped_volume < min_volume else min(stepped_volume, max_volume)
    if volume <= 0:
        raise ValueError("Calculated volume must be greater than zero")
    return round(volume, 8)


def _actual_risk_percent(
    account_balance: float,
    entry: float,
    stop_loss: float,
    contract_size: float,
    volume: float,
) -> float:
    if account_balance <= 0:
        raise ValueError("Account balance must be greater than zero")
    risk_amount = abs(entry - stop_loss) * contract_size * volume
    return (risk_amount / account_balance) * 100.0


def _signal_nonce(signal: Signal) -> str:
    payload = "|".join(
        [
            signal.symbol,
            signal.timeframe,
            signal.action.value,
            _format_float(signal.levels.entry),
            _format_float(signal.levels.stop_loss),
            _format_float(signal.levels.take_profit),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _journal_has_nonce(path: Path, nonce: str) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return any(row.get("nonce") == nonce for row in reader)


def _journal_trades_today(path: Path) -> int:
    if not path.exists():
        return 0

    today = datetime.now(tz=UTC).date()
    count = 0
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            created_at = row.get("created_at", "")
            try:
                created_date = datetime.fromisoformat(created_at).date()
            except ValueError:
                continue
            if created_date == today:
                count += 1
    return count


def _append_journal(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=ORDER_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _write_latest_order(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=ORDER_FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)
    temporary_path.replace(path)


def _clean_comment(value: str) -> str:
    return value.replace(",", " ").replace("\n", " ").replace("\r", " ")[:31]


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.8f}".rstrip("0").rstrip(".")
