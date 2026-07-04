"""Skeleton handlers. Onboarding (F-1), wallets, and trading land in Phase 1."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

START_TEXT = (
    "Welcome to Fade — fight club for forecasters.\n\n"
    "The forge is still hot: wallets, trading, and the fade button are under "
    "construction. Nothing to lose money on yet. Patience, champ.\n\n"
    "/ping — check I'm alive"
)

PING_TEXT = "Still standing. Which is more than your portfolio will be able to say."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(START_TEXT)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(PING_TEXT)
