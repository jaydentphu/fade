"""Paste-URL trading handlers: card rendering, gate, confirm, execution."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import trade
from fade.trading.markets import MarketCard, MarketLookupError
from fade.trading.service import OrderResult

USER_ID = 801

CARD = MarketCard(
    market_id="1",
    question="Will it rain tomorrow?",
    slug="will-it-rain",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.60"),
    no_price=Decimal("0.40"),
    end_date="2026-08-01",
    open=True,
)


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    trading = MagicMock()
    trading.get_market_card = AsyncMock(return_value=CARD)
    trading.execute_market_order = AsyncMock(
        return_value=OrderResult(
            status="filled",
            side="BUY",
            fill_price=Decimal("0.61"),
            fill_shares=Decimal("16.4"),
            fill_usdc=Decimal("10"),
            fee=Decimal("0.10"),
            new_balance=Decimal("90"),
        )
    )
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
    }
    return context


def make_message_update(text: str = "") -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = 5000
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def make_callback_update(data: str, update_id: int = 6000) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


async def test_pasting_url_yields_market_card_with_buy_buttons_and_sizes(
    settings, session_factory, onboard
):
    """PRD F-8: URL -> card with Buy YES / Buy NO and preset + custom sizes."""
    await onboard(USER_ID)
    update = make_message_update("https://polymarket.com/event/will-it-rain")
    context = make_context(settings, session_factory)

    await trade.show_market_card(update, context, update.message.text)

    text = update.message.reply_text.call_args.args[0]
    assert "Will it rain tomorrow?" in text
    assert "YES 0.60" in text and "NO 0.40" in text

    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    # Every preset size appears for both sides, plus a custom option each.
    for size in trade.PRESET_SIZES:
        assert f"🟢 YES ${size}" in labels
        assert f"🔴 NO ${size}" in labels
    assert "🟢 YES custom" in labels
    assert "🔴 NO custom" in labels

    # No label is truncated: rows are at most 3 buttons wide, and labels are short.
    assert all(len(row) <= trade.BUTTONS_PER_ROW for row in markup.inline_keyboard)
    assert all(len(label) <= 12 for label in labels)

    # The $10 YES button still drives the buy flow with the right callback.
    yes_ten = next(
        button for row in markup.inline_keyboard for button in row if button.text == "🟢 YES $10"
    )
    assert yes_ten.callback_data.endswith(":y:10")


async def test_attestation_gate_blocks_trading_until_accepted(settings, session_factory, onboard):
    """PRD F-1: no attestation -> no trading. The card is never even fetched."""
    update = make_message_update("https://polymarket.com/event/will-it-rain")
    context = make_context(settings, session_factory)

    await trade.show_market_card(update, context, update.message.text)

    assert update.message.reply_text.call_args.args[0] == trade.NOT_ONBOARDED_TEXT
    context.application.bot_data["trading"].get_market_card.assert_not_awaited()


async def test_non_binary_market_gets_polite_rejection(settings, session_factory, onboard):
    await onboard(USER_ID)
    update = make_message_update("https://polymarket.com/event/election-2028")
    context = make_context(settings, session_factory)
    context.application.bot_data["trading"].get_market_card = AsyncMock(
        side_effect=MarketLookupError("Only binary YES/NO markets are supported for now.")
    )

    await trade.show_market_card(update, context, update.message.text)

    assert "binary" in update.message.reply_text.call_args.args[0]


async def test_size_tap_shows_confirm_step(settings, session_factory, onboard):
    context = make_context(settings, session_factory)
    key = trade.cache_card(context, CARD)
    update = make_callback_update(f"t:{key}:y:25")

    await trade.size_callback(update, context)

    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Confirm order" in text and "$25" in text and "Buy YES" in text


async def test_confirm_executes_order_and_reports_fill(settings, session_factory, onboard):
    """PRD F-13: confirmation shows fill price, size, fee, and new balance."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = trade.cache_card(context, CARD)
    update = make_callback_update(f"tc:{key}:y:25", update_id=6001)

    await trade.confirm_callback(update, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["telegram_update_id"] == 6001
    assert call.kwargs["token_id"] == CARD.yes_token
    assert call.kwargs["side"] == "BUY"
    assert call.kwargs["amount"] == Decimal("25")

    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Filled." in text
    assert "0.61" in text  # fill price
    assert "16.4" in text  # shares
    assert "0.10" in text  # fee
    assert "90" in text  # new balance


async def test_custom_amount_flow(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = trade.cache_card(context, CARD)

    tap = make_callback_update(f"t:{key}:n:c")
    await trade.size_callback(tap, context)
    assert context.user_data["pending_custom"] == (key, "n")

    reply = make_message_update("42")
    handled = await trade.handle_custom_amount(reply, context)
    assert handled is True
    text = reply.message.reply_text.call_args.args[0]
    assert "Buy NO" in text and "$42" in text


async def test_custom_amount_rejects_garbage_and_out_of_bounds(settings, session_factory, onboard):
    context = make_context(settings, session_factory)
    key = trade.cache_card(context, CARD)
    context.user_data["pending_custom"] = (key, "y")

    bad = make_message_update("banana")
    await trade.handle_custom_amount(bad, context)
    assert bad.message.reply_text.call_args.args[0] == trade.BAD_AMOUNT_TEXT

    too_big = make_message_update("999999")
    await trade.handle_custom_amount(too_big, context)
    assert too_big.message.reply_text.call_args.args[0] == trade.BAD_AMOUNT_TEXT


async def test_duplicate_result_does_not_replace_message(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    context.application.bot_data["trading"].execute_market_order = AsyncMock(
        return_value=OrderResult(status="duplicate", side="BUY")
    )
    key = trade.cache_card(context, CARD)
    update = make_callback_update(f"tc:{key}:y:10")

    await trade.confirm_callback(update, context)

    update.callback_query.edit_message_text.assert_not_awaited()


async def test_cancel_places_nothing(settings, session_factory, onboard):
    context = make_context(settings, session_factory)
    update = make_callback_update("tx")

    await trade.cancel_callback(update, context)

    assert update.callback_query.edit_message_text.call_args.args[0] == trade.CANCELLED_TEXT
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()
