"""Market discovery: /trending and /search (PRD F-9)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import sanitize
from fade.bot.handlers.trade import cache_card, card_keyboard, get_card, render_card_text

TRENDING_HEADER = "🔥 Trending on Polymarket — pick your poison:"
SEARCH_HEADER = "Results for {query}:"
SEARCH_USAGE = "Usage: /search <term>. Example: /search fed rates"
NO_RESULTS_TEXT = "Nothing found. Either it doesn't exist or nobody's brave enough to trade it."
FETCHING_TEXT = "Looking..."


def _results_keyboard(context: ContextTypes.DEFAULT_TYPE, cards) -> InlineKeyboardMarkup:
    rows = []
    for card in cards:
        key = cache_card(context, card)
        price = f" ({card.yes_price:.2f})" if card.yes_price is not None else ""
        label = sanitize(card.question, limit=48) + price
        rows.append([InlineKeyboardButton(label, callback_data=f"v:{key}")])
    return InlineKeyboardMarkup(rows)


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    trading = context.application.bot_data["trading"]
    progress = await update.message.reply_text(FETCHING_TEXT)
    cards = await trading.list_trending()
    if not cards:
        await progress.edit_text(NO_RESULTS_TEXT)
        return
    await progress.edit_text(TRENDING_HEADER, reply_markup=_results_keyboard(context, cards))


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    query_text = " ".join(context.args or []).strip()
    if not query_text:
        await update.message.reply_text(SEARCH_USAGE)
        return
    trading = context.application.bot_data["trading"]
    progress = await update.message.reply_text(FETCHING_TEXT)
    cards = await trading.search_markets(query_text)
    if not cards:
        await progress.edit_text(NO_RESULTS_TEXT)
        return
    await progress.edit_text(
        SEARCH_HEADER.format(query=sanitize(query_text, limit=48)),
        reply_markup=_results_keyboard(context, cards),
    )


async def view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A discovery result was tapped: swap the list for the tradeable card."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    key = query.data.split(":")[1]
    card = get_card(context, key)
    if card is None:
        await query.edit_message_text("That card expired. Run the command again.")
        return
    await query.edit_message_text(render_card_text(card), reply_markup=card_keyboard(key))
