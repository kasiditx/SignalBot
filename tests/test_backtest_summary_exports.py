from __future__ import annotations

import ast
import csv
import json
import tempfile
import unittest
from pathlib import Path

from trading_signal_bot.backtest import (
    BacktestDecision,
    BacktestMetrics,
    BacktestRealismConfig,
    BacktestReport,
    BacktestTradeResult,
    calculate_backtest_metrics,
    calculate_session_metrics,
    export_backtest_cost_summary_csv,
    export_backtest_realism_summary_csv,
    export_backtest_report,
    export_backtest_risk_skip_summary_csv,
    export_backtest_session_pnl_summary_csv,
    export_enhanced_backtest_summary_files,
    export_enhanced_backtest_summary_json,
    summarize_reject_reasons,
    summarize_skip_reasons,
)
from trading_signal_bot.models import SignalAction


class BacktestSummaryExportsTest(unittest.TestCase):
    def test_export_enhanced_summary_json_writes_parseable_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "enhanced_backtest_summary.json"

            export_enhanced_backtest_summary_json(_sample_report(), path, realism=_realism_config(), mode="realism")

            self.assertTrue(path.exists())
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_export_enhanced_summary_json_has_legacy_keys(self) -> None:
        payload = _enhanced_json_payload(realism=_realism_config(), mode="realism")

        for key in (
            "metrics",
            "session_metrics",
            "reject_reason_summary",
            "skip_reason_summary",
            "stopped_reason",
        ):
            self.assertIn(key, payload)

    def test_export_enhanced_summary_json_has_new_keys(self) -> None:
        payload = _enhanced_json_payload(realism=_realism_config(), mode="realism")

        for key in (
            "mode",
            "trade_performance",
            "risk_skip_summary",
            "session_pnl_summary",
        ):
            self.assertIn(key, payload)

    def test_export_enhanced_summary_json_with_realism_has_balance_and_cost_keys(self) -> None:
        payload = _enhanced_json_payload(realism=_realism_config(), mode="realism")

        self.assertIn("balance_performance", payload)
        self.assertIn("cost_summary", payload)

    def test_export_enhanced_summary_json_without_realism_omits_balance_and_cost_keys(self) -> None:
        payload = _enhanced_json_payload(realism=None, mode="simulation")

        self.assertNotIn("balance_performance", payload)
        self.assertNotIn("cost_summary", payload)

    def test_export_backtest_realism_summary_csv_writes_expected_header_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_realism_summary.csv"

            export_backtest_realism_summary_csv(_sample_report(), path, _realism_config())

            rows = _csv_rows(path)
            self.assertEqual(
                _csv_header(path),
                [
                    "initial_balance",
                    "final_balance",
                    "net_pnl",
                    "return_percent",
                    "max_drawdown",
                    "total_trades",
                    "wins",
                    "losses",
                    "open_at_end",
                    "loss_both_hit",
                    "win_rate",
                    "profit_factor",
                    "net_r",
                    "average_win",
                    "average_loss",
                    "average_rr",
                ],
            )
            self.assertEqual(rows[0]["initial_balance"], "10000.0")
            self.assertEqual(rows[0]["final_balance"], "10080.0")
            self.assertEqual(rows[0]["net_pnl"], "80.0")
            self.assertEqual(rows[0]["total_trades"], "4")
            self.assertEqual(rows[0]["wins"], "1")
            self.assertEqual(rows[0]["losses"], "1")
            self.assertEqual(rows[0]["open_at_end"], "1")
            self.assertEqual(rows[0]["loss_both_hit"], "1")

    def test_export_backtest_risk_skip_summary_csv_writes_reason_rows_with_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_risk_skip_summary.csv"

            export_backtest_risk_skip_summary_csv(_sample_report(), path)

            rows = _csv_rows(path)
            self.assertEqual(_csv_header(path), ["reason", "count"])
            self.assertEqual(
                [row["reason"] for row in rows],
                [
                    "cooldown active",
                    "daily risk stopped for day",
                    "max daily loss reached",
                    "max consecutive losses reached",
                    "weekly loss pause active",
                ],
            )
            self.assertEqual({row["reason"]: row["count"] for row in rows}["cooldown active"], "2")
            self.assertEqual({row["reason"]: row["count"] for row in rows}["max daily loss reached"], "1")

    def test_export_backtest_risk_skip_summary_csv_empty_report_has_zero_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_risk_skip_summary.csv"

            export_backtest_risk_skip_summary_csv(_empty_report(), path)

            rows = _csv_rows(path)
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row["count"] == "0" for row in rows))

    def test_export_backtest_cost_summary_csv_writes_expected_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_cost_summary.csv"

            export_backtest_cost_summary_csv(_sample_report(), path, _realism_config())

            rows = _csv_rows(path)
            self.assertEqual(
                _csv_header(path),
                ["total_commission", "total_spread_cost", "total_slippage_cost", "total_cost"],
            )
            self.assertEqual(rows[0]["total_commission"], "21.0")
            self.assertEqual(rows[0]["total_spread_cost"], "60.0")
            self.assertEqual(rows[0]["total_slippage_cost"], "15.0")
            self.assertEqual(rows[0]["total_cost"], "96.0")

    def test_export_backtest_session_pnl_summary_csv_writes_four_sessions_with_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backtest_session_pnl_summary.csv"

            export_backtest_session_pnl_summary_csv(_sample_report(), path)

            rows = _csv_rows(path)
            self.assertEqual(
                _csv_header(path),
                ["session", "trades", "wins", "losses", "net_pnl", "average_pnl", "win_rate", "net_r"],
            )
            self.assertEqual([row["session"] for row in rows], ["Asia", "London", "NewYork", "Other"])
            asia = rows[0]
            self.assertEqual(asia["trades"], "2")
            self.assertEqual(asia["wins"], "1")
            self.assertEqual(asia["losses"], "0")
            self.assertEqual(asia["net_pnl"], "170.0")
            self.assertEqual(asia["average_pnl"], "85.0")
            self.assertEqual(asia["win_rate"], "100.0")
            self.assertEqual(asia["net_r"], "2.0")

    def test_export_enhanced_summary_files_writes_base_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "nested" / "summary"

            export_enhanced_backtest_summary_files(_sample_report(), output_dir, mode="simulation")

            self.assertTrue((output_dir / "enhanced_backtest_summary.json").exists())
            self.assertTrue((output_dir / "backtest_risk_skip_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_session_pnl_summary.csv").exists())

    def test_export_enhanced_summary_files_with_realism_writes_realism_and_cost_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "summary"

            export_enhanced_backtest_summary_files(_sample_report(), output_dir, realism=_realism_config(), mode="realism")

            self.assertTrue((output_dir / "backtest_realism_summary.csv").exists())
            self.assertTrue((output_dir / "backtest_cost_summary.csv").exists())

    def test_export_enhanced_summary_files_without_realism_omits_realism_and_cost_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "summary"

            export_enhanced_backtest_summary_files(_sample_report(), output_dir, mode="simulation")

            self.assertFalse((output_dir / "backtest_realism_summary.csv").exists())
            self.assertFalse((output_dir / "backtest_cost_summary.csv").exists())

    def test_empty_report_exports_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "empty"

            export_enhanced_backtest_summary_files(_empty_report(), output_dir, realism=_realism_config(), mode="realism")

            self.assertTrue((output_dir / "enhanced_backtest_summary.json").exists())
            self.assertEqual(len(_csv_rows(output_dir / "backtest_risk_skip_summary.csv")), 5)
            self.assertEqual(len(_csv_rows(output_dir / "backtest_session_pnl_summary.csv")), 4)

    def test_legacy_export_backtest_report_still_writes_original_four_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "legacy"

            export_backtest_report(_sample_report(), output_dir)

            self.assertEqual(
                {path.name for path in output_dir.iterdir() if path.is_file()},
                {
                    "backtest_trades.csv",
                    "backtest_decisions.csv",
                    "backtest_session_summary.csv",
                    "backtest_summary.json",
                },
            )

    def test_does_not_create_root_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "summary"

            export_enhanced_backtest_summary_files(_sample_report(), output_dir, realism=_realism_config(), mode="realism")

            self.assertFalse((Path(directory) / "trading_signal_order.csv").exists())

    def test_does_not_create_logs_order_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "summary"

            export_enhanced_backtest_summary_files(_sample_report(), output_dir, realism=_realism_config(), mode="realism")

            self.assertFalse((Path(directory) / "logs" / "trading_signal_order.csv").exists())

    def test_backtest_source_has_no_execution_or_order_intent_terms(self) -> None:
        source = _source_text()
        imports = _source_imports()

        self.assertFalse(any("auto_trade" in import_name for import_name in imports))
        self.assertNotIn("process_auto_trade", source)
        self.assertNotIn("order_file", source)
        self.assertNotIn("trading_signal_order", source)


