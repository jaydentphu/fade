"""Kill switch commands: admin-gated /pause and /resume (PRD F-25)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import admin

ADMIN_ID = 1  # matches settings fixture
RANDO_ID = 999


def make_context(settings) -> MagicMock:
    context = MagicMock()
    trading = MagicMock()
    trading.set_paused = AsyncMock()
    context.application.bot_data = {"settings": settings, "trading": trading}
    return context


def make_update(user_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


async def test_pause_rejects_non_admin(settings):
    update = make_update(RANDO_ID)
    context = make_context(settings)

    await admin.pause(update, context)

    assert update.message.reply_text.call_args.args[0] == admin.NOT_ADMIN_TEXT
    context.application.bot_data["trading"].set_paused.assert_not_awaited()


async def test_pause_and_resume_flip_the_flag_for_admin(settings):
    context = make_context(settings)

    await admin.pause(make_update(ADMIN_ID), context)
    context.application.bot_data["trading"].set_paused.assert_awaited_with(True, ADMIN_ID)

    await admin.resume(make_update(ADMIN_ID), context)
    context.application.bot_data["trading"].set_paused.assert_awaited_with(False, ADMIN_ID)
