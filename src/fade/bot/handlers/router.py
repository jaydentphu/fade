"""Routes free-text DMs: Polymarket URLs and in-progress flow replies."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers import trade, withdraw
from fade.trading.markets import extract_polymarket_url


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    url = extract_polymarket_url(update.message.text)
    if url is not None:
        await trade.show_market_card(update, context, url)
        return
    if await trade.handle_custom_amount(update, context):
        return
    if await withdraw.handle_withdraw_text(update, context):
        return
    # Anything else is just chat; stay quiet.
