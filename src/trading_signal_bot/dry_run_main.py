from __future__ import annotations

import os
import sys

from .config import load_auto_trade_config, load_env_file, load_signal_config
from .dry_run_pipeline import DryRunMarketInput
from .models import SignalAction
from .multitimeframe import load_timeframe_candles
from .pipeline_adapter import DryRunAdapterInput, DryRunAdapterResult, run_pipeline_from_configs


NO_ORDER_TEXT = "No order was sent. No MT5 order intent was written. Dry-run only."


def parse_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"Expected a numeric value, got: {value!r}") from exc


def parse_optional_action(value: str | None) -> SignalAction | None:
    if value is None or not value.strip():
        return None

    normalized = value.strip().upper()
    if normalized == "WAIT":
        return None
    if normalized in {SignalAction.BUY.value, SignalAction.SELL.value}:
        return SignalAction(normalized)
    raise ValueError("DRY_RUN_ACTION must be BUY, SELL, WAIT, or empty")


def build_adapter_input_from_env() -> DryRunAdapterInput:
    return DryRunAdapterInput(
        action=parse_optional_action(os.getenv("DRY_RUN_ACTION")),
        entry=parse_optional_float(os.getenv("DRY_RUN_ENTRY")),
        stop_loss=parse_optional_float(os.getenv("DRY_RUN_STOP_LOSS")),
        tp1=parse_optional_float(os.getenv("DRY_RUN_TP1")),
        tp2=parse_optional_float(os.getenv("DRY_RUN_TP2")),
        risk_reward=parse_optional_float(os.getenv("DRY_RUN_RISK_REWARD")),
        mode=os.getenv("DRY_RUN_MODE", "paper").strip().lower() or "paper",
    )


def build_market_input_from_env() -> DryRunMarketInput:
    current_price = parse_optional_float(os.getenv("DRY_RUN_CURRENT_PRICE"))
    if current_price is None:
        raise ValueError("DRY_RUN_CURRENT_PRICE is required for dry-run market snapshot")

    return DryRunMarketInput(
        current_price=current_price,
        spread_points=parse_optional_float(os.getenv("DRY_RUN_SPREAD_POINTS")),
        atr_value=parse_optional_float(os.getenv("DRY_RUN_ATR_VALUE")),
        average_atr=parse_optional_float(os.getenv("DRY_RUN_AVERAGE_ATR")),
        session=(os.getenv("DRY_RUN_SESSION") or "").strip() or None,
        high_impact_news_nearby=_parse_bool(os.getenv("DRY_RUN_HIGH_IMPACT_NEWS_NEARBY"), False),
    )


def format_dry_run_summary(result: DryRunAdapterResult) -> str:
    pipeline_result = result.pipeline_result
    lines = [
        "Dry-run pipeline summary",
        f"approved={pipeline_result.approved}",
        f"stage={pipeline_result.stage}",
        f"reasons={_format_reasons(pipeline_result.reasons)}",
        result.message,
        NO_ORDER_TEXT,
    ]

    if pipeline_result.execution_plan is not None:
        plan = pipeline_result.execution_plan
        lines.extend(
            [
                f"action={plan.action.value}",
                f"entry={plan.entry}",
                f"stop_loss={plan.stop_loss}",
                f"tp1={plan.tp1}",
                f"tp2={plan.tp2}",
                f"break_even_trigger={plan.break_even_trigger}",
                f"trailing_stop_enabled={plan.trailing_stop_enabled}",
                f"partial_close_enabled={plan.partial_close_enabled}",
            ]
        )
    if pipeline_result.risk_decision is not None:
        risk = pipeline_result.risk_decision
        lines.extend(
            [
                f"risk_approved={risk.approved}",
                f"volume={risk.volume}",
                f"money_at_risk={risk.money_at_risk}",
                f"risk_percent={risk.risk_percent}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    try:
        load_env_file()
        signal_config = load_signal_config()
        auto_trade_config = load_auto_trade_config()
        candles_by_timeframe = load_timeframe_candles(signal_config)
        adapter_input = build_adapter_input_from_env()
        market_input = build_market_input_from_env()

        preflight_reasons = _adapter_preflight_reasons(adapter_input)
        if preflight_reasons:
            print(_format_preflight_rejection(preflight_reasons, adapter_input.mode))
            return 0

        result = run_pipeline_from_configs(
            candles_by_timeframe=candles_by_timeframe,
            signal_config=signal_config,
            auto_trade_config=auto_trade_config,
            adapter_input=adapter_input,
            market_input=market_input,
        )
        print(format_dry_run_summary(result))
        return 0
    except Exception as exc:
        print("Dry-run failed before pipeline execution")
        print(f"error={exc}")
        print(NO_ORDER_TEXT)
        return 1


def _adapter_preflight_reasons(adapter_input: DryRunAdapterInput) -> tuple[str, ...]:
    if adapter_input.mode.strip().lower() == "live":
        return ()

    reasons: list[str] = []
    if adapter_input.action is None:
        reasons.append("DRY_RUN_ACTION is empty; no trade action candidate was provided")
    if adapter_input.entry is None:
        reasons.append("DRY_RUN_ENTRY is required before position sizing can be evaluated")
    if adapter_input.stop_loss is None:
        reasons.append("DRY_RUN_STOP_LOSS is required before position sizing can be evaluated")
    if adapter_input.tp1 is None:
        reasons.append("DRY_RUN_TP1 is missing; execution policy would reject the candidate")
    if adapter_input.tp2 is None:
        reasons.append("DRY_RUN_TP2 is missing; execution policy would reject the candidate")
    if adapter_input.risk_reward is None:
        reasons.append("DRY_RUN_RISK_REWARD is missing; risk manager would reject the candidate")
    return tuple(reasons)


def _format_preflight_rejection(reasons: tuple[str, ...], mode: str) -> str:
    return "\n".join(
        [
            "Dry-run pipeline summary",
            "approved=False",
            "stage=adapter_preflight",
            f"mode={mode}",
            f"reasons={_format_reasons(reasons)}",
            "Dry-run rejected before adapter execution because required dry-run inputs are incomplete.",
            NO_ORDER_TEXT,
        ]
    )


def _format_reasons(reasons: tuple[str, ...]) -> str:
    if not reasons:
        return "none"
    return " | ".join(reasons)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    sys.exit(main())
