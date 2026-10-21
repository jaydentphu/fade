"""/invite: the user's personal referral link (PRD J1, D-14).

Attribution and links only — the PRD explicitly defers points/rewards/token
mechanics, so there are none here. A "recruit" counts once they complete
the attestation gate (D-14: that's when they become a real user; a /start
that never attests is a drive-by, not a referral).

Group-safe: the link carries a Telegram user id, never an address or balance.
"""

from __future__ import annotations

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.trade import NOT_ONBOARDED_TEXT, user_onboarded
from fade.db.models import User

INVITE_TEXT = (
    "⚔️ Your recruitment link:\n"
    "{link}\n\n"
    "Recruits so far: {count}.\n"
    "Every friend you drag in is another name for the leaderboard to humble."
)


def referral_link(bot_username: str, telegram_user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref{telegram_user_id}"


def bot_referral_link(bot, telegram_user_id: int) -> str:
    """Referral link from a live Bot object, or "" if its username is unknown.

    Deliberately no fallback name: a guessed username would point the QR on
    a shared flex card at somebody else's bot.
    """
    try:
        username = bot.username
    except Exception:  # PTB raises if the bot was never initialized
        return ""
    if not isinstance(username, str) or not username:
        return ""
    return referral_link(username, telegram_user_id)


async def count_recruits(session_factory, telegram_user_id: int) -> int:
    """Users this person referred who COMPLETED attestation (D-14)."""
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.referred_by == telegram_user_id,
                User.attestation_accepted_at.is_not(None),
            )
        )
    return count or 0


async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return
    sessions = context.application.bot_data["sessions"]
    count = await count_recruits(sessions, update.effective_user.id)
    await update.message.reply_text(
        INVITE_TEXT.format(
            link=referral_link(context.bot.username, update.effective_user.id),
            count=count,
        )
    )
