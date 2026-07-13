"""FastAPI webhook receiver for production (CLAUDE.md: webhook in prod, polling
in dev). The only web surface, per the stack rules.

Telegram is told a secret token when we set the webhook; it echoes that token
in the X-Telegram-Bot-Api-Secret-Token header on every delivery. We reject any
request whose header doesn't match TELEGRAM_WEBHOOK_SECRET with 401 BEFORE the
Application ever sees it. Valid updates go into the exact same
Application.process_update pipeline that polling uses.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from telegram import Update

from fade.bot.app import build_application
from fade.config import Settings, load_settings

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
WEBHOOK_PATH = "/telegram/webhook"


def create_webhook_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    application = build_application(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await application.initialize()
        await application.start()
        try:
            yield
        finally:
            await application.stop()
            await application.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.application = application
    app.state.webhook_secret = settings.telegram_webhook_secret

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/miniapp")
    async def miniapp_page() -> Response:
        from fade.miniapp import MINIAPP_HTML

        return Response(content=MINIAPP_HTML, media_type="text/html")

    @app.post("/miniapp/register")
    async def miniapp_register(request: Request) -> Response:
        from fade.miniapp import handle_register

        try:
            body = await request.json()
        except Exception:
            return Response(status_code=400, content="bad request")
        return await handle_register(
            application.bot_data["sessions"],
            application.bot_data["wallets"],
            settings.telegram_bot_token,
            body,
        )

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        return await handle_webhook_raw(
            application,
            app.state.webhook_secret,
            x_telegram_bot_api_secret_token,
            await request.body(),
        )

    return app


async def handle_webhook_raw(
    application,
    expected_secret: str,
    provided_secret: str | None,
    raw_body: bytes,
) -> Response:
    """Secret check first (cheapest rejection), then JSON parsing: a body that
    isn't valid JSON is a 400, never a 500, and never touches the Application."""
    if not expected_secret or provided_secret != expected_secret:
        return Response(status_code=401, content="unauthorized")
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        logger.warning("webhook body was not valid JSON; rejected 400")
        return Response(status_code=400, content="bad request")
    return await handle_webhook_request(application, expected_secret, provided_secret, body)


async def handle_webhook_request(
    application,
    expected_secret: str,
    provided_secret: str | None,
    body: dict,
) -> Response:
    """Verify the secret, then forward. Extracted so it's testable without a
    running HTTP server or a live Telegram connection."""
    # A blank configured secret would make verification meaningless; treat the
    # absence of a configured secret as "reject everything" rather than "allow".
    if not expected_secret or provided_secret != expected_secret:
        return Response(status_code=401, content="unauthorized")
    # Plausibility gate: every real Telegram update is an object with an
    # integer update_id. Anything else is rejected before the Application.
    if not isinstance(body, dict) or not isinstance(body.get("update_id"), int):
        logger.warning("webhook body was not a plausible Telegram update; rejected 400")
        return Response(status_code=400, content="bad request")
    try:
        update = Update.de_json(body, application.bot)
    except Exception:
        logger.warning("webhook body failed Update parsing; rejected 400", exc_info=True)
        return Response(status_code=400, content="bad request")
    if update is None:
        return Response(status_code=400, content="bad request")
    await application.process_update(update)
    return Response(status_code=200, content="ok")
