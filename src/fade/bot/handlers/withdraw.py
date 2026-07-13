"""/withdraw (PRD F-4). DM-only. First-time addresses require an explicit confirm.

PRD §7.3 hardening (D-13): first-time destinations can carry a cooldown
(FADE_WITHDRAWAL_COOLDOWN_MINUTES, 0 = off). Registering a new address inside
the cooldown moves no funds, tells the user when they can complete it, and
sends an immediate security notice so a hijacked session can't drain quietly.
Addresses are EIP-55 checksummed wherever rendered (PRD J4).

Money screens throughout: plain and precise. Withdrawals never consult the
trading pause flag (PRD F-25): they must always work.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from eth_utils import is_checksum_address, to_checksum_address
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import GROUP_REDIRECT_TEXT, dm_button
from fade.db.models import Withdrawal, WithdrawalAddress
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
BAD_CHECKSUM_TEXT = (
    "That address fails its checksum — at least one character is mistyped. "
    "Paste it again exactly as your wallet shows it."
)
NEW_ADDRESS_REGISTERED_TEXT = (
    "New withdrawal address registered:\n\n"
    "`{address}`\n\n"
    "For security, first-time addresses have a {minutes}-minute cooldown before funds "
    "can be sent. Run /withdraw again after {ready} UTC to complete this withdrawal.\n"
    "No funds have moved."
)
NEW_ADDRESS_NOTICE_TEXT = (
    "Security notice: a new withdrawal address was just added to your account:\n\n"
    "`{address}`\n\n"
    "If this wasn't you, use /export now and move your funds."
)
COOLDOWN_ACTIVE_TEXT = (
    "This address is still in its security cooldown. "
    "You can withdraw to it after {ready} UTC. No funds have moved."
)
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
        hexpart = text[2:]
        if hexpart != hexpart.lower() and hexpart != hexpart.upper():
            # Mixed case carries an EIP-55 checksum claim: verify it.
            if not is_checksum_address(text):
                await update.message.reply_text(BAD_CHECKSUM_TEXT)
                return True
        address = to_checksum_address(text)
        state["address"] = address
        state["step"] = "confirm"
        known = await _known_address_row(context, update.effective_user.id, address) is not None
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
            template.format(address=address, amount=state["amount"]),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return True

    return False


async def _known_address_row(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, address: str
) -> WithdrawalAddress | None:
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        return await session.scalar(
            select(WithdrawalAddress).where(
                WithdrawalAddress.telegram_user_id == telegram_user_id,
                WithdrawalAddress.address == address.lower(),
            )
        )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


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
    settings = context.application.bot_data["settings"]

    # PRD §7.3 / D-13: first-time-destination cooldown. Never consults the
    # trading pause flag — withdrawal availability is untouched (PRD F-25);
    # this only staggers the FIRST send to a brand-new destination.
    cooldown = timedelta(minutes=settings.withdrawal_cooldown_minutes)
    row = await _known_address_row(context, user_id, address)
    now = datetime.now(UTC)
    if cooldown > timedelta(0):
        if row is None:
            async with sessions() as session:
                session.add(
                    WithdrawalAddress(
                        telegram_user_id=user_id, address=address.lower(), confirmed_at=now
                    )
                )
                await session.commit()
            await context.bot.send_message(
                chat_id=user_id,
                text=NEW_ADDRESS_NOTICE_TEXT.format(address=address),
                parse_mode="Markdown",
            )
            ready = now + cooldown
            await query.edit_message_text(
                NEW_ADDRESS_REGISTERED_TEXT.format(
                    address=address,
                    minutes=settings.withdrawal_cooldown_minutes,
                    ready=f"{ready:%Y-%m-%d %H:%M}",
                ),
                parse_mode="Markdown",
            )
            return
        ready = _aware(row.confirmed_at) + cooldown
        if now < ready:
            await query.edit_message_text(
                COOLDOWN_ACTIVE_TEXT.format(ready=f"{ready:%Y-%m-%d %H:%M}")
            )
            return

    try:
        tx_hash = await wallets.withdraw(user_id, address, amount)
    except InsufficientBalanceError as error:
        await query.edit_message_text(INSUFFICIENT_TEXT.format(detail=error))
        return
    except Exception as error:
        await query.edit_message_text(FAILED_TEXT.format(reason=str(error)[:150]))
        return

    async with sessions() as session:
        if row is None:  # cooldown off: record after first successful send, as before
            session.add(
                WithdrawalAddress(
                    telegram_user_id=user_id, address=address.lower(), confirmed_at=now
                )
            )
        # Receipt trail for every completed withdrawal (feeds the F-26 digest).
        session.add(
            Withdrawal(
                telegram_user_id=user_id,
                address=address.lower(),
                amount=amount,
                tx_hash=str(tx_hash)[:66],
            )
        )
        await session.commit()

    await query.edit_message_text(
        SENT_TEXT.format(amount=amount, address=address, tx=tx_hash),
        parse_mode="Markdown",
    )
