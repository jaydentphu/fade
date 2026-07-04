"""Handler tests with mocked Update/Context per project testing rules.

No test in this suite may hit a live network. The old placeholder /start test
moved to tests/bot/test_onboarding.py when real onboarding replaced it.
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


async def test_ping_replies():
    update = make_update()
    context = MagicMock()

    await basic.ping(update, context)

    update.message.reply_text.assert_awaited_once_with(basic.PING_TEXT)


async def test_ping_ignores_updates_without_message():
    update = MagicMock(spec=Update)
    update.message = None
    context = MagicMock()

    await basic.ping(update, context)
