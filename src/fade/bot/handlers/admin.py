"""Kill switch: /pause and /resume, admin-only (PRD F-25).

Pausing blocks new orders only. Withdrawals, /wallet, /positions, and /export
keep working — enforced by the fact that none of them consult the pause flag.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

NOT_ADMIN_TEXT = "Nice try. This button belongs to the management."
PAUSED_TEXT = "Trading is now PAUSED. New orders are rejected; withdrawals stay live."
RESUMED_TEXT = "Trading is now LIVE again."


def _is_admin(context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int) -> bool:
    settings = context.application.bot_data["settings"]
    return settings.admin_user_id != 0 and telegram_user_id == settings.admin_user_id


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(context, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN_TEXT)
        return
    trading = context.application.bot_data["trading"]
    await trading.set_paused(True, update.effective_user.id)
    await update.message.reply_text(PAUSED_TEXT)


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(context, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN_TEXT)
        return
    trading = context.application.bot_data["trading"]
    await trading.set_paused(False, update.effective_user.id)
    await update.message.reply_text(RESUMED_TEXT)