def _sample_report() -> BacktestReport:
    trades = (
        _trade(session="Asia", result="WIN", r_multiple=1.5, pnl=150.0, balance_after=10150.0, volume=1.0),
        _trade(session="Asia", result="OPEN_AT_END", r_multiple=0.5, pnl=20.0, balance_after=10170.0, volume=1.0),
        _trade(session="London", result="LOSS", r_multiple=-1.0, pnl=-100.0, balance_after=10070.0, volume=1.0),
        _trade(session="NewYork", result="LOSS_BOTH_HIT", r_multiple=-1.0, pnl=-90.0, balance_after=10080.0, volume=None),
    )
    decisions = (
        _decision("Asia", "signal_candidate", True, ()),
        _decision("London", "risk_skip", False, ("cooldown active",)),
        _decision("London", "risk_skip", False, ("cooldown active",)),
        _decision("NewYork", "risk_skip", False, ("max daily loss reached",)),
    )
    return BacktestReport(
        trades=trades,
        decisions=decisions,
        metrics=calculate_backtest_metrics(list(trades), list(decisions)),
        session_metrics=calculate_session_metrics(list(trades), list(decisions)),
        reject_reason_summary=summarize_reject_reasons(list(decisions)),
        skip_reason_summary=summarize_skip_reasons(list(decisions)),
        stopped_reason=None,
    )


