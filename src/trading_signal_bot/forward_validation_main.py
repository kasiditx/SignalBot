from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from .config import load_env_file, load_signal_config
from .dry_run_pipeline import DryRunMarketInput
from .forward_validation import (
    ForwardValidationConfig,
    ForwardValidationInput,
    ForwardValidationResult,
    load_forward_records_jsonl,
    run_forward_validation,
    write_forward_summaries,
)
from .models import AutoTradeConfig, SignalAction, SignalConfig
from .multitimeframe import load_timeframe_candles
from .pipeline_adapter import DryRunAdapterInput, run_pipeline_from_configs


DEFAULT_OUTPUT_DIR = Path("logs/forward_validation")
SAFETY_TEXT = "No order was sent.\nNo MT5 order intent was written.\nForward dry-run only."


def parse_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Expected a numeric value, got: {value}") from exc


def parse_optional_action(value: str | None) -> SignalAction | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().upper()
    if normalized == SignalAction.BUY.value:
        return SignalAction.BUY
    if normalized == SignalAction.SELL.value:
        return SignalAction.SELL
    raise ValueError("FORWARD_ACTION must be BUY, SELL, or empty")


def parse_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError("Boolean env value must be true or false")


def forward_validation_output_dir_from_env() -> Path:
    raw_path = os.getenv("FORWARD_VALIDATION_OUTPUT_DIR")
    if raw_path is None or not raw_path.strip():
        return DEFAULT_OUTPUT_DIR
    return Path(raw_path.strip())


def build_forward_validation_config_from_env() -> ForwardValidationConfig:
    output_dir = forward_validation_output_dir_from_env()
    return ForwardValidationConfig(
        record_csv_path=output_dir / "forward_records.csv",
        record_jsonl_path=output_dir / "forward_records.jsonl",
        daily_summary_path=output_dir / "daily_summary.csv",
        weekly_summary_path=output_dir / "weekly_summary.csv",
        write_csv=True,
        write_jsonl=True,
    )


def update_forward_summaries(config: ForwardValidationConfig) -> bool:
    records = load_forward_records_jsonl(config.record_jsonl_path)
    write_forward_summaries(records, config)
    return True


def build_forward_validation_input_from_env(
    signal_config: SignalConfig,
) -> ForwardValidationInput:
    current_price = parse_optional_float(os.getenv("FORWARD_CURRENT_PRICE"))
    spread_points = parse_optional_float(os.getenv("FORWARD_SPREAD_POINTS"))
    session = _optional_text(os.getenv("FORWARD_SESSION"))
    atr_value = parse_optional_float(os.getenv("FORWARD_ATR_VALUE"))
    average_atr = parse_optional_float(os.getenv("FORWARD_AVERAGE_ATR"))
    high_impact_news_nearby = parse_bool_env(os.getenv("FORWARD_HIGH_IMPACT_NEWS_NEARBY"), False)

    return ForwardValidationInput(
        symbol=signal_config.symbol,
        mode=os.getenv("FORWARD_VALIDATION_MODE", "paper").strip().lower() or "paper",
        action=parse_optional_action(os.getenv("FORWARD_ACTION")),
        entry=parse_optional_float(os.getenv("FORWARD_ENTRY")),
        stop_loss=parse_optional_float(os.getenv("FORWARD_STOP_LOSS")),
        tp1=parse_optional_float(os.getenv("FORWARD_TP1")),
        tp2=parse_optional_float(os.getenv("FORWARD_TP2")),
        risk_reward=parse_optional_float(os.getenv("FORWARD_RISK_REWARD")),
        current_price=current_price,
        spread_points=spread_points,
        session=session,
        metadata={
            "current_price": current_price,
            "spread_points": spread_points,
            "session": session,
            "atr_value": atr_value,
            "average_atr": average_atr,
            "high_impact_news_nearby": high_impact_news_nearby,
        },
    )


def build_dry_run_adapter_input_from_forward_input(
    validation_input: ForwardValidationInput,
) -> DryRunAdapterInput:
    return DryRunAdapterInput(
        action=validation_input.action,
        entry=validation_input.entry,
        stop_loss=validation_input.stop_loss,
        tp1=validation_input.tp1,
        tp2=validation_input.tp2,
        risk_reward=validation_input.risk_reward,
        mode=validation_input.mode,
    )


def build_dry_run_market_input_from_env() -> DryRunMarketInput:
    current_price = parse_optional_float(os.getenv("FORWARD_CURRENT_PRICE"))
    if current_price is None:
        raise ValueError("FORWARD_CURRENT_PRICE is required for forward validation")

    return DryRunMarketInput(
        current_price=current_price,
        spread_points=parse_optional_float(os.getenv("FORWARD_SPREAD_POINTS")),
        atr_value=parse_optional_float(os.getenv("FORWARD_ATR_VALUE")),
        average_atr=parse_optional_float(os.getenv("FORWARD_AVERAGE_ATR")),
        session=_optional_text(os.getenv("FORWARD_SESSION")),
        high_impact_news_nearby=parse_bool_env(os.getenv("FORWARD_HIGH_IMPACT_NEWS_NEARBY"), False),
    )


def format_forward_validation_summary(
    result: ForwardValidationResult,
    output_dir: Path,
    summaries_written: bool = False,
) -> str:
    reasons = " | ".join(result.record.reasons) if result.record.reasons else "None"
    summary_status = "written" if summaries_written else "not written"
    return "\n".join(
        [
            "Forward Validation Summary",
            f"Stage: {result.record.stage}",
            f"Approved: {result.record.approved}",
            f"Reasons: {reasons}",
            f"Record written: {result.write_success}",
            f"Daily/weekly summaries: {summary_status}",
            f"Output directory: {output_dir}",
            SAFETY_TEXT,
        ]
    )


def main() -> int:
    try:
        load_env_file()
        signal_config = load_signal_config()
        candles_by_timeframe = load_timeframe_candles(signal_config)
        validation_input = build_forward_validation_input_from_env(signal_config)
        validation_config = build_forward_validation_config_from_env()
        output_dir = forward_validation_output_dir_from_env()

        if validation_input.mode == "live":
            result = run_forward_validation(
                validation_input,
                SimpleNamespace(),
                validation_config,
            )
            summaries_written = False
            if result.write_success:
                summaries_written = update_forward_summaries(validation_config)
            print(format_forward_validation_summary(result, output_dir, summaries_written))
            return 0 if result.write_success and summaries_written else 1

        adapter_result = run_pipeline_from_configs(
            candles_by_timeframe,
            signal_config,
            _paper_sizing_config(),
            build_dry_run_adapter_input_from_forward_input(validation_input),
            build_dry_run_market_input_from_env(),
        )
        result = run_forward_validation(
            validation_input,
            adapter_result.pipeline_result,
            validation_config,
        )
        summaries_written = False
        if result.write_success:
            summaries_written = update_forward_summaries(validation_config)
        print(format_forward_validation_summary(result, output_dir, summaries_written))
        return 0 if result.write_success and summaries_written else 1
    except Exception as exc:
        print("Forward validation failed")
        print(f"error={exc}")
        print(SAFETY_TEXT)
        return 1


def _paper_sizing_config() -> AutoTradeConfig:
    return AutoTradeConfig(
        False,
        "paper",
        "logs/forward_validation/disabled.csv",
        "logs/forward_validation/journal.csv",
        10000.0,
        1.0,
        100.0,
        0.01,
        10.0,
        0.01,
        True,
        20260521,
        "ForwardValidation",
    )


def _optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


if __name__ == "__main__":
    sys.exit(main())
