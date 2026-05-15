from __future__ import annotations

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import load_env_file, load_telegram_config, load_webhook_config
from .telegram import send_telegram_message
from .tradingview import format_tradingview_message, parse_tradingview_payload


LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 32_768


def run_server() -> None:
    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        webhook_config = load_webhook_config()
    except ValueError as exc:
        LOGGER.error(
            "%s. Add it to .env, for example: TRADINGVIEW_WEBHOOK_SECRET=your-random-secret",
            exc,
        )
        return
    telegram_config = load_telegram_config()

    class TradingViewWebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != webhook_config.path:
                self._write_json(HTTPStatus.NOT_FOUND, '{"ok": false, "error": "not found"}')
                return

            body = (
                '{"ok": true, '
                '"service": "tradingview-webhook", '
                '"message": "Webhook is running. Send TradingView alerts with POST."}'
            )
            self._write_json(HTTPStatus.OK, body)

        def do_POST(self) -> None:
            if self.path != webhook_config.path:
                self._write_json(HTTPStatus.NOT_FOUND, '{"ok": false, "error": "not found"}')
                return

            content_length = self.headers.get("Content-Length")
            if content_length is None:
                self._write_json(HTTPStatus.LENGTH_REQUIRED, '{"ok": false, "error": "content length required"}')
                return

            try:
                body_size = int(content_length)
            except ValueError:
                self._write_json(HTTPStatus.BAD_REQUEST, '{"ok": false, "error": "invalid content length"}')
                return

            if body_size <= 0 or body_size > MAX_BODY_BYTES:
                self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, '{"ok": false, "error": "invalid body size"}')
                return

            body = self.rfile.read(body_size)
            try:
                signal = parse_tradingview_payload(body, webhook_config.secret)
                message = format_tradingview_message(signal)
                if webhook_config.dry_run:
                    LOGGER.info("Webhook dry-run enabled; Telegram message was not sent.")
                    print(message)
                else:
                    send_telegram_message(telegram_config, message)
                    LOGGER.info("TradingView signal sent for %s %s", signal.symbol, signal.action.value)
            except PermissionError:
                LOGGER.warning("Rejected TradingView webhook with invalid secret")
                self._write_json(HTTPStatus.UNAUTHORIZED, '{"ok": false, "error": "unauthorized"}')
                return
            except ValueError as exc:
                LOGGER.warning("Rejected invalid TradingView webhook: %s", exc)
                self._write_json(HTTPStatus.BAD_REQUEST, '{"ok": false, "error": "invalid payload"}')
                return
            except Exception as exc:
                LOGGER.error("Failed to process TradingView webhook: %s", exc)
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, '{"ok": false, "error": "server error"}')
                return

            self._write_json(HTTPStatus.OK, '{"ok": true}')

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("webhook %s", format % args)

        def _write_json(self, status: HTTPStatus, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((webhook_config.host, webhook_config.port), TradingViewWebhookHandler)
    LOGGER.info("TradingView webhook server listening on http://%s:%s%s", webhook_config.host, webhook_config.port, webhook_config.path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("TradingView webhook server stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