def _empty_report() -> BacktestReport:
    metrics = calculate_backtest_metrics([], [])
    return BacktestReport(
        trades=(),
        decisions=(),
        metrics=metrics,
        session_metrics={},
        reject_reason_summary={},
        skip_reason_summary={},
        stopped_reason=None,
    )


def _trade(
    session: str,
    result: str,
    r_multiple: float,
    pnl: float,
    balance_after: float,
    volume: float | None,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        action=SignalAction.BUY,
        session=session,
        entry_time="2026-05-18T07:00:00Z",
        exit_time="2026-05-18T07:05:00Z",
        entry=100.0,
        stop_loss=99.0,
        tp1=None,
        tp2=101.5,
        result=result,
        r_multiple=r_multiple,
        risk_reward=1.5,
        volume=volume,
        pnl=pnl,
        balance_after=balance_after,
        loss_reason="stop_loss_hit" if result.startswith("LOSS") else None,
        reject_reasons_before_entry=(),
    )


def _decision(
    session: str,
    stage: str,
    approved: bool,
    reasons: tuple[str, ...],
) -> BacktestDecision:
    return BacktestDecision(
        timestamp="2026-05-18T07:00:00+00:00",
        session=session,
        symbol="XAUUSD",
        timeframe="M1",
        action="BUY" if approved else None,
        stage=stage,
        approved=approved,
        reasons=reasons,
        htf_bias="BULLISH",
        execution_trend="BULLISH",
        price_location="NEAR_DEMAND",
        candle_confirmation_summary="strong close",
        risk_reward=1.5,
    )


def _realism_config() -> BacktestRealismConfig:
    return BacktestRealismConfig(
        initial_balance=10000.0,
        risk_percent=1.0,
        contract_size=100.0,
        min_volume=0.01,
        max_volume=10.0,
        volume_step=0.01,
        allow_min_volume=True,
        spread_points=20.0,
        point_value=0.01,
        slippage_points=5.0,
        commission_per_lot=7.0,
        max_daily_loss_percent=3.0,
        max_consecutive_losses=3,
        cooldown_minutes=30,
    )


def _enhanced_json_payload(
    realism: BacktestRealismConfig | None,
    mode: str,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "enhanced_backtest_summary.json"
        export_enhanced_backtest_summary_json(_sample_report(), path, realism=realism, mode=mode)
        return json.loads(path.read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return next(csv.reader(file))


def _source_text() -> str:
    return _source_path().read_text(encoding="utf-8")


def _source_imports() -> list[str]:
    tree = ast.parse(_source_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return imports


def _source_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "trading_signal_bot" / "backtest.py"


if __name__ == "__main__":
    unittest.main()
