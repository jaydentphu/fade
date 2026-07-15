"""/record [@user]: the permanent public record (PRD F-22).

Works in any chat: it shows trade RESULTS (the whole point of the product's
public-record thesis) but never a wallet address or balance, same group-safety
rule as every other social surface.
"""

from __future__ import annotations

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import sanitize
from fade.db.models import User
from fade.social.record import compute_record

USAGE_TEXT = "Usage: /record @username — or /record on its own for your own rap sheet."
NOT_FOUND_TEXT = "No record of that trader. Either they don't exist or they never dared."
NO_TRADES_TEXT = "{who} has no closed trades yet. All talk so far."

RECORD_TEXT = (
    "📜 The record: {who}\n"
    "Win rate: {win_rate} ({wins}W / {losses}L, {closed} closed)\n"
    "Biggest win: {biggest_win}\n"
    "Biggest loss: {biggest_loss}\n"
    "Current streak: {streak}\n"
    "Most faded: {most_faded}\n"
    "Recruits: {recruits}"
)


def _fmt_streak(streak: int) -> str:
    if streak > 0:
        return f"{streak} win{'s' if streak > 1 else ''} 🔥"
    if streak < 0:
        return f"{-streak} loss{'es' if streak < -1 else ''} 🧊"
    return "none"


async def record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    sessions = context.application.bot_data["sessions"]

    args = context.args or []
    if args:
        handle = args[0].lstrip("@").strip()
        if not handle:
            await update.message.reply_text(USAGE_TEXT)
            return
        async with sessions() as session:
            target = await session.scalar(
                select(User).where(func.lower(User.username) == handle.lower())
            )
        if target is None:
            await update.message.reply_text(NOT_FOUND_TEXT)
            return
        target_id = target.telegram_user_id
    else:
        target_id = update.effective_user.id

    user_record = await compute_record(sessions, target_id)
    if user_record is None:
        await update.message.reply_text(NOT_FOUND_TEXT)
        return

    who = sanitize(user_record.username, limit=40)
    if user_record.closed_trades == 0:
        await update.message.reply_text(NO_TRADES_TEXT.format(who=who))
        return

    win_rate = f"{user_record.win_rate * 100:.0f}%" if user_record.win_rate is not None else "—"
    most_faded = (
        f"{sanitize(user_record.most_faded_username, limit=40)} ({user_record.most_faded_count}x) ⚔️"
        if user_record.most_faded_username
        else "nobody yet. Pacifist."
    )
    await update.message.reply_text(
        RECORD_TEXT.format(
            who=who,
            win_rate=win_rate,
            wins=user_record.wins,
            losses=user_record.closed_trades - user_record.wins,
            closed=user_record.closed_trades,
            biggest_win=(
                f"+${user_record.biggest_win:.2f}" if user_record.biggest_win else "none yet"
            ),
            biggest_loss=(
                f"-${abs(user_record.biggest_loss):.2f}" if user_record.biggest_loss else "spotless"
            ),
            streak=_fmt_streak(user_record.streak),
            most_faded=most_faded,
            recruits=user_record.recruits,
        )
    )
