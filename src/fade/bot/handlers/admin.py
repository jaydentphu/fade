"""Admin commands: /pause + /resume kill switch (PRD F-25), /stats (F-26).

Pausing blocks new orders only. Withdrawals, /wallet, /positions, and /export
keep working — enforced by the fact that none of them consult the pause flag.
"""

from __future__ import annotations

from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

NOT_ADMIN_TEXT = "Nice try. This button belongs to the management."
PAUSED_TEXT = "Trading is now PAUSED. New orders are rejected; withdrawals stay live."
RESUMED_TEXT = "Trading is now LIVE again."
STATS_DM_ONLY_TEXT = "Stats are DM-only. Message me directly."
CLAN_RESET_USAGE_TEXT = "Usage: /clanreset <clan name>"
CLAN_RESET_MISSING_TEXT = "No live clan by that name."
CLAN_RESET_DONE_TEXT = "Reset: name is now {name}; photo and motto cleared."


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


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats: the F-26 digest on demand. Admin-only, DM-only — the numbers
    are aggregates, but they're still nobody else's business."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(context, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN_TEXT)
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(STATS_DM_ONLY_TEXT)
        return

    # Same computation as the daily digest job — one source of truth.
    from fade.bot.handlers.digest import compute_digest, render_digest

    now = datetime.now(UTC)
    digest = await compute_digest(context.application.bot_data["sessions"], now)
    await update.message.reply_text(render_digest(digest, now))


async def clan_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clanreset <name>: moderation for user-made clan identity (D-15). Wipes
    the photo and motto and renames to a neutral placeholder. Admin only."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(context, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN_TEXT)
        return
    from sqlalchemy import select

    from fade.db.models import Clan
    from fade.social import clans

    name = " ".join(context.args or [])
    if not name:
        await update.message.reply_text(CLAN_RESET_USAGE_TEXT)
        return
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        clan_id = await session.scalar(
            select(Clan.id).where(
                Clan.name_key == clans.name_key(name), Clan.disbanded_at.is_(None)
            )
        )
    clan = await clans.admin_reset(sessions, clan_id) if clan_id is not None else None
    if clan is None:
        await update.message.reply_text(CLAN_RESET_MISSING_TEXT)
        return
    await update.message.reply_text(CLAN_RESET_DONE_TEXT.format(name=clan.name))
