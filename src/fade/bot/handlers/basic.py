"""Utility handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

PING_TEXT = "Still standing. Which is more than your portfolio will be able to say."


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(PING_TEXT)
