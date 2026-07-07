"""/security discloses capabilities honestly (PRD §7.9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import security


async def test_security_command_discloses_capabilities_and_exit():
    update = MagicMock(spec=Update)
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await security.security(update, context)

    text = update.message.reply_text.call_args.args[0]
    # Can: trade. Never: unconfirmed moves. Honest risk. Exit right.
    assert "place and cancel orders" in text
    assert "you didn't confirm" in text
    assert "compromised" in text  # the honest residual-risk paragraph
    assert "/export" in text
    assert "withdrawals" in text.lower()
