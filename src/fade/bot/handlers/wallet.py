"""/wallet: address, balance, deposit instructions. DM-only per PRD F-7.

Money screen: plain and precise. Group chats never see addresses or balances.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import (
    GROUP_REDIRECT_TEXT,
    dm_button,
    format_balance,
    format_deposit_lines,
)

NOT_ONBOARDED_TEXT = "No wallet yet. Run /start first."

FETCHING_TEXT = "Fetching your balance..."

WALLET_TEXT = (
    "Wallet: `{address}`\n"
    "Balance: ${balance} (pUSD)\n\n"
    "To deposit, send USDC to your personal deposit address:\n"
    "{deposit_lines}\n\n"
    "Funds arrive as pUSD after bridge processing. "
    "You'll get a message here when a deposit is confirmed."
)


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "wallet"),
        )
        return

    wallets = context.application.bot_data["wallets"]
    address = await wallets.get_wallet_address(update.effective_user.id)
    if address is None:
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    progress = await update.message.reply_text(FETCHING_TEXT)
    balance = await wallets.get_balance(update.effective_user.id)
    deposit_addresses = await wallets.get_deposit_addresses(update.effective_user.id)
    await progress.edit_text(
        WALLET_TEXT.format(
            address=address,
            balance=format_balance(balance),
            deposit_lines=format_deposit_lines(deposit_addresses),
        ),
        parse_mode="Markdown",
    )
