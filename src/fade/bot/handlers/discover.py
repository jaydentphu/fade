"""Market discovery: /trending, /search, and category browsing (PRD F-9).

Category buttons ride /trending rather than a separate /categories command:
trending is already the browse entry point users reach for, the categories
come from the same market data (Polymarket's own category labels), and one
command with buttons beats two commands (PRD §6: buttons are the default
path). Tapping a category renders through the exact same cache/keyboard
path as /search results — no parallel card system.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import sanitize
from fade.bot.handlers.trade import cache_card, card_keyboard, get_card, render_card_text
from fade.trading.markets import truncate_utf8

TRENDING_HEADER = "🔥 Trending on Polymarket — pick your poison:"
CATEGORY_HEADER = "📂 Top of {category}:"
SEARCH_HEADER = "Results for {query}:"
SEARCH_USAGE = "Usage: /search <term>. Example: /search fed rates"
NO_RESULTS_TEXT = "Nothing found. Either it doesn't exist or nobody's brave enough to trade it."
FETCHING_TEXT = "Looking..."

MAX_CATEGORY_BUTTONS = 6
CATEGORY_BUTTONS_PER_ROW = 3
# Telegram caps callback_data at 64 BYTES; "c:" costs 2, leave headroom.
CATEGORY_DATA_BYTES = 60


async def _results_keyboard(
    context: ContextTypes.DEFAULT_TYPE, cards, categories: list[str] | None = None
) -> InlineKeyboardMarkup:
    rows = []
    for card in cards:
        key = await cache_card(context, card)
        price = f" ({card.yes_price:.2f})" if card.yes_price is not None else ""
        label = sanitize(card.question, limit=48) + price
        rows.append([InlineKeyboardButton(label, callback_data=f"v:{key}")])
    if categories:
        buttons = [
            InlineKeyboardButton(
                f"📂 {name}", callback_data=f"c:{truncate_utf8(name, CATEGORY_DATA_BYTES)}"
            )
            for name in categories[:MAX_CATEGORY_BUTTONS]
        ]
        rows.extend(
            buttons[i : i + CATEGORY_BUTTONS_PER_ROW]
            for i in range(0, len(buttons), CATEGORY_BUTTONS_PER_ROW)
        )
    return InlineKeyboardMarkup(rows)


def _categories_of(cards) -> list[str]:
    """Distinct category labels present in the fetched markets, order kept."""
    seen: list[str] = []
    for card in cards:
        if card.category and card.category not in seen:
            seen.append(card.category)
    return seen


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    trading = context.application.bot_data["trading"]
    progress = await update.message.reply_text(FETCHING_TEXT)
    cards = await trading.list_trending()
    if not cards:
        await progress.edit_text(NO_RESULTS_TEXT)
        return
    keyboard = await _results_keyboard(context, cards, categories=_categories_of(cards))
    await progress.edit_text(TRENDING_HEADER, reply_markup=keyboard)


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
        reply_markup=await _results_keyboard(context, cards),
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A category button was tapped: top markets in that category, rendered
    through the same discovery-card path as /search results (F-9)."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    category = query.data.split(":", 1)[1]
    trading = context.application.bot_data["trading"]
    cards = await trading.list_by_category(category)
    if not cards:
        await query.edit_message_text(NO_RESULTS_TEXT)
        return
    await query.edit_message_text(
        CATEGORY_HEADER.format(category=sanitize(category, limit=40)),
        reply_markup=await _results_keyboard(context, cards),
    )


async def view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A discovery result was tapped: swap the list for the tradeable card."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    key = query.data.split(":")[1]
    card = await get_card(context, key)
    if card is None:
        await query.edit_message_text("That card expired. Run the command again.")
        return
    await query.edit_message_text(render_card_text(card), reply_markup=card_keyboard(key))
