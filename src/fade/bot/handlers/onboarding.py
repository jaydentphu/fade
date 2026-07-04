"""Onboarding: /start attestation gate + wallet creation (PRD F-1, D-7).

The attestation gate and wallet-address screens are money-adjacent: plain and
precise, no jokes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.db.models import User

ATTEST_CALLBACK = "attest:accept"

ATTESTATION_TEXT = (
    "Before you can use Fade, confirm all of the following:\n\n"
    "1. I am 18 or older.\n"
    "2. I am not located in, or a resident of, any jurisdiction where using "
    "Polymarket is prohibited (including the United States), per Polymarket's "
    "Terms of Service: https://polymarket.com/tos\n"
    "3. I accept Polymarket's Terms of Service and Fade's terms.\n\n"
    "Tapping the button below records your acceptance."
)

WELCOME_BACK_TEXT = (
    "You're already in. Paste a Polymarket link when trading goes live, "
    "or /wallet to see your balance."
)

RESUME_SETUP_TEXT = (
    "Your acceptance is recorded but your wallet setup didn't finish. Tap below to complete it."
)

CREATING_TEXT = "Setting up your wallet. This takes a few seconds."

CREATION_FAILED_TEXT = (
    "Wallet setup failed. Nothing was lost — tap below to try again. "
    "If this keeps happening, contact the operator."
)

WALLET_READY_TEXT = (
    "Your wallet is ready.\n\n"
    "Address: `{address}`\n\n"
    "Use /wallet to see your balance and deposit instructions. "
    "Funds in this wallet are controlled by your own signing key; "
    "Fade's backend can place trades but cannot withdraw to an arbitrary address."
)

GROUP_REDIRECT_TEXT = "Wallets are a DM thing. Tap below and we'll talk in private."


def _dm_button(bot_username: str, payload: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start={payload}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("Open DM", url=url)]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=_dm_button(context.bot.username, "start"),
        )
        return

    sessions = context.application.bot_data["sessions"]
    wallets = context.application.bot_data["wallets"]
    async with sessions() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == update.effective_user.id)
        )
    if user is not None and user.onboarded:
        # Attested, but wallet creation may have failed midway: offer to resume.
        address = await wallets.get_wallet_address(update.effective_user.id)
        if address is not None:
            await update.message.reply_text(WELCOME_BACK_TEXT)
            return
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Finish wallet setup", callback_data=ATTEST_CALLBACK)]]
        )
        await update.message.reply_text(RESUME_SETUP_TEXT, reply_markup=keyboard)
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("I confirm all of the above", callback_data=ATTEST_CALLBACK)]]
    )
    await update.message.reply_text(ATTESTATION_TEXT, reply_markup=keyboard)


async def attest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    settings = context.application.bot_data["settings"]
    sessions = context.application.bot_data["sessions"]
    wallets = context.application.bot_data["wallets"]
    tg_user = update.effective_user

    async with sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == tg_user.id))
        if user is None:
            user = User(telegram_user_id=tg_user.id)
            session.add(user)
        user.username = tg_user.username
        if user.attestation_accepted_at is None:
            user.attestation_accepted_at = datetime.now(UTC)
            user.attestation_version = settings.attestation_version
        await session.commit()

    await query.edit_message_text(CREATING_TEXT)
    try:
        address = await wallets.create_wallet(tg_user.id)
    except Exception:
        logging.getLogger(__name__).exception("Wallet creation failed for user %s", tg_user.id)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Try again", callback_data=ATTEST_CALLBACK)]]
        )
        await query.edit_message_text(CREATION_FAILED_TEXT, reply_markup=keyboard)
        return
    await query.edit_message_text(WALLET_READY_TEXT.format(address=address), parse_mode="Markdown")
