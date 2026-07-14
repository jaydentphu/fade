"""/wallet: address, balance, open-position value, deposit instructions.
DM-only per PRD F-7.

Money screen: plain and precise. Group chats never see addresses or balances.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import (
    GROUP_REDIRECT_TEXT,
    dm_button,
    format_balance,
    format_deposit_lines,
)

logger = logging.getLogger(__name__)

NOT_ONBOARDED_TEXT = "No wallet yet. Run /start first."

FETCHING_TEXT = "Fetching your balance..."

POSITIONS_VALUE_LINE = "Open positions: ${value}"
POSITIONS_VALUE_UNAVAILABLE = "Open positions: value unavailable right now."

# Client-custody wallets (PRD §7.8): the server can't sign, so it can't read
# the pUSD balance either. Say so plainly instead of crashing or pretending.
CLIENT_BALANCE_LINE = (
    "Balance: check your wallet app — Fade can't read client-custody wallets yet."
)
CLIENT_CUSTODY_NOTE = (
    "\nThis wallet's key lives on your device. Trading and withdrawals through "
    "Fade aren't supported for it yet — /security explains both custody modes."
)

WALLET_TEXT = (
    "Wallet: `{address}`\n"
    "{balance_line}\n"
    "{positions_line}\n\n"
    "To deposit, send USDC to your personal deposit address:\n"
    "{deposit_lines}\n\n"
    "Funds arrive as pUSD after bridge processing. "
    "You'll get a message here when a deposit is confirmed."
    "{custody_note}"
)


async def _positions_line(context: ContextTypes.DEFAULT_TYPE, address: str) -> str:
    """Total current value of open positions (PRD F-2), reusing the existing
    TradingService.list_positions path. A fetch failure degrades to a plain
    'unavailable' line — the balance must always still render."""
    trading = context.application.bot_data.get("trading")
    if trading is None:
        return POSITIONS_VALUE_UNAVAILABLE
    try:
        views = await trading.list_positions(address)
    except Exception:
        logger.warning("positions fetch failed for /wallet", exc_info=True)
        return POSITIONS_VALUE_UNAVAILABLE
    total = sum(
        (view.shares * view.cur_price for view in views if view.cur_price is not None),
        Decimal(0),
    )
    return POSITIONS_VALUE_LINE.format(value=f"{total:.2f}")


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
    custody = await wallets.get_custody(update.effective_user.id)
    if custody == "client":
        balance_line = CLIENT_BALANCE_LINE
        custody_note = CLIENT_CUSTODY_NOTE
    else:
        balance = await wallets.get_balance(update.effective_user.id)
        balance_line = f"Balance: ${format_balance(balance)} (pUSD)"
        custody_note = ""
    deposit_addresses = await wallets.get_deposit_addresses(update.effective_user.id)
    positions_line = await _positions_line(context, address)
    await progress.edit_text(
        WALLET_TEXT.format(
            address=address,
            balance_line=balance_line,
            positions_line=positions_line,
            deposit_lines=format_deposit_lines(deposit_addresses),
            custody_note=custody_note,
        ),
        parse_mode="Markdown",
    )
