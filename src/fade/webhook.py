"""FastAPI webhook receiver for production (CLAUDE.md: webhook in prod, polling
in dev). The only web surface, per the stack rules.

Telegram is told a secret token when we set the webhook; it echoes that token
in the X-Telegram-Bot-Api-Secret-Token header on every delivery. We reject any
request whose header doesn't match TELEGRAM_WEBHOOK_SECRET with 401 BEFORE the
Application ever sees it. Valid updates go into the exact same
Application.process_update pipeline that polling uses.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from telegram import Update

from fade.bot.app import build_application
from fade.config import Settings, load_settings

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

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        return await handle_webhook_request(
            application,
            app.state.webhook_secret,
            x_telegram_bot_api_secret_token,
            await request.json(),
        )

    return app


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
    update = Update.de_json(body, application.bot)
    await application.process_update(update)
    return Response(status_code=200, content="ok")
