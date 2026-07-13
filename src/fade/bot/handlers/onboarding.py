"""Onboarding: /start attestation gate + wallet creation (PRD F-1, D-7).

The attestation gate is money-adjacent: plain and precise, no jokes. The
post-onboarding overview (balance, deposit addresses, commands) keeps a light
brand touch in the header only — amounts and addresses stay plain.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from fade.bot.handlers.basic import COMMANDS_TEXT
from fade.bot.handlers.common import (
    GROUP_REDIRECT_TEXT,
    dm_button,
    format_balance,
    format_deposit_lines,
)
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

RESUME_SETUP_TEXT = (
    "Your acceptance is recorded but your wallet setup didn't finish. Tap below to complete it."
)

CREATING_TEXT = "Setting up your wallet. This takes a few seconds."

MINIAPP_CUSTODY_TEXT = (
    "Attestation recorded.\n\n"
    "Tap below to create your wallet. The signing key is generated on YOUR "
    "device and never touches Fade's server — Fade cannot export, move, or "
    "recover it for you, so back it up when prompted."
)
MINIAPP_BUTTON_LABEL = "Create wallet (key stays on your device)"

CREATION_FAILED_TEXT = (
    "Wallet setup failed. Nothing was lost — tap below to try again. "
    "If this keeps happening, contact the operator."
)

OVERVIEW_HEADER_NEW = "⚔️ Welcome to Fade."
OVERVIEW_HEADER_BACK = "⚔️ Welcome back to Fade."

# The command list is the shared registry in basic.py (single source of
# truth with /help and the Telegram command menu — they can never drift).

OVERVIEW_TEXT = (
    "{header}\n\n"
    "💰 Balance: ${balance} (pUSD)\n\n"
    "Deposit (send USDC, arrives as pUSD):\n"
    "{deposit_lines}\n\n"
    "Wallet: `{address}`\n\n"
    "{commands}"
)


async def _render_overview(wallets, telegram_user_id: int, address: str, header: str) -> str:
    balance = await wallets.get_balance(telegram_user_id)
    deposit_addresses = await wallets.get_deposit_addresses(telegram_user_id)
    return OVERVIEW_TEXT.format(
        header=header,
        balance=format_balance(balance),
        deposit_lines=format_deposit_lines(deposit_addresses),
        address=address,
        commands=COMMANDS_TEXT,
    )


async def _route_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route a /start payload into the flow the user tapped for. True if routed."""
    payload = (context.args[0] if context.args else "").lower()
    if payload == "withdraw":
        from fade.bot.handlers.withdraw import withdraw

        await withdraw(update, context)
        return True
    if payload == "positions":
        from fade.bot.handlers.positions import positions

        await positions(update, context)
        return True
    if payload == "wallet":
        from fade.bot.handlers.wallet import wallet

        await wallet(update, context)
        return True
    if payload == "export":
        from fade.bot.handlers.export import export

        await export(update, context)
        return True
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "start"),
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
            # Deep-link payload routing: the group-redirect buttons send the
            # user here with the flow they actually asked for. Unknown or
            # missing payloads fall back to the overview. Non-onboarded users
            # never reach this branch — the attestation gate always wins.
            if await _route_deep_link(update, context):
                return
            text = await _render_overview(
                wallets, update.effective_user.id, address, OVERVIEW_HEADER_BACK
            )
            await update.message.reply_text(text, parse_mode="Markdown")
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

    # PRD §7.8 Mini App custody (flag-gated, default off): NEW users create
    # their key client-side instead of the server minting a signer. With the
    # flag off this branch does not exist — the classic flow is untouched.
    if settings.miniapp_custody and settings.miniapp_url:
        if await wallets.get_wallet_address(tg_user.id) is None:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            MINIAPP_BUTTON_LABEL, web_app=WebAppInfo(url=settings.miniapp_url)
                        )
                    ]
                ]
            )
            await query.edit_message_text(MINIAPP_CUSTODY_TEXT, reply_markup=keyboard)
            return

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

    text = await _render_overview(wallets, tg_user.id, address, OVERVIEW_HEADER_NEW)
    await query.edit_message_text(text, parse_mode="Markdown")
