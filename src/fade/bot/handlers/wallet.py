"""/wallet: address, balance, deposit instructions. DM-only per PRD F-7.

Money screen: plain and precise. Group chats never see addresses or balances.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.onboarding import GROUP_REDIRECT_TEXT, _dm_button

NOT_ONBOARDED_TEXT = "No wallet yet. Run /start first."

FETCHING_TEXT = "Fetching your balance..."

WALLET_TEXT = (
    "Wallet: `{address}`\n"
    "Balance: ${balance} (pUSD)\n\n"
    "To deposit, send USDC to your personal deposit address:\n"
    "Polygon/EVM chains: `{evm}`\n"
    "{extra_networks}"
    "Funds arrive as pUSD after bridge processing. "
    "You'll get a message here when a deposit is confirmed."
)


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=_dm_button(context.bot.username, "wallet"),
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
    evm = deposit_addresses.get("evm", "unavailable")
    extra = "".join(
        f"{network.upper()}: `{addr}`\n"
        for network, addr in sorted(deposit_addresses.items())
        if network != "evm"
    )
    await progress.edit_text(
        WALLET_TEXT.format(address=address, balance=balance, evm=evm, extra_networks=extra),
        parse_mode="Markdown",
    )
