"""/positions: sell buttons, DM-only, sell execution (PRD F-12)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import positions
from fade.bot.handlers.common import GROUP_REDIRECT_TEXT
from fade.trading.service import OrderResult, PositionView

USER_ID = 802
WALLET = "0xFadE0000000000000000000000000000000000aa"

VIEW = PositionView(
    token_id="111",
    title="Will it rain tomorrow?",
    outcome="Yes",
    shares=Decimal("16.4"),
    avg_price=Decimal("0.61"),
    cur_price=Decimal("0.70"),
    cash_pnl=Decimal("1.48"),
    percent_pnl=14.8,
)


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=WALLET)
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[VIEW])
    trading.execute_market_order = AsyncMock(
        return_value=OrderResult(
            status="filled",
            side="SELL",
            fill_price=Decimal("0.70"),
            fill_shares=Decimal("16.4"),
            fill_usdc=Decimal("11.48"),
            fee=Decimal("0.11"),
            new_balance=Decimal("101.48"),
        )
    )
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
        "trading": trading,
    }
    return context


def make_update(chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = 7000
    update.effective_chat.type = chat_type
    update.effective_user.id = USER_ID
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    progress.delete = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    return update


def make_callback_update(data: str, update_id: int = 7100) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_positions_render_open_positions_with_sell_buttons(
    settings, session_factory, onboard
):
    """PRD F-12: open positions with live PnL and one-tap Sell buttons."""
    await onboard(USER_ID)
    update = make_update()
    context = make_context(settings, session_factory)

    await positions.positions(update, context)

    # Second reply_text call is the position card (first was the progress message).
    card_call = update.message.reply_text.call_args_list[-1]
    text = card_call.args[0]
    assert "Will it rain tomorrow?" in text
    assert "16.4 shares" in text
    assert "+$1.48" in text  # live PnL
    markup = card_call.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Sell"
    assert markup.inline_keyboard[0][0].callback_data.startswith("ps:")


async def test_positions_in_group_redirects_to_dm(settings, session_factory, onboard):
    update = make_update(chat_type="group")
    context = make_context(settings, session_factory)

    await positions.positions(update, context)

    assert update.message.reply_text.call_args.args[0] == GROUP_REDIRECT_TEXT
    context.application.bot_data["trading"].list_positions.assert_not_awaited()


async def test_positions_empty_state(settings, session_factory, onboard):
    await onboard(USER_ID)
    update = make_update()
    context = make_context(settings, session_factory)
    context.application.bot_data["trading"].list_positions = AsyncMock(return_value=[])

    await positions.positions(update, context)

    progress = update.message.reply_text.return_value
    assert progress.edit_text.call_args.args[0] == positions.NO_POSITIONS_TEXT


async def test_sell_tap_shows_confirm_then_executes_full_position(
    settings, session_factory, onboard
):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = await positions._cache_position(context, VIEW)

    tap = make_callback_update(f"ps:{key}")
    await positions.sell_callback(tap, context)
    confirm_text = tap.callback_query.edit_message_text.call_args.args[0]
    assert "Confirm sell" in confirm_text and "16.4" in confirm_text

    confirm = make_callback_update(f"psc:{key}", update_id=7200)
    await positions.sell_confirm_callback(confirm, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["side"] == "SELL"
    assert call.kwargs["token_id"] == "111"
    assert call.kwargs["amount"] == Decimal("16.4")
    assert call.kwargs["telegram_update_id"] == 7200


async def test_position_card_is_owner_locked(settings, session_factory, onboard):
    """Stage 10: user A's sell card, tapped by user B, is rejected — nothing
    executes. Cards are stamped with their owner by the /positions flow itself."""
    await onboard(USER_ID)
    update = make_update()
    context = make_context(settings, session_factory)
    await positions.positions(update, context)

    card_call = update.message.reply_text.call_args_list[-1]
    key = card_call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.split(":")[1]

    intruder_tap = make_callback_update(f"ps:{key}")
    intruder_tap.effective_user.id = 999  # not the card's owner
    await positions.sell_callback(intruder_tap, context)
    intruder_tap.callback_query.answer.assert_awaited_once_with(
        positions.NOT_YOUR_POSITION_ALERT, show_alert=True
    )
    intruder_tap.callback_query.edit_message_text.assert_not_awaited()

    intruder_confirm = make_callback_update(f"psc:{key}", update_id=7300)
    intruder_confirm.effective_user.id = 999
    await positions.sell_confirm_callback(intruder_confirm, context)
    intruder_confirm.callback_query.answer.assert_awaited_once_with(
        positions.NOT_YOUR_POSITION_ALERT, show_alert=True
    )
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()

    # The rightful owner is unaffected.
    owner_tap = make_callback_update(f"ps:{key}")
    await positions.sell_callback(owner_tap, context)
    assert "Confirm sell" in owner_tap.callback_query.edit_message_text.call_args.args[0]
