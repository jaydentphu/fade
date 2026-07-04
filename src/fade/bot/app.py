from __future__ import annotations

from telegram.ext import Application, CommandHandler

from fade.bot.handlers import basic
from fade.config import Settings


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", basic.start))
    app.add_handler(CommandHandler("ping", basic.ping))
    return app
