#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing .env at $ROOT_DIR/.env" >&2
  exit 1
fi

echo "คำเตือน: runner นี้เขียน MT5 order intent ได้จริงเมื่อ AUTO_TRADE_ENABLED=true และ AUTO_TRADE_MODE=mt5_file"
echo "ใช้กับบัญชี Demo เท่านั้น และต้องตรวจว่า MT5 EA TradingSignalAutoTrader อยู่บนบัญชี Demo ก่อน"
echo

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

from trading_signal_bot.config import load_auto_trade_config, load_env_file, load_signal_config

load_env_file()
signal_config = load_signal_config()
auto_trade_config = load_auto_trade_config()

errors: list[str] = []
if not auto_trade_config.enabled:
    errors.append("AUTO_TRADE_ENABLED must be true")
if auto_trade_config.mode != "mt5_file":
    errors.append("AUTO_TRADE_MODE must be mt5_file")
if not auto_trade_config.order_file:
    errors.append("AUTO_TRADE_ORDER_FILE is required")
allowed_trade_modes = {"asian_breakout", "h4_breakout_retest"}
if signal_config.trade_mode not in allowed_trade_modes:
    errors.append("SIGNAL_TRADE_MODE must be asian_breakout or h4_breakout_retest for this XAUUSD session bot")
allowed_execution_timeframes = {"M5", "M15", "M30"}
if signal_config.execution_timeframe not in allowed_execution_timeframes:
    errors.append("SIGNAL_EXECUTION_TIMEFRAME must be M5, M15, or M30")

required_timeframes = ("D1", "H4", "H1", "M30", "M15", "M5")
for timeframe in required_timeframes:
    raw_path = signal_config.timeframe_paths.get(timeframe)
    if not raw_path:
        errors.append(f"SIGNAL_CSV_PATH_{timeframe} is required")
        continue
    if timeframe not in {"M15", "M30"} and not Path(raw_path).exists():
        errors.append(f"SIGNAL_CSV_PATH_{timeframe} does not exist: {raw_path}")

if errors:
    print("Auto-trade preflight failed:")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("Auto-trade preflight OK")
print(f"- symbol: {signal_config.symbol}")
print(f"- trade_mode: {signal_config.trade_mode}")
print(f"- execution_timeframe: {signal_config.execution_timeframe}")
print(f"- order_file: {auto_trade_config.order_file}")
print(f"- account_balance: {auto_trade_config.account_balance}")
print(f"- risk_percent: {auto_trade_config.risk_percent}")
PY

echo
echo "Rebuilding M15/M30 from real M5 candles..."
"$PYTHON_BIN" scripts/resample_mt5_timeframes_from_m5.py --overwrite

echo
echo "Checking real data coverage..."
"$PYTHON_BIN" scripts/check_real_data_coverage.py

resampler_pid=""
cleanup() {
  if [[ -n "$resampler_pid" ]] && kill -0 "$resampler_pid" 2>/dev/null; then
    kill "$resampler_pid" 2>/dev/null || true
    wait "$resampler_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo
echo "Starting M15/M30 resampler watch..."
"$PYTHON_BIN" scripts/resample_mt5_timeframes_from_m5.py --overwrite --watch --interval-seconds "${RESAMPLE_INTERVAL_SECONDS:-5}" &
resampler_pid="$!"

echo "Starting signal poller. Press Ctrl+C to stop."
echo
"$PYTHON_BIN" -m trading_signal_bot.signal_poller
