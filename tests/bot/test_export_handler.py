"""/export: warning flow gates key material (PRD F-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import export
from fade.bot.handlers.common import GROUP_REDIRECT_TEXT

USER_ID = 804
WALLET = "0xFadE0000000000000000000000000000000000aa"
# Obviously fake test key, per project testing rules.
FAKE_KEY = "0x" + "ab" * 32


def make_context(settings) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=WALLET)
    wallets.export_key = AsyncMock(return_value=FAKE_KEY)
    context.application.bot_data = {"settings": settings, "wallets": wallets}
    return context


def make_update(chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_user.id = USER_ID
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def make_callback_update(chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_user.id = USER_ID
    update.callback_query = MagicMock()
    update.callback_query.data = export.EXPORT_CALLBACK
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_export_runs_warning_flow_before_returning_key_material(settings):
    """PRD F-5: the warning comes first; the key is NOT in the first response."""
    update = make_update()
    context = make_context(settings)

    await export.export(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "private key" in text.lower()
    assert FAKE_KEY not in text  # no key material in the warning screen
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == export.EXPORT_CALLBACK
    context.application.bot_data["wallets"].export_key.assert_not_awaited()


async def test_export_callback_reveals_key_only_after_acknowledgement(settings):
    update = make_callback_update()
    context = make_context(settings)

    await export.export_callback(update, context)

    context.application.bot_data["wallets"].export_key.assert_awaited_once_with(USER_ID)
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert FAKE_KEY in text
    assert "delete this message" in text.lower()


async def test_export_in_group_redirects_and_reveals_nothing(settings):
    update = make_update(chat_type="group")
    context = make_context(settings)

    await export.export(update, context)

    assert update.message.reply_text.call_args.args[0] == GROUP_REDIRECT_TEXT
    context.application.bot_data["wallets"].export_key.assert_not_awaited()


async def test_export_callback_refuses_outside_dm(settings):
    update = make_callback_update(chat_type="group")
    context = make_context(settings)

    await export.export_callback(update, context)

    context.application.bot_data["wallets"].export_key.assert_not_awaited()
    update.callback_query.edit_message_text.assert_not_awaited()


# -- Stage 10: the key message self-deletes ------------------------------------


async def test_key_message_schedules_auto_delete(settings):
    """Revealing the key schedules a one-shot delete of that exact message."""
    update = make_callback_update()
    update.callback_query.message.chat_id = USER_ID
    update.callback_query.message.message_id = 42
    context = make_context(settings)

    await export.export_callback(update, context)

    context.job_queue.run_once.assert_called_once()
    args, kwargs = context.job_queue.run_once.call_args
    assert args[1] == export.EXPORT_DELETE_AFTER_S
    assert kwargs["data"] == {"chat_id": USER_ID, "message_id": 42}
    # The copy warns the user it will happen.
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "self-delete" in text


async def test_delete_job_tolerates_already_deleted_message(settings):
    """The user deleting the message first must not crash the job."""
    context = MagicMock()
    context.bot.delete_message = AsyncMock(side_effect=Exception("message not found"))
    context.job.data = {"chat_id": USER_ID, "message_id": 42}

    await export._delete_key_message(context)  # must not raise

    context.bot.delete_message.assert_awaited_once()
