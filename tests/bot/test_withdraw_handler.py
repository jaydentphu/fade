"""/withdraw: first-time address confirm, DM-only, works while paused (PRD F-4, F-25)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import withdraw
from fade.bot.handlers.common import GROUP_REDIRECT_TEXT
from fade.db.models import TradingPauseFlag, WithdrawalAddress

USER_ID = 803
WALLET = "0xFadE0000000000000000000000000000000000aa"
# NOTE (Stage 10): this constant changed from "0xAbCd...1234" to the EIP-55
# checksummed form of the SAME address. The old casing was arbitrary mock
# casing that happens to be an invalid checksum, which the handler now
# rightly rejects — the fixture had to become a valid input, the assertions
# it feeds are unchanged.
DEST = "0xAbcd000000000000000000000000000000001234"
TX_HASH = "0x" + "cd" * 32


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.bot.send_message = AsyncMock()
    context.user_data = {}
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=WALLET)
    wallets.withdraw = AsyncMock(return_value=TX_HASH)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
    }
    return context


def make_update(text: str = "", chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_user.id = USER_ID
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def make_callback_update(data: str) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def run_flow_to_confirm(context) -> None:
    """Drive /withdraw -> amount -> address, stopping at the confirm screen."""
    start = make_update()
    await withdraw.withdraw(start, context)
    assert start.message.reply_text.call_args.args[0] == withdraw.ASK_AMOUNT_TEXT

    amount_msg = make_update("25")
    assert await withdraw.handle_withdraw_text(amount_msg, context)
    assert amount_msg.message.reply_text.call_args.args[0] == withdraw.ASK_ADDRESS_TEXT

    address_msg = make_update(DEST)
    assert await withdraw.handle_withdraw_text(address_msg, context)
    return address_msg


async def test_first_time_address_requires_explicit_confirm(settings, session_factory, onboard):
    """PRD F-4: first-time destination gets the explicit 'first time' confirm step."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)

    address_msg = await run_flow_to_confirm(context)

    text = address_msg.message.reply_text.call_args.args[0]
    assert "First time sending to this address" in text
    assert DEST in text
    markup = address_msg.message.reply_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "wd:yes"
    # Nothing has moved yet.
    context.application.bot_data["wallets"].withdraw.assert_not_awaited()


