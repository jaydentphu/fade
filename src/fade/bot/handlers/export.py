"""/export (PRD F-5): key export behind a warning flow. DM-only, obviously.

The exported key goes straight to its owner in a DM and is never logged.
Money screen: plain, precise, and appropriately scary.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import GROUP_REDIRECT_TEXT, dm_button

EXPORT_CALLBACK = "exp:go"

NOT_ONBOARDED_TEXT = "No wallet yet. Run /start first."

WARNING_TEXT = (
    "⚠️ You are about to export the private key that controls your funds.\n\n"
    "- Anyone who sees this key can take everything in your wallet, instantly "
    "and irreversibly.\n"
    "- Never paste it into websites, chats, or apps.\n"
    "- Fade cannot undo exposure or recover stolen funds.\n\n"
    "Only proceed if you understand and accept this."
)

KEY_TEXT = (
    "Your private key:\n\n"
    "`{key}`\n\n"
    "Save it somewhere safe OFFLINE, then delete this message. "
    "This key gives full, unilateral control of your wallet — with or without Fade."
)


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "export"),
        )
        return

    wallets = context.application.bot_data["wallets"]
    if await wallets.get_wallet_address(update.effective_user.id) is None:
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "I understand the risk — show my key", callback_data=EXPORT_CALLBACK
                )
            ]
        ]
    )
    await update.message.reply_text(WARNING_TEXT, reply_markup=keyboard)


async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    if update.effective_chat is not None and update.effective_chat.type != "private":
        return  # never reveal a key outside a DM, no matter how the tap arrived

    wallets = context.application.bot_data["wallets"]
    key = await wallets.export_key(update.effective_user.id)
    await query.edit_message_text(KEY_TEXT.format(key=key), parse_mode="Markdown")
