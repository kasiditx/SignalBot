from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_TRADES_PATH = Path("logs/asian_breakout_stop8_180d_45_livefull_latest/backtest_trades.csv")
DEFAULT_OUTPUT_DIR = Path("logs/sequence_reality")


@dataclass(frozen=True)
class SequenceResult:
    label: str
    start: str
    end: str
    trades_taken: int
    trades_available: int
    wins: int
    losses: int
    net_pnl: float
    final_balance: float
    min_balance: float
    max_drawdown: float
    halted: bool
    halt_reason: str


def main() -> int:
    trades_path = Path(os.getenv("SEQUENCE_TRADES_PATH", str(DEFAULT_TRADES_PATH)))
    output_dir = Path(os.getenv("SEQUENCE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    output_dir.mkdir(parents=True, exist_ok=True)

    initial_balance = _float_env("SEQUENCE_INITIAL_BALANCE", 45.0)
    max_actual_risk_percent = _float_env("SEQUENCE_MAX_ACTUAL_RISK_PERCENT", 25.0)
    trades = _read_trades(trades_path)
    if not trades:
        raise SystemExit(f"No trades found in {trades_path}")

    risk_amount = max(abs(trade["entry"] - trade["stop_loss"]) * 100.0 * trade["volume"] for trade in trades)
    minimum_tradeable_balance = risk_amount / (max_actual_risk_percent / 100.0)

    calendar_results = _calendar_month_results(trades, initial_balance, minimum_tradeable_balance)
    start_month_results = _start_month_to_end_results(trades, initial_balance, minimum_tradeable_balance)
    all_results = [*calendar_results, *start_month_results]

    csv_path = output_dir / "sequence_reality.csv"
    json_path = output_dir / "sequence_reality_summary.json"
    _write_csv(csv_path, all_results)
    _write_json(
        json_path,
        all_results,
        trades_path,
        initial_balance,
        max_actual_risk_percent,
        minimum_tradeable_balance,
    )
    print(_format_summary(all_results, trades_path, csv_path, json_path, minimum_tradeable_balance))
    return 0


def _read_trades(path: Path) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            rows.append(
                {
                    "entry_time": row["entry_time"],
                    "month": row["entry_time"][:7].replace(".", "-"),
                    "result": row["result"],
                    "entry": float(row["entry"]),
                    "stop_loss": float(row["stop_loss"]),
                    "volume": float(row["volume"]),
                    "pnl": float(row["pnl"]),
                }
            )
    return rows


def _calendar_month_results(
    trades: list[dict[str, float | str]],
    initial_balance: float,
    minimum_tradeable_balance: float,
) -> list[SequenceResult]:
    grouped: dict[str, list[dict[str, float | str]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["month"])].append(trade)

    results: list[SequenceResult] = []
    for month, month_trades in sorted(grouped.items()):
        results.append(
            _simulate_sequence(
                label="calendar_month_reset",
                start=month,
                trades=month_trades,
                initial_balance=initial_balance,
                minimum_tradeable_balance=minimum_tradeable_balance,
            )
        )
    return results


def _start_month_to_end_results(
    trades: list[dict[str, float | str]],
    initial_balance: float,
    minimum_tradeable_balance: float,
) -> list[SequenceResult]:
    months = sorted({str(trade["month"]) for trade in trades})
    results: list[SequenceResult] = []
    for month in months:
        month_trades = [trade for trade in trades if str(trade["month"]) >= month]
        results.append(
            _simulate_sequence(
                label="start_month_to_end",
                start=month,
                trades=month_trades,
                initial_balance=initial_balance,
                minimum_tradeable_balance=minimum_tradeable_balance,
            )
        )
    return results


def _simulate_sequence(
    *,
    label: str,
    start: str,
    trades: list[dict[str, float | str]],
    initial_balance: float,
    minimum_tradeable_balance: float,
) -> SequenceResult:
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0.0
    trades_taken = 0
    wins = 0
    losses = 0
    halted = False
    halt_reason = ""

    for trade in trades:
        if balance < minimum_tradeable_balance:
            halted = True
            halt_reason = "balance below minimum required for capped 0.01 lot risk"
            break

        pnl = float(trade["pnl"])
        balance += pnl
        trades_taken += 1
        wins += str(trade["result"]) == "WIN"
        losses += str(trade["result"]).startswith("LOSS")
        peak_balance = max(peak_balance, balance)
        max_drawdown = max(max_drawdown, peak_balance - balance)

    if balance < minimum_tradeable_balance and not halted:
        halted = True
        halt_reason = "ending balance below minimum required for next trade"

    end = str(trades[-1]["month"]) if trades else start
    return SequenceResult(
        label=label,
        start=start,
        end=end,
        trades_taken=trades_taken,
        trades_available=len(trades),
        wins=wins,
        losses=losses,
        net_pnl=round(balance - initial_balance, 2),
        final_balance=round(balance, 2),
        min_balance=round(min(initial_balance, balance), 2),
        max_drawdown=round(max_drawdown, 2),
        halted=halted,
        halt_reason=halt_reason,
    )


def _write_csv(path: Path, results: list[SequenceResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(SequenceResult.__dataclass_fields__))
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def _write_json(
    path: Path,
    results: list[SequenceResult],
    trades_path: Path,
    initial_balance: float,
    max_actual_risk_percent: float,
    minimum_tradeable_balance: float,
) -> None:
    payload = {
        "source_trades": str(trades_path),
        "initial_balance": initial_balance,
        "max_actual_risk_percent": max_actual_risk_percent,
        "minimum_tradeable_balance": minimum_tradeable_balance,
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_summary(
    results: list[SequenceResult],
    trades_path: Path,
    csv_path: Path,
    json_path: Path,
    minimum_tradeable_balance: float,
) -> str:
    lines = [
        "Sequence Reality Check",
        f"Source: {trades_path}",
        f"Minimum balance to continue trading: {minimum_tradeable_balance:.2f}",
        "",
        "Calendar Months:",
    ]
    for result in [item for item in results if item.label == "calendar_month_reset"]:
        lines.append(_format_result(result))

    lines.append("")
    lines.append("Start Month To End:")
    for result in [item for item in results if item.label == "start_month_to_end"]:
        lines.append(_format_result(result))

    lines.extend(["", f"CSV: {csv_path}", f"JSON: {json_path}"])
    return "\n".join(lines)


def _format_result(result: SequenceResult) -> str:
    status = "HALT" if result.halted else "PASS"
    return (
        "- "
        f"{result.start}->{result.end}: {status}, "
        f"taken={result.trades_taken}/{result.trades_available}, "
        f"W/L={result.wins}/{result.losses}, "
        f"pnl={result.net_pnl:.2f}, "
        f"final={result.final_balance:.2f}, "
        f"dd={result.max_drawdown:.2f}"
    )


def _float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


if __name__ == "__main__":
    raise SystemExit(main())
