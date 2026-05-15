from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .models import TelegramConfig


class TelegramSendError(RuntimeError):
    pass


def send_telegram_message(config: TelegramConfig, message: str, timeout_seconds: int = 15) -> None:
    if not config.bot_token or not config.chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when dry-run is disabled")

    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": config.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise TelegramSendError("Failed to send Telegram message") from exc

    try:
        response_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TelegramSendError("Telegram returned invalid JSON") from exc

    if not response_data.get("ok"):
        description = response_data.get("description", "unknown error")
        raise TelegramSendError(f"Telegram API rejected the message: {description}")


def send_telegram_photo(
    config: TelegramConfig,
    photo_path: str,
    caption: str | None = None,
    timeout_seconds: int = 30,
) -> None:
    if not config.bot_token or not config.chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when dry-run is disabled")

    path = Path(photo_path)
    if not path.exists():
        raise FileNotFoundError(f"Telegram photo file not found: {photo_path}")

    boundary = f"----TradingSignalBot{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = _multipart_body(
        boundary=boundary,
        fields={
            "chat_id": config.chat_id,
            "caption": caption or "",
        },
        files={
            "photo": (path.name, mime_type, path.read_bytes()),
        },
    )
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{config.bot_token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise TelegramSendError("Failed to send Telegram photo") from exc

    try:
        response_data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise TelegramSendError("Telegram returned invalid JSON") from exc

    if not response_data.get("ok"):
        description = response_data.get("description", "unknown error")
        raise TelegramSendError(f"Telegram API rejected the photo: {description}")


def _multipart_body(
    boundary: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, str, bytes]],
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    for name, (filename, mime_type, content) in files.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(content)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)
