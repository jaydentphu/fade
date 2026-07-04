"""/withdraw (PRD F-4). DM-only. First-time addresses require an explicit confirm.

Money screens throughout: plain and precise. Withdrawals never consult the
trading pause flag (PRD F-25): they must always work.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import GROUP_REDIRECT_TEXT, dm_button
from fade.db.models import WithdrawalAddress
from fade.wallets import InsufficientBalanceError

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

NOT_ONBOARDED_TEXT = "No wallet yet. Run /start first."
ASK_AMOUNT_TEXT = "Amount to withdraw, in USD (pUSD balance). Example: 25"
BAD_AMOUNT_TEXT = "That's not a valid amount. Send a positive number, e.g. 25"
ASK_ADDRESS_TEXT = (
    "Destination address (Polygon — you will receive USDC there).\n"
    "Double-check it: transfers cannot be reversed."
)
BAD_ADDRESS_TEXT = "That doesn't look like a valid address (0x + 40 hex characters). Try again."
FIRST_TIME_CONFIRM_TEXT = (
    "First time sending to this address:\n\n"
    "`{address}`\n\n"
    "Amount: ${amount}\n"
    "Confirm the withdrawal?"
)
REPEAT_CONFIRM_TEXT = "Withdraw ${amount} to known address `{address}` — confirm?"
SENT_TEXT = (
    "Withdrawal submitted.\n"
    "Amount: ${amount}\n"
    "Destination: `{address}`\n"
    "Tx: https://polygonscan.com/tx/{tx}\n"
    "USDC arrives after bridge processing."
)
INSUFFICIENT_TEXT = "Insufficient balance: {detail}."
FAILED_TEXT = "Withdrawal failed: {reason}. Your funds have not moved."
CANCELLED_TEXT = "Withdrawal cancelled. No funds moved."


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "withdraw"),
        )
        return

    wallets = context.application.bot_data["wallets"]
    if await wallets.get_wallet_address(update.effective_user.id) is None:
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    context.user_data["withdraw"] = {"step": "amount"}
    await update.message.reply_text(ASK_AMOUNT_TEXT)


async def handle_withdraw_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume a text reply belonging to an in-progress /withdraw. True if handled."""
    state = context.user_data.get("withdraw")
    if state is None or update.message is None or update.message.text is None:
        return False
    text = update.message.text.strip()

    if state["step"] == "amount":
        try:
            amount = Decimal(text.lstrip("$"))
        except InvalidOperation:
            await update.message.reply_text(BAD_AMOUNT_TEXT)
            return True
        if amount <= 0:
            await update.message.reply_text(BAD_AMOUNT_TEXT)
            return True
        state["amount"] = str(amount)
        state["step"] = "address"
        await update.message.reply_text(ASK_ADDRESS_TEXT)
        return True

    if state["step"] == "address":
        if not ADDRESS_RE.match(text):
            await update.message.reply_text(BAD_ADDRESS_TEXT)
            return True
        state["address"] = text
        state["step"] = "confirm"
        known = await _address_known(context, update.effective_user.id, text)
        template = REPEAT_CONFIRM_TEXT if known else FIRST_TIME_CONFIRM_TEXT
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm withdrawal", callback_data="wd:yes"),
                    InlineKeyboardButton("Cancel", callback_data="wd:no"),
                ]
            ]
        )
        await update.message.reply_text(
            template.format(address=text, amount=state["amount"]),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return True

    return False


async def _address_known(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, address: str
) -> bool:
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        row = await session.scalar(
            select(WithdrawalAddress).where(
                WithdrawalAddress.telegram_user_id == telegram_user_id,
                WithdrawalAddress.address == address.lower(),
            )
        )
    return row is not None


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    state = context.user_data.pop("withdraw", None)

    if query.data == "wd:no" or state is None or state.get("step") != "confirm":
        await query.edit_message_text(CANCELLED_TEXT)
        return

    amount = Decimal(state["amount"])
    address = state["address"]
    user_id = update.effective_user.id
    wallets = context.application.bot_data["wallets"]
    sessions = context.application.bot_data["sessions"]

    try:
        tx_hash = await wallets.withdraw(user_id, address, amount)
    except InsufficientBalanceError as error:
        await query.edit_message_text(INSUFFICIENT_TEXT.format(detail=error))
        return
    except Exception as error:
        await query.edit_message_text(FAILED_TEXT.format(reason=str(error)[:150]))
        return

    if not await _address_known(context, user_id, address):
        async with sessions() as session:
            session.add(
                WithdrawalAddress(
                    telegram_user_id=user_id,
                    address=address.lower(),
                    confirmed_at=datetime.now(UTC),
                )
            )
            await session.commit()

    await query.edit_message_text(
        SENT_TEXT.format(amount=amount, address=address, tx=tx_hash),
        parse_mode="Markdown",
    )
