"""/slippage: view and set the per-user worst-price guard (PRD F-10 UX).

users.slippage_bps has driven every order's guard since Phase 1; this is the
first time the user can see or change it. Money screen: plain copy. Presets
only — the callback re-validates against the preset list, so a forged
callback can never write an out-of-bounds value.
"""

from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.db.models import User

# Floor 0.5% (tighter rejects most real fills), ceiling 5% (looser stops
# being a guard). Anything outside this list is refused server-side too.
SLIPPAGE_PRESETS_BPS = (50, 100, 200, 500)

NOT_ONBOARDED_TEXT = "You need an account first. Run /start and accept the terms."
GROUP_TEXT = "Slippage is a personal setting. Message me directly: /slippage"
CURRENT_TEXT = (
    "Your slippage guard: {pct}% ({bps} bps).\n"
    "Market orders are rejected if the fill would be worse than this from "
    "the quoted price. Pick a new value:"
)
SET_TEXT = "Slippage guard set to {pct}% ({bps} bps). It applies from your next order."
INVALID_ALERT = "That value isn't offered. Pick one of the preset buttons."


def _pct(bps: int) -> str:
    return f"{bps / 100:g}"


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{_pct(bps)}%", callback_data=f"sl:{bps}")
                for bps in SLIPPAGE_PRESETS_BPS
            ]
        ]
    )


async def slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(GROUP_TEXT)
        return

    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == update.effective_user.id)
        )
    if user is None or not user.onboarded:
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    await update.message.reply_text(
        CURRENT_TEXT.format(pct=_pct(user.slippage_bps), bps=user.slippage_bps),
        reply_markup=_keyboard(),
    )


async def set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    try:
        bps = int(query.data.split(":")[1])
    except ValueError:
        await query.answer(INVALID_ALERT, show_alert=True)
        return
    if bps not in SLIPPAGE_PRESETS_BPS:  # bounds hold even for forged callbacks
        await query.answer(INVALID_ALERT, show_alert=True)
        return

    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == update.effective_user.id)
        )
        if user is None or not user.onboarded:
            await query.answer(NOT_ONBOARDED_TEXT, show_alert=True)
            return
        user.slippage_bps = bps
        await session.commit()
    await query.answer()
    await query.edit_message_text(SET_TEXT.format(pct=_pct(bps), bps=bps))
