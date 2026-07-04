"""Handler tests with mocked Update/Context per project testing rules.

No test in this suite may hit a live network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import basic


def make_update() -> Update:
    update = MagicMock(spec=Update)
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


async def test_start_replies_with_welcome():
    update = make_update()
    context = MagicMock()

    await basic.start(update, context)

    update.message.reply_text.assert_awaited_once_with(basic.START_TEXT)


async def test_ping_replies():
    update = make_update()
    context = MagicMock()

    await basic.ping(update, context)

    update.message.reply_text.assert_awaited_once_with(basic.PING_TEXT)


async def test_handlers_ignore_updates_without_message():
    update = MagicMock(spec=Update)
    update.message = None
    context = MagicMock()

    await basic.start(update, context)
    await basic.ping(update, context)
