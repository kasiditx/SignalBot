from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import SignalAction


CSV_FIELDNAMES = (
    "timestamp",
    "symbol",
    "mode",
    "action",
    "stage",
    "approved",
    "reasons",
    "entry",
    "stop_loss",
    "tp1",
    "tp2",
    "risk_reward",
    "execution_plan_present",
    "risk_decision_present",
    "order_sent",
    "order_intent_written",
    "journal_success",
    "metadata",
)

DAILY_SUMMARY_FIELDNAMES = (
    "date",
    "total_runs",
    "approved_count",
    "rejected_count",
    "no_trade_count",
    "execution_reject_count",
    "risk_reject_count",
    "paper_intent_count",
    "journal_failures",
    "order_sent_count",
    "order_intent_written_count",
    "top_reason",
    "reason_summary",
)

WEEKLY_SUMMARY_FIELDNAMES = (
    "week_start",
    "week_end",
    "iso_year",
    "iso_week",
    "total_runs",
    "approved_count",
    "rejected_count",
    "no_trade_count",
    "execution_reject_count",
    "risk_reject_count",
    "paper_intent_count",
    "journal_failures",
    "order_sent_count",
    "order_intent_written_count",
    "top_reason",
    "reason_summary",
)

FORWARD_JSONL_ENCODING = "utf-8"
FORWARD_JSONL_READ_ENCODING = "utf-8-sig"
FORWARD_CSV_ENCODING = "utf-8-sig"

RISK_REJECT_STAGE = "risk_manager"
EXECUTION_REJECT_STAGE = "execution_policy"
NO_TRADE_STAGE = "no_trade_filter"
PAPER_INTENT_STAGE = "approved"


@dataclass(frozen=True)
class ForwardValidationInput:
    symbol: str
    mode: str
    action: SignalAction | None
    entry: float | None
    stop_loss: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    current_price: float | None
    spread_points: float | None
    session: str | None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardValidationConfig:
    record_csv_path: Path
    record_jsonl_path: Path
    daily_summary_path: Path
    weekly_summary_path: Path
    write_csv: bool = True
    write_jsonl: bool = True


@dataclass(frozen=True)
class ForwardValidationRecord:
    timestamp: str
    symbol: str
    mode: str
    action: str | None
    stage: str
    approved: bool
    reasons: tuple[str, ...]
    entry: float | None
    stop_loss: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    execution_plan_present: bool
    risk_decision_present: bool
    order_sent: bool
    order_intent_written: bool
    journal_success: bool
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardValidationResult:
    record: ForwardValidationRecord
    pipeline_result: object | None
    write_success: bool
    error_message: str | None


@dataclass(frozen=True)
class ForwardSummary:
    total_runs: int
    approved_count: int
    rejected_count: int
    no_trade_count: int
    execution_reject_count: int
    risk_reject_count: int
    paper_intent_count: int
    journal_failures: int
    order_sent_count: int
    order_intent_written_count: int
    reason_summary: dict[str, int]


def forward_record_from_pipeline_result(
    validation_input: ForwardValidationInput,
    pipeline_result: object,
    timestamp: str | None = None,
) -> ForwardValidationRecord:
    if validation_input.mode.lower() == "live":
        return _record_from_input(
            validation_input,
            timestamp=timestamp,
            stage="mode_validation",
            approved=False,
            reasons=("live mode is not allowed",),
            execution_plan_present=False,
            risk_decision_present=False,
            journal_success=True,
        )

    return _record_from_input(
        validation_input,
        timestamp=timestamp,
        stage=_object_attr(pipeline_result, "stage", "unknown"),
        approved=bool(_object_attr(pipeline_result, "approved", False)),
        reasons=_reasons_from_pipeline_result(pipeline_result),
        execution_plan_present=_object_attr(pipeline_result, "execution_plan", None) is not None,
        risk_decision_present=_object_attr(pipeline_result, "risk_decision", None) is not None,
        journal_success=_journal_results_success(_object_attr(pipeline_result, "journal_results", None)),
    )


