from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EVENT_SIGNAL_GENERATED = "SIGNAL_GENERATED"
EVENT_NO_TRADE = "NO_TRADE"
EVENT_EXECUTION_POLICY_REJECT = "EXECUTION_POLICY_REJECT"
EVENT_RISK_MANAGER_REJECT = "RISK_MANAGER_REJECT"
EVENT_EXECUTION_PLAN_APPROVED = "EXECUTION_PLAN_APPROVED"
EVENT_PAPER_ORDER_INTENT = "PAPER_ORDER_INTENT"
EVENT_TRADE_RESULT = "TRADE_RESULT"
EVENT_ERROR = "ERROR"
EVENT_COOLDOWN_TRIGGERED = "COOLDOWN_TRIGGERED"
EVENT_DAILY_STOP_TRIGGERED = "DAILY_STOP_TRIGGERED"

VALID_EVENT_TYPES = (
    EVENT_SIGNAL_GENERATED,
    EVENT_NO_TRADE,
    EVENT_EXECUTION_POLICY_REJECT,
    EVENT_RISK_MANAGER_REJECT,
    EVENT_EXECUTION_PLAN_APPROVED,
    EVENT_PAPER_ORDER_INTENT,
    EVENT_TRADE_RESULT,
    EVENT_ERROR,
    EVENT_COOLDOWN_TRIGGERED,
    EVENT_DAILY_STOP_TRIGGERED,
)

CSV_FIELDNAMES = (
    "timestamp",
    "event_type",
    "symbol",
    "timeframe",
    "action",
    "mode",
    "htf_bias",
    "execution_trend",
    "structure_label",
    "price_location",
    "candle_confirmation_summary",
    "entry",
    "stop_loss",
    "tp1",
    "tp2",
    "risk_reward",
    "volume",
    "approved",
    "reasons",
    "error_message",
    "trade_result",
    "pnl",
    "metadata",
)


@dataclass(frozen=True)
class JournalEvent:
    timestamp: str
    event_type: str
    symbol: str | None = None
    timeframe: str | None = None
    action: str | None = None
    mode: str | None = None
    htf_bias: str | None = None
    execution_trend: str | None = None
    structure_label: str | None = None
    price_location: str | None = None
    candle_confirmation_summary: str | None = None
    entry: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    risk_reward: float | None = None
    volume: float | None = None
    approved: bool | None = None
    reasons: tuple[str, ...] = ()
    error_message: str | None = None
    trade_result: str | None = None
    pnl: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JournalWriterConfig:
    csv_path: Path | None = None
    jsonl_path: Path | None = None
    write_csv: bool = True
    write_jsonl: bool = True


@dataclass(frozen=True)
class JournalWriteResult:
    success: bool
    csv_written: bool
    jsonl_written: bool
    error_message: str | None = None


