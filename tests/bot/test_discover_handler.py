"""/trending and /search (PRD F-9)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import discover
from fade.trading.markets import MarketCard

CARD = MarketCard(
    market_id="1",
    question="Will it rain tomorrow?",
    slug="will-it-rain",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.60"),
    no_price=Decimal("0.40"),
    end_date=None,
    open=True,
)


def make_context() -> MagicMock:
    context = MagicMock()
    context.args = []
    trading = MagicMock()
    trading.list_trending = AsyncMock(return_value=[CARD])
    trading.search_markets = AsyncMock(return_value=[CARD])
    context.application.bot_data = {"trading": trading}
    return context


def make_update() -> MagicMock:
    update = MagicMock(spec=Update)
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    return update


async def test_trending_lists_markets_as_buttons():
    update = make_update()
    context = make_context()

    await discover.trending(update, context)

    progress = update.message.reply_text.return_value
    markup = progress.edit_text.call_args.kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert "Will it rain tomorrow?" in button.text
    assert button.callback_data.startswith("v:")


async def test_search_requires_a_term():
    update = make_update()
    context = make_context()
    context.args = []

    await discover.search(update, context)

    assert update.message.reply_text.call_args.args[0] == discover.SEARCH_USAGE
    context.application.bot_data["trading"].search_markets.assert_not_awaited()


async def test_search_lists_results():
    update = make_update()
    context = make_context()
    context.args = ["rain"]

    await discover.search(update, context)

    context.application.bot_data["trading"].search_markets.assert_awaited_once_with("rain")
    progress = update.message.reply_text.return_value
    assert "rain" in progress.edit_text.call_args.args[0]


async def test_view_callback_swaps_in_tradeable_card():
    update = MagicMock(spec=Update)
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = make_context()

    from fade.bot.handlers.trade import cache_card

    key = cache_card(context, CARD)
    update.callback_query.data = f"v:{key}"

    await discover.view_callback(update, context)

    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Will it rain tomorrow?" in text
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "🟢 YES $10" in labels and "🔴 NO custom" in labels