def write_forward_record_csv(
    record: ForwardValidationRecord,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding=FORWARD_CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(_record_to_csv_row(record))


def append_forward_record_jsonl(
    record: ForwardValidationRecord,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=FORWARD_JSONL_ENCODING) as file:
        file.write(json.dumps(_record_to_dict(record), ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def write_forward_record(
    record: ForwardValidationRecord,
    config: ForwardValidationConfig,
) -> bool:
    if not config.write_csv and not config.write_jsonl:
        return False
    try:
        if config.write_csv:
            write_forward_record_csv(record, config.record_csv_path)
        if config.write_jsonl:
            append_forward_record_jsonl(record, config.record_jsonl_path)
        return True
    except Exception:
        return False


def summarize_forward_records_daily(
    records: tuple[ForwardValidationRecord, ...],
) -> ForwardSummary:
    return _summarize_forward_records(records)


def summarize_forward_records_weekly(
    records: tuple[ForwardValidationRecord, ...],
) -> ForwardSummary:
    return _summarize_forward_records(records)


def run_forward_validation(
    validation_input: ForwardValidationInput,
    pipeline_result: object,
    config: ForwardValidationConfig,
) -> ForwardValidationResult:
    try:
        record = forward_record_from_pipeline_result(validation_input, pipeline_result)
    except Exception as exc:
        record = _record_from_input(
            validation_input,
            timestamp=None,
            stage="validation_error",
            approved=False,
            reasons=(str(exc),),
            execution_plan_present=False,
            risk_decision_present=False,
            journal_success=False,
        )
        return ForwardValidationResult(
            record=record,
            pipeline_result=pipeline_result,
            write_success=False,
            error_message=str(exc),
        )

    write_success = write_forward_record(record, config)
    return ForwardValidationResult(
        record=record,
        pipeline_result=pipeline_result,
        write_success=write_success,
        error_message=None if write_success else "forward record write failed",
    )


def forward_record_from_dict(
    payload: dict[str, object],
) -> ForwardValidationRecord:
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("Forward validation record timestamp is required")

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    return ForwardValidationRecord(
        timestamp=timestamp,
        symbol=_payload_str(payload, "symbol"),
        mode=_payload_str(payload, "mode", "paper"),
        action=_payload_optional_str(payload, "action"),
        stage=_payload_str(payload, "stage", "unknown"),
        approved=bool(payload.get("approved", False)),
        reasons=_payload_reasons(payload.get("reasons", ())),
        entry=_payload_optional_float(payload, "entry"),
        stop_loss=_payload_optional_float(payload, "stop_loss"),
        tp1=_payload_optional_float(payload, "tp1"),
        tp2=_payload_optional_float(payload, "tp2"),
        risk_reward=_payload_optional_float(payload, "risk_reward"),
        execution_plan_present=bool(payload.get("execution_plan_present", False)),
        risk_decision_present=bool(payload.get("risk_decision_present", False)),
        order_sent=bool(payload.get("order_sent", False)),
        order_intent_written=bool(payload.get("order_intent_written", False)),
        journal_success=bool(payload.get("journal_success", False)),
        metadata={str(key): value for key, value in metadata.items()},
    )


def load_forward_records_jsonl(
    path: Path,
) -> tuple[ForwardValidationRecord, ...]:
    if not path.exists():
        return ()

    records: list[ForwardValidationRecord] = []
    with path.open("r", encoding=FORWARD_JSONL_READ_ENCODING) as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL forward record at line {line_number}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"Forward record at line {line_number} must be a JSON object")
            records.append(forward_record_from_dict(payload))
    return tuple(records)


def _parse_forward_record_timestamp(
    record: ForwardValidationRecord,
) -> datetime:
    try:
        return datetime.fromisoformat(record.timestamp)
    except ValueError as exc:
        raise ValueError(f"Invalid forward record timestamp: {record.timestamp}") from exc


def _top_reason(
    reason_summary: dict[str, int],
) -> str:
    if not reason_summary:
        return ""
    return sorted(reason_summary.items(), key=lambda item: (-item[1], item[0]))[0][0]


def write_forward_daily_summary_csv(
    records: tuple[ForwardValidationRecord, ...],
    path: Path,
) -> None:
    grouped: dict[str, list[ForwardValidationRecord]] = {}
    for record in records:
        date_key = _parse_forward_record_timestamp(record).date().isoformat()
        grouped.setdefault(date_key, []).append(record)

    rows = [
        {
            "date": date_key,
            **_summary_to_csv_row(summarize_forward_records_daily(tuple(grouped[date_key]))),
        }
        for date_key in sorted(grouped)
    ]
    _write_summary_csv(path, list(DAILY_SUMMARY_FIELDNAMES), rows)


def write_forward_weekly_summary_csv(
    records: tuple[ForwardValidationRecord, ...],
    path: Path,
) -> None:
    grouped: dict[tuple[int, int], list[ForwardValidationRecord]] = {}
    for record in records:
        record_date = _parse_forward_record_timestamp(record).date()
        iso_year, iso_week, _ = record_date.isocalendar()
        grouped.setdefault((iso_year, iso_week), []).append(record)

    rows: list[dict[str, object]] = []
    for iso_year, iso_week in sorted(grouped):
        week_start = datetime.fromisocalendar(iso_year, iso_week, 1).date()
        week_end = week_start + timedelta(days=6)
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "iso_year": iso_year,
                "iso_week": iso_week,
                **_summary_to_csv_row(summarize_forward_records_weekly(tuple(grouped[(iso_year, iso_week)]))),
            }
        )
    _write_summary_csv(path, list(WEEKLY_SUMMARY_FIELDNAMES), rows)


