"""/wallet handler: DM-only enforcement and no leaks into groups."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import onboarding, wallet

FAKE_ADDRESS = "0xFadE0000000000000000000000000000000000aa"
FAKE_EVM_DEPOSIT = "0xDe9051700000000000000000000000000000BBbb"


def make_context(with_wallet: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=FAKE_ADDRESS if with_wallet else None)
    wallets.get_custody = AsyncMock(return_value="server")  # classic mode
    wallets.get_balance = AsyncMock(return_value=Decimal("12.5"))
    wallets.get_deposit_addresses = AsyncMock(
        return_value={"evm": FAKE_EVM_DEPOSIT, "svm": "SoLFakeAddr"}
    )
    context.application.bot_data = {"wallets": wallets}
    return context


def make_update(chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_user.id = 401
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    return update


async def test_wallet_in_group_never_leaks_address_or_balance():
    update = make_update(chat_type="supergroup")
    context = make_context()

    await wallet.wallet(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert text == onboarding.GROUP_REDIRECT_TEXT
    assert FAKE_ADDRESS not in text
    assert "12.5" not in text
    context.application.bot_data["wallets"].get_balance.assert_not_awaited()


async def test_wallet_in_dm_shows_address_balance_and_deposit_info():
    update = make_update()
    context = make_context()

    await wallet.wallet(update, context)

    progress = update.message.reply_text.return_value
    text = progress.edit_text.call_args.args[0]
    assert FAKE_ADDRESS in text
    assert "12.5" in text
    assert FAKE_EVM_DEPOSIT in text
    assert "Solana" in text  # extra networks listed with friendly labels


async def test_wallet_without_account_prompts_start():
    update = make_update()
    context = make_context(with_wallet=False)

    await wallet.wallet(update, context)

    assert update.message.reply_text.call_args.args[0] == wallet.NOT_ONBOARDED_TEXT
