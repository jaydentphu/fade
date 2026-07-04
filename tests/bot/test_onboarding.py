"""Onboarding handlers with mocked Update/Context per project testing rules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import onboarding
from fade.db.models import User

FAKE_ADDRESS = "0xFadE0000000000000000000000000000000000aa"


def make_context(settings, session_factory, has_wallet: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.create_wallet = AsyncMock(return_value=FAKE_ADDRESS)
    wallets.get_wallet_address = AsyncMock(return_value=FAKE_ADDRESS if has_wallet else None)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
    }
    return context


def make_update(chat_type: str = "private", user_id: int = 301) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_user.id = user_id
    update.effective_user.username = "tester"
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


async def test_start_in_group_redirects_without_details(settings, session_factory):
    update = make_update(chat_type="group")
    context = make_context(settings, session_factory)

    await onboarding.start(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert text == onboarding.GROUP_REDIRECT_TEXT
    assert "0x" not in text


async def test_start_in_dm_shows_attestation_gate(settings, session_factory):
    update = make_update()
    context = make_context(settings, session_factory)

    await onboarding.start(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert text == onboarding.ATTESTATION_TEXT
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == onboarding.ATTEST_CALLBACK


async def test_attest_callback_records_user_and_creates_wallet(settings, session_factory):
    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = make_context(settings, session_factory)

    await onboarding.attest_callback(update, context)

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == 301))
    assert user is not None
    assert user.attestation_accepted_at is not None
    assert user.attestation_version == settings.attestation_version

    context.application.bot_data["wallets"].create_wallet.assert_awaited_once_with(301)
    final_text = update.callback_query.edit_message_text.call_args.args[0]
    assert FAKE_ADDRESS in final_text


async def test_start_after_onboarding_welcomes_back(settings, session_factory):
    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = make_context(settings, session_factory)
    await onboarding.attest_callback(update, context)

    update2 = make_update()
    await onboarding.start(update2, context)
    assert update2.message.reply_text.call_args.args[0] == onboarding.WELCOME_BACK_TEXT


async def test_start_offers_resume_when_wallet_creation_did_not_finish(settings, session_factory):
    # Attest with a failing wallet service: attestation persists, wallet doesn't.
    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = make_context(settings, session_factory, has_wallet=False)
    context.application.bot_data["wallets"].create_wallet = AsyncMock(
        side_effect=RuntimeError("relayer rejected")
    )

    await onboarding.attest_callback(update, context)

    # The failure is surfaced with a retry button, not a dead end.
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert text == onboarding.CREATION_FAILED_TEXT
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == onboarding.ATTEST_CALLBACK

    # And /start now offers to finish setup instead of claiming success.
    update2 = make_update()
    await onboarding.start(update2, context)
    assert update2.message.reply_text.call_args.args[0] == onboarding.RESUME_SETUP_TEXT


async def test_retry_after_failure_completes_wallet_setup(settings, session_factory):
    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = make_context(settings, session_factory, has_wallet=False)
    context.application.bot_data["wallets"].create_wallet = AsyncMock(
        side_effect=[RuntimeError("relayer rejected"), FAKE_ADDRESS]
    )

    await onboarding.attest_callback(update, context)  # fails
    await onboarding.attest_callback(update, context)  # retry succeeds

    final_text = update.callback_query.edit_message_text.call_args.args[0]
    assert FAKE_ADDRESS in final_text