def write_forward_summaries(
    records: tuple[ForwardValidationRecord, ...],
    config: ForwardValidationConfig,
) -> None:
    write_forward_daily_summary_csv(records, config.daily_summary_path)
    write_forward_weekly_summary_csv(records, config.weekly_summary_path)


def _record_from_input(
    validation_input: ForwardValidationInput,
    *,
    timestamp: str | None,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
    execution_plan_present: bool,
    risk_decision_present: bool,
    journal_success: bool,
) -> ForwardValidationRecord:
    return ForwardValidationRecord(
        timestamp=timestamp or _utc_timestamp(),
        symbol=validation_input.symbol,
        mode=validation_input.mode,
        action=validation_input.action.value if validation_input.action is not None else None,
        stage=stage,
        approved=approved,
        reasons=tuple(str(reason) for reason in reasons),
        entry=validation_input.entry,
        stop_loss=validation_input.stop_loss,
        tp1=validation_input.tp1,
        tp2=validation_input.tp2,
        risk_reward=validation_input.risk_reward,
        execution_plan_present=execution_plan_present,
        risk_decision_present=risk_decision_present,
        order_sent=False,
        order_intent_written=False,
        journal_success=journal_success,
        metadata=dict(validation_input.metadata),
    )


def _summarize_forward_records(
    records: tuple[ForwardValidationRecord, ...],
) -> ForwardSummary:
    reason_summary: dict[str, int] = {}
    for record in records:
        for reason in record.reasons:
            reason_summary[reason] = reason_summary.get(reason, 0) + 1

    return ForwardSummary(
        total_runs=len(records),
        approved_count=sum(1 for record in records if record.approved),
        rejected_count=sum(1 for record in records if not record.approved),
        no_trade_count=sum(1 for record in records if record.stage == NO_TRADE_STAGE),
        execution_reject_count=sum(1 for record in records if record.stage == EXECUTION_REJECT_STAGE),
        risk_reject_count=sum(1 for record in records if record.stage == RISK_REJECT_STAGE),
        paper_intent_count=sum(1 for record in records if record.stage == PAPER_INTENT_STAGE and record.approved),
        journal_failures=sum(1 for record in records if not record.journal_success),
        order_sent_count=sum(1 for record in records if record.order_sent),
        order_intent_written_count=sum(1 for record in records if record.order_intent_written),
        reason_summary=reason_summary,
    )