def create_journal_event(
    event_type: str,
    *,
    timestamp: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    action: str | None = None,
    mode: str | None = None,
    htf_bias: str | None = None,
    execution_trend: str | None = None,
    structure_label: str | None = None,
    price_location: str | None = None,
    candle_confirmation_summary: str | None = None,
    entry: float | None = None,
    stop_loss: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    risk_reward: float | None = None,
    volume: float | None = None,
    approved: bool | None = None,
    reasons: tuple[str, ...] = (),
    error_message: str | None = None,
    trade_result: str | None = None,
    pnl: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> JournalEvent:
    validate_event_type(event_type)
    return JournalEvent(
        timestamp=timestamp or _utc_timestamp(),
        event_type=event_type,
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        mode=mode,
        htf_bias=htf_bias,
        execution_trend=execution_trend,
        structure_label=structure_label,
        price_location=price_location,
        candle_confirmation_summary=candle_confirmation_summary,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=risk_reward,
        volume=volume,
        approved=approved,
        reasons=tuple(reasons),
        error_message=error_message,
        trade_result=trade_result,
        pnl=pnl,
        metadata=metadata or {},
    )


def journal_event_to_dict(event: JournalEvent) -> dict[str, Any]:
    validate_event_type(event.event_type)
    _serialize_metadata(event.metadata)
    return {
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "symbol": event.symbol,
        "timeframe": event.timeframe,
        "action": event.action,
        "mode": event.mode,
        "htf_bias": event.htf_bias,
        "execution_trend": event.execution_trend,
        "structure_label": event.structure_label,
        "price_location": event.price_location,
        "candle_confirmation_summary": event.candle_confirmation_summary,
        "entry": event.entry,
        "stop_loss": event.stop_loss,
        "tp1": event.tp1,
        "tp2": event.tp2,
        "risk_reward": event.risk_reward,
        "volume": event.volume,
        "approved": event.approved,
        "reasons": list(event.reasons),
        "error_message": event.error_message,
        "trade_result": event.trade_result,
        "pnl": event.pnl,
        "metadata": event.metadata,
    }


def write_journal_event(
    event: JournalEvent,
    config: JournalWriterConfig,
) -> JournalWriteResult:
    if not config.write_csv and not config.write_jsonl:
        return JournalWriteResult(
            success=False,
            csv_written=False,
            jsonl_written=False,
            error_message="At least one journal output must be enabled",
        )

    try:
        csv_written = False
        jsonl_written = False
        if config.write_csv:
            if config.csv_path is None:
                raise ValueError("CSV journal path is required when write_csv=True")
            append_csv_event(event, config.csv_path)
            csv_written = True
        if config.write_jsonl:
            if config.jsonl_path is None:
                raise ValueError("JSONL journal path is required when write_jsonl=True")
            append_jsonl_event(event, config.jsonl_path)
            jsonl_written = True
        return JournalWriteResult(success=True, csv_written=csv_written, jsonl_written=jsonl_written)
    except Exception as exc:
        return JournalWriteResult(
            success=False,
            csv_written=False,
            jsonl_written=False,
            error_message=str(exc),
        )


def append_csv_event(
    event: JournalEvent,
    csv_path: Path,
) -> None:
    row = _event_to_csv_row(event)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def append_jsonl_event(
    event: JournalEvent,
    jsonl_path: Path,
) -> None:
    event_dict = journal_event_to_dict(event)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event_dict, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def validate_event_type(event_type: str) -> None:
    if event_type not in VALID_EVENT_TYPES:
        allowed = ", ".join(VALID_EVENT_TYPES)
        raise ValueError(f"Invalid journal event type: {event_type}. Allowed: {allowed}")


def _event_to_csv_row(event: JournalEvent) -> dict[str, str]:
    event_dict = journal_event_to_dict(event)
    return {
        "timestamp": _csv_value(event_dict["timestamp"]),
        "event_type": _csv_value(event_dict["event_type"]),
        "symbol": _csv_value(event_dict["symbol"]),
        "timeframe": _csv_value(event_dict["timeframe"]),
        "action": _csv_value(event_dict["action"]),
        "mode": _csv_value(event_dict["mode"]),
        "htf_bias": _csv_value(event_dict["htf_bias"]),
        "execution_trend": _csv_value(event_dict["execution_trend"]),
        "structure_label": _csv_value(event_dict["structure_label"]),
        "price_location": _csv_value(event_dict["price_location"]),
        "candle_confirmation_summary": _csv_value(event_dict["candle_confirmation_summary"]),
        "entry": _csv_value(event_dict["entry"]),
        "stop_loss": _csv_value(event_dict["stop_loss"]),
        "tp1": _csv_value(event_dict["tp1"]),
        "tp2": _csv_value(event_dict["tp2"]),
        "risk_reward": _csv_value(event_dict["risk_reward"]),
        "volume": _csv_value(event_dict["volume"]),
        "approved": _csv_value(event_dict["approved"]),
        "reasons": " | ".join(event.reasons),
        "error_message": _csv_value(event_dict["error_message"]),
        "trade_result": _csv_value(event_dict["trade_result"]),
        "pnl": _csv_value(event_dict["pnl"]),
        "metadata": _serialize_metadata(event.metadata),
    }


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _serialize_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
