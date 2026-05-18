from __future__ import annotations

import os
from pathlib import Path

from .models import AutoTradeConfig, SignalConfig, TelegramConfig, WebhookConfig


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def _get_float(name: str, default: float, minimum: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    return value


def load_signal_config() -> SignalConfig:
    fast_ema_period = _get_int("SIGNAL_FAST_EMA", 12, 2)
    slow_ema_period = _get_int("SIGNAL_SLOW_EMA", 26, 3)
    if fast_ema_period >= slow_ema_period:
        raise ValueError("SIGNAL_FAST_EMA must be lower than SIGNAL_SLOW_EMA")

    timeframe_paths = {
        "D1": os.getenv("SIGNAL_CSV_PATH_D1", "").strip(),
        "H4": os.getenv("SIGNAL_CSV_PATH_H4", "").strip(),
        "H1": os.getenv("SIGNAL_CSV_PATH_H1", "").strip(),
        "M30": os.getenv("SIGNAL_CSV_PATH_M30", "").strip(),
        "M15": os.getenv("SIGNAL_CSV_PATH_M15", "").strip(),
        "M5": os.getenv("SIGNAL_CSV_PATH_M5", "").strip(),
    }
    trade_mode = os.getenv("SIGNAL_TRADE_MODE", "high_winrate").strip().lower() or "high_winrate"
    if trade_mode not in {"high_winrate", "active"}:
        raise ValueError("SIGNAL_TRADE_MODE must be high_winrate or active")

    return SignalConfig(
        symbol=os.getenv("SIGNAL_SYMBOL", "XAUUSD").strip() or "XAUUSD",
        timeframe=os.getenv("SIGNAL_TIMEFRAME", "M5").strip().upper() or "M5",
        csv_path=os.getenv("SIGNAL_CSV_PATH", "samples/ohlcv_sample.csv").strip(),
        fast_ema_period=fast_ema_period,
        slow_ema_period=slow_ema_period,
        rsi_period=_get_int("SIGNAL_RSI_PERIOD", 14, 2),
        atr_period=_get_int("SIGNAL_ATR_PERIOD", 14, 2),
        atr_multiplier=_get_float("SIGNAL_ATR_MULTIPLIER", 1.5, 0.1),
        body_break_atr_ratio=_get_float("SIGNAL_BODY_BREAK_ATR_RATIO", 0.20, 0.01),
        risk_reward=_get_float("SIGNAL_RISK_REWARD", 2.0, 0.1),
        min_candles=_get_int("SIGNAL_MIN_CANDLES", 60, 30),
        max_candle_age_minutes=_get_int("SIGNAL_MAX_CANDLE_AGE_MINUTES", 180, 1),
        multi_timeframe_enabled=_get_bool("SIGNAL_MULTI_TIMEFRAME", False),
        timeframe_paths=timeframe_paths,
        dry_run=_get_bool("SIGNAL_DRY_RUN", True),
        send_wait=_get_bool("SIGNAL_SEND_WAIT", False),
        trade_mode=trade_mode,
    )


def load_telegram_config() -> TelegramConfig:
    return TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )


def load_webhook_config() -> WebhookConfig:
    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ValueError("TRADINGVIEW_WEBHOOK_SECRET is required")

    path = os.getenv("TRADINGVIEW_WEBHOOK_PATH", "/webhook").strip() or "/webhook"
    if not path.startswith("/"):
        raise ValueError("TRADINGVIEW_WEBHOOK_PATH must start with /")

    return WebhookConfig(
        host=os.getenv("TRADINGVIEW_WEBHOOK_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_get_int("TRADINGVIEW_WEBHOOK_PORT", 8080, 1),
        path=path,
        secret=secret,
        dry_run=_get_bool("TRADINGVIEW_WEBHOOK_DRY_RUN", True),
    )


def load_auto_trade_config() -> AutoTradeConfig:
    mode = os.getenv("AUTO_TRADE_MODE", "paper").strip().lower() or "paper"
    if mode not in {"paper", "mt5_file"}:
        raise ValueError("AUTO_TRADE_MODE must be paper or mt5_file")

    risk_percent = _get_float("AUTO_TRADE_RISK_PERCENT", 0.5, 0.01)
    if risk_percent > 2.0:
        raise ValueError("AUTO_TRADE_RISK_PERCENT should not exceed 2.0 without a reviewed risk plan")

    min_volume = _get_float("AUTO_TRADE_MIN_VOLUME", 0.01, 0.0)
    max_volume = _get_float("AUTO_TRADE_MAX_VOLUME", 0.01, 0.0)
    if min_volume > max_volume:
        raise ValueError("AUTO_TRADE_MIN_VOLUME must be lower than or equal to AUTO_TRADE_MAX_VOLUME")

    volume_step = _get_float("AUTO_TRADE_VOLUME_STEP", 0.01, 0.00000001)

    return AutoTradeConfig(
        enabled=_get_bool("AUTO_TRADE_ENABLED", False),
        mode=mode,
        order_file=os.getenv("AUTO_TRADE_ORDER_FILE", "logs/trading_signal_order.csv").strip()
        or "logs/trading_signal_order.csv",
        journal_file=os.getenv("AUTO_TRADE_JOURNAL_FILE", "logs/auto_trade_journal.csv").strip()
        or "logs/auto_trade_journal.csv",
        account_balance=_get_float("AUTO_TRADE_ACCOUNT_BALANCE", 1000.0, 0.01),
        risk_percent=risk_percent,
        contract_size=_get_float("AUTO_TRADE_CONTRACT_SIZE", 100.0, 0.00000001),
        min_volume=min_volume,
        max_volume=max_volume,
        volume_step=volume_step,
        allow_min_volume=_get_bool("AUTO_TRADE_ALLOW_MIN_VOLUME", False),
        magic_number=_get_int("AUTO_TRADE_MAGIC_NUMBER", 20260515, 1),
        comment=os.getenv("AUTO_TRADE_COMMENT", "TradingSignalBot").strip() or "TradingSignalBot",
    )