def _record_to_dict(record: ForwardValidationRecord) -> dict[str, Any]:
    _serialize_metadata(record.metadata)
    return {
        "timestamp": record.timestamp,
        "symbol": record.symbol,
        "mode": record.mode,
        "action": record.action,
        "stage": record.stage,
        "approved": record.approved,
        "reasons": list(record.reasons),
        "entry": record.entry,
        "stop_loss": record.stop_loss,
        "tp1": record.tp1,
        "tp2": record.tp2,
        "risk_reward": record.risk_reward,
        "execution_plan_present": record.execution_plan_present,
        "risk_decision_present": record.risk_decision_present,
        "order_sent": record.order_sent,
        "order_intent_written": record.order_intent_written,
        "journal_success": record.journal_success,
        "metadata": record.metadata,
    }


def _record_to_csv_row(record: ForwardValidationRecord) -> dict[str, str]:
    record_dict = _record_to_dict(record)
    return {
        "timestamp": _csv_value(record_dict["timestamp"]),
        "symbol": _csv_value(record_dict["symbol"]),
        "mode": _csv_value(record_dict["mode"]),
        "action": _csv_value(record_dict["action"]),
        "stage": _csv_value(record_dict["stage"]),
        "approved": _csv_value(record_dict["approved"]),
        "reasons": " | ".join(record.reasons),
        "entry": _csv_value(record_dict["entry"]),
        "stop_loss": _csv_value(record_dict["stop_loss"]),
        "tp1": _csv_value(record_dict["tp1"]),
        "tp2": _csv_value(record_dict["tp2"]),
        "risk_reward": _csv_value(record_dict["risk_reward"]),
        "execution_plan_present": _csv_value(record_dict["execution_plan_present"]),
        "risk_decision_present": _csv_value(record_dict["risk_decision_present"]),
        "order_sent": _csv_value(record_dict["order_sent"]),
        "order_intent_written": _csv_value(record_dict["order_intent_written"]),
        "journal_success": _csv_value(record_dict["journal_success"]),
        "metadata": _serialize_metadata(record.metadata),
    }


def _summary_to_csv_row(summary: ForwardSummary) -> dict[str, object]:
    return {
        "total_runs": summary.total_runs,
        "approved_count": summary.approved_count,
        "rejected_count": summary.rejected_count,
        "no_trade_count": summary.no_trade_count,
        "execution_reject_count": summary.execution_reject_count,
        "risk_reject_count": summary.risk_reject_count,
        "paper_intent_count": summary.paper_intent_count,
        "journal_failures": summary.journal_failures,
        "order_sent_count": summary.order_sent_count,
        "order_intent_written_count": summary.order_intent_written_count,
        "top_reason": _top_reason(summary.reason_summary),
        "reason_summary": _serialize_metadata(summary.reason_summary),
    }


def _write_summary_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=FORWARD_CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _payload_reasons(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(reason) for reason in value)  # type: ignore[operator]
    except TypeError:
        return (str(value),)


def _payload_str(payload: dict[str, object], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    return str(value)


def _payload_optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _payload_optional_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Forward validation record field {key} must be numeric") from exc


def _journal_results_success(journal_results: object) -> bool:
    if journal_results is None:
        return True
    try:
        results = tuple(journal_results)  # type: ignore[arg-type]
    except TypeError:
        results = (journal_results,)
    return all(bool(_object_attr(result, "success", False)) for result in results)


def _reasons_from_pipeline_result(pipeline_result: object) -> tuple[str, ...]:
    reasons = _object_attr(pipeline_result, "reasons", ())
    if reasons is None:
        return ()
    if isinstance(reasons, str):
        return (reasons,)
    try:
        return tuple(str(reason) for reason in reasons)
    except TypeError:
        return (str(reasons),)


def _object_attr(source: object, name: str, default: Any) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _serialize_metadata(metadata: dict[str, object]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
