from __future__ import annotations

from .models import Signal, SignalAction


def format_signal_message(signal: Signal) -> str:
    header = _header(signal)
    if signal.action == SignalAction.WAIT:
        trade_plan = (
            "🟡 แผน: ยังไม่เข้า trade\n"
            f"⏳ รออะไร: {signal.entry_condition}\n"
            f"🧱 เหตุผลที่รอ: {signal.no_trade_reason}"
        )
    else:
        trade_plan = (
            f"🎯 Entry: {_format_price(signal.levels.entry)}\n"
            f"🛑 Stop Loss: {_format_price(signal.levels.stop_loss)}\n"
            f"✅ Take Profit: {_format_price(signal.levels.take_profit)}\n"
            f"⚖️ Risk/Reward: 1:{signal.levels.risk_reward:.2f}\n"
            f"📌 เงื่อนไขเข้า: {signal.entry_condition}\n"
            f"❌ ยกเลิกแผน: {signal.invalidation}"
        )

    return (
        f"{header}\n\n"
        f"📍 Market Structure\n"
        f"• {signal.market_structure}\n"
        f"• Trend: {signal.trend_summary}\n"
        f"• Alignment: {signal.trend_alignment}\n"
        f"• Close: {_format_price(signal.latest_close)}\n"
        f"• Support: {_format_price(signal.support)}\n"
        f"• Resistance: {_format_price(signal.resistance)}\n\n"
        f"📊 Confirmation\n"
        f"• EMA Fast/Slow: {_format_price(signal.fast_ema)} / {_format_price(signal.slow_ema)}\n"
        f"• RSI: {signal.rsi:.2f}\n"
        f"• ATR: {_format_price(signal.atr)}\n\n"
        f"🧭 Trade Plan\n"
        f"{trade_plan}\n\n"
        f"🧠 เหตุผลหลัก\n"
        f"{signal.reason}\n\n"
        f"⚠️ Key Risk\n"
        f"{signal.no_trade_reason}\n\n"
        "📎 หมายเหตุ: ไม่ใช่คำแนะนำทางการเงินส่วนบุคคล ใช้ทดสอบ Demo/Paper ก่อนเงินจริงเสมอ"
    )


def _header(signal: Signal) -> str:
    action_icon = {
        SignalAction.BUY: "🟢",
        SignalAction.SELL: "🔴",
        SignalAction.WAIT: "🟡",
    }[signal.action]
    return (
        f"{action_icon} {signal.action.value} | {signal.symbol} | {signal.timeframe}\n"
        f"🧩 Strategy: {signal.strategy_name}\n"
        f"🔎 Setup: {signal.setup_type}\n"
        f"🎚️ Confidence: {signal.confidence.value}"
    )


def _format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.5f}".rstrip("0").rstrip(".")