async def test_confirm_executes_and_remembers_address(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await run_flow_to_confirm(context)

    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once_with(
        USER_ID, DEST, Decimal("25")
    )
    text = confirm.callback_query.edit_message_text.call_args.args[0]
    assert "Withdrawal submitted" in text and TX_HASH in text

    async with session_factory() as session:
        row = await session.scalar(select(WithdrawalAddress))
    assert row is not None and row.address == DEST.lower()


async def test_known_address_skips_first_time_warning(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    async with session_factory() as session:
        session.add(WithdrawalAddress(telegram_user_id=USER_ID, address=DEST.lower()))
        await session.commit()

    address_msg = await run_flow_to_confirm(context)

    text = address_msg.message.reply_text.call_args.args[0]
    assert "First time" not in text
    assert "known address" in text


async def test_cancel_moves_no_funds(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await run_flow_to_confirm(context)

    cancel = make_callback_update("wd:no")
    await withdraw.confirm_callback(cancel, context)

    assert cancel.callback_query.edit_message_text.call_args.args[0] == withdraw.CANCELLED_TEXT
    context.application.bot_data["wallets"].withdraw.assert_not_awaited()


async def test_withdraw_in_group_redirects_without_details(settings, session_factory, onboard):
    update = make_update(chat_type="supergroup")
    context = make_context(settings, session_factory)

    await withdraw.withdraw(update, context)

    assert update.message.reply_text.call_args.args[0] == GROUP_REDIRECT_TEXT
    assert "withdraw" not in context.user_data


async def test_withdraw_works_while_trading_is_paused(settings, session_factory, onboard):
    """PRD F-25: the kill switch must never block withdrawals."""
    await onboard(USER_ID)
    async with session_factory() as session:
        session.add(TradingPauseFlag(id=1, paused=True))
        await session.commit()
    context = make_context(settings, session_factory)

    await run_flow_to_confirm(context)
    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once()
    text = confirm.callback_query.edit_message_text.call_args.args[0]
    assert "Withdrawal submitted" in text


async def test_bad_amount_and_address_are_rejected(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await withdraw.withdraw(make_update(), context)

    bad_amount = make_update("-5")
    await withdraw.handle_withdraw_text(bad_amount, context)
    assert bad_amount.message.reply_text.call_args.args[0] == withdraw.BAD_AMOUNT_TEXT

    ok_amount = make_update("25")
    await withdraw.handle_withdraw_text(ok_amount, context)

    bad_address = make_update("not-an-address")
    await withdraw.handle_withdraw_text(bad_address, context)
    assert bad_address.message.reply_text.call_args.args[0] == withdraw.BAD_ADDRESS_TEXT


# -- Stage 10: EIP-55 checksum rendering and validation (PRD J4) ---------------


async def test_lowercase_address_renders_checksummed(settings, session_factory, onboard):
    """A lowercase-entered address is displayed in its EIP-55 checksummed form."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)

    await withdraw.withdraw(make_update(), context)
    await withdraw.handle_withdraw_text(make_update("25"), context)
    address_msg = make_update(DEST.lower())
    await withdraw.handle_withdraw_text(address_msg, context)

    text = address_msg.message.reply_text.call_args.args[0]
    assert DEST in text  # DEST is the checksummed form
    assert DEST.lower() not in text.replace(DEST, "")

    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)
    receipt = confirm.callback_query.edit_message_text.call_args.args[0]
    assert DEST in receipt  # receipt checksummed too


async def test_invalid_mixed_case_checksum_is_rejected(settings, session_factory, onboard):
    """Mixed-case input carries a checksum claim; a wrong one is re-prompted."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await withdraw.withdraw(make_update(), context)
    await withdraw.handle_withdraw_text(make_update("25"), context)

    # Same address as DEST but with a deliberately wrong case pattern.
    bad = make_update("0xAbCd000000000000000000000000000000001234")
    assert await withdraw.handle_withdraw_text(bad, context)

    assert bad.message.reply_text.call_args.args[0] == withdraw.BAD_CHECKSUM_TEXT
    assert context.user_data["withdraw"]["step"] == "address"  # still waiting for a valid one
    context.application.bot_data["wallets"].withdraw.assert_not_awaited()


# -- Stage 10: first-time-address cooldown (PRD §7.3, D-13) ---------------------


def guarded(settings, minutes: int = 60):
    from dataclasses import replace

    return replace(settings, withdrawal_cooldown_minutes=minutes)


async def test_first_time_address_inside_cooldown_blocks_and_notifies(
    settings, session_factory, onboard
):
    """Inside the cooldown: no funds move, the address is registered, and a
    security notice DM is sent immediately."""
    await onboard(USER_ID)
    context = make_context(guarded(settings), session_factory)
    await run_flow_to_confirm(context)

    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_not_awaited()
    async with session_factory() as session:
        row = await session.scalar(select(WithdrawalAddress))
    assert row is not None and row.address == DEST.lower()

    notice = context.bot.send_message.call_args.kwargs["text"]
    assert "new withdrawal address" in notice.lower()
    assert "/export" in notice  # "if this wasn't you" escape hatch

    told = confirm.callback_query.edit_message_text.call_args.args[0]
    assert "cooldown" in told and "No funds have moved" in told


async def test_same_address_after_cooldown_window_works(settings, session_factory, onboard):
    """The registered address becomes withdrawable once the window has passed."""
    from datetime import UTC, datetime, timedelta

    await onboard(USER_ID)
    context = make_context(guarded(settings), session_factory)

    # First attempt registers the address (blocked, per the previous test).
    await run_flow_to_confirm(context)
    await withdraw.confirm_callback(make_callback_update("wd:yes"), context)
    context.application.bot_data["wallets"].withdraw.assert_not_awaited()

    # The cooldown elapses.
    async with session_factory() as session:
        row = await session.scalar(select(WithdrawalAddress))
        row.confirmed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=61)
        await session.commit()

    await run_flow_to_confirm(context)
    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once_with(
        USER_ID, DEST, Decimal("25")
    )
    assert "Withdrawal submitted" in confirm.callback_query.edit_message_text.call_args.args[0]


async def test_previously_used_address_is_unaffected_by_cooldown(
    settings, session_factory, onboard
):
    """An address with real history predates the cooldown entirely."""
    from datetime import UTC, datetime, timedelta

    await onboard(USER_ID)
    context = make_context(guarded(settings), session_factory)
    async with session_factory() as session:
        session.add(
            WithdrawalAddress(
                telegram_user_id=USER_ID,
                address=DEST.lower(),
                confirmed_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30),
            )
        )
        await session.commit()

    await run_flow_to_confirm(context)
    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()  # no security notice for known addresses


async def test_cooldown_zero_preserves_existing_behavior(settings, session_factory, onboard):
    """Default (0 = off): first-time address executes immediately and is
    recorded after the send, exactly as before Stage 10."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)  # default cooldown 0
    await run_flow_to_confirm(context)

    confirm = make_callback_update("wd:yes")
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once_with(
        USER_ID, DEST, Decimal("25")
    )
    context.bot.send_message.assert_not_awaited()  # no cooldown notice in off mode
    async with session_factory() as session:
        row = await session.scalar(select(WithdrawalAddress))
    assert row is not None and row.address == DEST.lower()
