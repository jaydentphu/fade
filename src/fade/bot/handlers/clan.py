"""Clans and the copy/fade loop (PRD F-16 through F-20).

Group messages never contain balances or wallet addresses. Trade sizes appear
only per the trader's privacy setting (F-17). Execution reuses TradingService,
so FOK, slippage guard, builder code, and idempotency all apply unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.cards import get_store
from fade.bot.handlers.common import sanitize
from fade.bot.handlers.trade import user_onboarded
from fade.db.models import Clan, ClanMember, User
from fade.social.broadcast import PendingMessage, hide_amounts_for, render_trade_card
from fade.social.leaderboard import EASTERN, compute_leaderboard, week_start

GROUP_ONLY_TEXT = "That's a group-chat move. Add me to your group and run it there."
SETUP_DONE_TEXT = (
    "⚔️ Clan registered: {title}\n"
    "Every member's trade gets broadcast here with Fade and Copy buttons. "
    "Members: /join to enlist. Rankings: /leaderboard."
)
ALREADY_SETUP_TEXT = "This clan already exists. Rivals don't need re-registering."
JOIN_NEED_ACCOUNT_TEXT = "You need an account first. DM me and run /start."
JOIN_NEED_SETUP_TEXT = "No clan here yet. Someone run /setup first."
JOINED_TEXT = "{who} joined the clan. Their calls are public record now."
ALREADY_JOINED_TEXT = "You're already in. Eager."
PRIVACY_ON_TEXT = "Amounts hidden in broadcasts. Your positions stay loud, your size stays quiet."
PRIVACY_OFF_TEXT = "Amounts visible in broadcasts. Full send."
NEED_ACCOUNT_ALERT = "You need an account first. DM me and run /start."
CARD_EXPIRED_ALERT = "That card expired. Trade again to make a new one."
NOT_YOUR_PICKER_ALERT = "That picker belongs to someone else. Tap Fade/Copy yourself."
NO_LEADERBOARD_TEXT = "No trades on the books yet. Someone do something."

PICKER_SIZES = ("10", "25", "50")

FADE_REPLY_TEXT = "⚔️ {actor} faded {orig}{size_text} at {price}. This will be remembered."
COPY_REPLY_TEXT = "📋 {actor} copied {orig}{size_text} at {price}. Flattery, probably."


@dataclass
class FadeCard:
    """Context cached per broadcast card so taps can execute later."""

    chat_id: int
    orig_user_id: int
    orig_username: str
    question: str
    side_label: str  # what the original trader bought: "YES" | "NO"
    yes_token: str
    no_token: str
    message_id: int | None = None
    extra: dict = field(default_factory=dict)


async def cache_fade_card(context: ContextTypes.DEFAULT_TYPE, card: FadeCard) -> str:
    return await get_store(context).put("fade", card)


async def get_fade_card(context: ContextTypes.DEFAULT_TYPE, key: str) -> FadeCard | None:
    return await get_store(context).get("fade", key)


def fade_copy_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚔️ Fade", callback_data=f"fd:{key}"),
                InlineKeyboardButton("📋 Copy", callback_data=f"cp:{key}"),
            ]
        ]
    )


def _is_group(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type in (
        "group",
        "supergroup",
    )


async def _add_member(session_factory, chat_id: int, telegram_user_id: int) -> bool:
    """Idempotently record clan membership. Returns True if newly added."""
    async with session_factory() as session:
        clan = await session.get(Clan, chat_id)
        if clan is None:
            return False
        existing = await session.get(ClanMember, (chat_id, telegram_user_id))
        if existing is not None:
            return False
        session.add(ClanMember(chat_id=chat_id, telegram_user_id=telegram_user_id))
        await session.commit()
        return True


# -- commands -----------------------------------------------------------------


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setup: register this group as a clan (PRD F-16)."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_group(update):
        await update.message.reply_text(GROUP_ONLY_TEXT)
        return
    if not await user_onboarded(context, update.effective_user.id):
        await update.message.reply_text(JOIN_NEED_ACCOUNT_TEXT)
        return

    sessions = context.application.bot_data["sessions"]
    chat = update.effective_chat
    async with sessions() as session:
        if await session.get(Clan, chat.id) is not None:
            await update.message.reply_text(ALREADY_SETUP_TEXT)
            return
        session.add(
            Clan(
                chat_id=chat.id,
                title=sanitize(chat.title or "Unnamed clan", limit=100),
                created_by=update.effective_user.id,
            )
        )
        await session.commit()
    await _add_member(sessions, chat.id, update.effective_user.id)
    await update.message.reply_text(
        SETUP_DONE_TEXT.format(title=sanitize(chat.title or "Unnamed clan", limit=100))
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/join: enlist in this group's clan so your trades broadcast here."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_group(update):
        await update.message.reply_text(GROUP_ONLY_TEXT)
        return
    if not await user_onboarded(context, update.effective_user.id):
        await update.message.reply_text(JOIN_NEED_ACCOUNT_TEXT)
        return

    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        if await session.get(Clan, update.effective_chat.id) is None:
            await update.message.reply_text(JOIN_NEED_SETUP_TEXT)
            return
    added = await _add_member(sessions, update.effective_chat.id, update.effective_user.id)
    if not added:
        await update.message.reply_text(ALREADY_JOINED_TEXT)
        return
    who = sanitize(update.effective_user.username or update.effective_user.first_name, limit=40)
    await update.message.reply_text(JOINED_TEXT.format(who=who))


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/privacy: toggle whether broadcasts show your dollar amounts (F-17)."""
    if update.message is None or update.effective_user is None:
        return
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == update.effective_user.id)
        )
        if user is None or not user.onboarded:
            await update.message.reply_text(JOIN_NEED_ACCOUNT_TEXT)
            return
        user.hide_amounts = not user.hide_amounts
        hidden = user.hide_amounts
        await session.commit()
    await update.message.reply_text(PRIVACY_ON_TEXT if hidden else PRIVACY_OFF_TEXT)


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/leaderboard: weekly + all-time clan rankings (PRD F-20)."""
    if update.message is None or update.effective_chat is None:
        return
    if not _is_group(update):
        await update.message.reply_text(GROUP_ONLY_TEXT)
        return
    sessions = context.application.bot_data["sessions"]
    now = datetime.now(tz=EASTERN)
    weekly = await compute_leaderboard(sessions, update.effective_chat.id, since=week_start(now))
    all_time = await compute_leaderboard(sessions, update.effective_chat.id)
    if not all_time:
        await update.message.reply_text(NO_LEADERBOARD_TEXT)
        return
    text = (
        "🏆 Leaderboard\n\n"
        + _render_board("This week (resets Sun 6pm ET)", weekly)
        + "\n"
        + _render_board("All time", all_time)
    )
    await update.message.reply_text(text)


def _render_board(title: str, rows) -> str:
    if not rows:
        return f"{title}:\n(no trades yet)\n"
    lines = [f"{title}:"]
    for rank, row in enumerate(rows[:10], start=1):
        sign = "+" if row.realized_pnl >= 0 else ""
        rate = f"{row.win_rate * 100:.0f}%" if row.win_rate is not None else "—"
        lines.append(
            f"{rank}. {sanitize(row.username, limit=24)}  "
            f"PnL {sign}${row.realized_pnl:.2f} · Vol ${row.volume:.2f} · Win {rate}"
        )
    return "\n".join(lines) + "\n"


# -- broadcast trigger (called from trade/positions handlers after a fill) ----


async def broadcast_fill(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    telegram_user_id: int,
    username: str,
    question: str,
    side_label: str,
    yes_token: str,
    no_token: str,
    amount_usdc: Decimal | None,
    price: Decimal | None,
    is_sell: bool = False,
) -> None:
    """Queue a trade card to every clan the trader belongs to (F-17)."""
    queue = context.application.bot_data.get("broadcasts")
    if queue is None:
        return
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        chat_ids = list(
            await session.scalars(
                select(ClanMember.chat_id).where(ClanMember.telegram_user_id == telegram_user_id)
            )
        )
    if not chat_ids:
        return
    hide = await hide_amounts_for(sessions, telegram_user_id)
    text = render_trade_card(
        who=username,
        question=question,
        side_label=side_label,
        amount_usdc=amount_usdc,
        price=price,
        hide_amounts=hide,
        is_sell=is_sell,
    )
    for chat_id in chat_ids:
        markup = None
        if not is_sell:
            card = FadeCard(
                chat_id=chat_id,
                orig_user_id=telegram_user_id,
                orig_username=username,
                question=question,
                side_label=side_label,
                yes_token=yes_token,
                no_token=no_token,
            )
            key = await cache_fade_card(context, card)
            markup = fade_copy_keyboard(key)

            async def _remember(message, card=card, key=key):
                # Persist the sent card's message_id so the F-19 rivalry reply
                # can thread onto it even after a restart.
                card.message_id = getattr(message, "message_id", None)
                await get_store(context).update("fade", key, card)

            queue.enqueue(
                PendingMessage(chat_id=chat_id, text=text, reply_markup=markup),
                on_sent=_remember,
            )
        else:
            queue.enqueue(PendingMessage(chat_id=chat_id, text=text))


# -- fade / copy taps ----------------------------------------------------------


async def fade_copy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⚔️ Fade / 📋 Copy tapped: show a size picker for the tapper (F-18)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    mode, key = query.data.split(":")  # fd | cp
    card = await get_fade_card(context, key)
    if card is None:
        await query.answer(CARD_EXPIRED_ALERT, show_alert=True)
        return
    if not await user_onboarded(context, update.effective_user.id):
        await query.answer(NEED_ACCOUNT_ALERT, show_alert=True)
        return
    await query.answer()

    tapper = update.effective_user
    sessions = context.application.bot_data["sessions"]
    await _add_member(sessions, card.chat_id, tapper.id)

    verb = "fade" if mode == "fd" else "copy"
    who = sanitize(tapper.username or tapper.first_name, limit=40)
    orig = sanitize(card.orig_username, limit=40)
    rows = [
        [
            InlineKeyboardButton(f"${size}", callback_data=f"s{mode[0]}:{key}:{tapper.id}:{size}")
            for size in PICKER_SIZES
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"gx:{key}:{tapper.id}")],
    ]
    await query.message.reply_text(
        f"{who}: pick your size to {verb} {orig}.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Size picked: show the confirm step (F-18: size -> confirm -> execute)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    prefix, key, owner_id, size = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer(NOT_YOUR_PICKER_ALERT, show_alert=True)
        return
    card = await get_fade_card(context, key)
    if card is None:
        await query.answer(CARD_EXPIRED_ALERT, show_alert=True)
        return
    await query.answer()
    mode = "fd" if prefix == "sf" else "cp"
    verb = "Fade" if mode == "fd" else "Copy"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"gc:{key}:{mode}:{owner_id}:{size}"),
                InlineKeyboardButton("Cancel", callback_data=f"gx:{key}:{owner_id}"),
            ]
        ]
    )
    await query.edit_message_text(
        f"{verb} {sanitize(card.orig_username, limit=40)} with ${size} on:\n"
        f"{sanitize(card.question)}\n"
        "Market order, fill-or-kill. Slippage guard and 1% builder fee apply.",
        reply_markup=keyboard,
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm tapped: execute opposite side (fade) or same side (copy)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    _, key, mode, owner_id, size = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer(NOT_YOUR_PICKER_ALERT, show_alert=True)
        return
    card = await get_fade_card(context, key)
    if card is None:
        await query.answer(CARD_EXPIRED_ALERT, show_alert=True)
        return
    await query.answer()

    # Copy buys the SAME side the original trader bought; Fade buys the OPPOSITE.
    original_token = card.yes_token if card.side_label == "YES" else card.no_token
    opposite_token = card.no_token if card.side_label == "YES" else card.yes_token
    token_id = opposite_token if mode == "fd" else original_token

    trading = context.application.bot_data["trading"]
    result = await trading.execute_market_order(
        telegram_user_id=update.effective_user.id,
        telegram_update_id=update.update_id,
        token_id=token_id,
        market_title=card.question,
        side="BUY",
        amount=Decimal(size),
    )

    if result.status == "duplicate":
        return
    if result.status == "paused":
        await query.edit_message_text(
            "Trading is paused right now. Withdrawals still work: DM me /withdraw."
        )
        return
    if result.status != "filled":
        reason = result.error or result.status
        await query.edit_message_text(f"Order not filled: {reason}. Nothing was spent.")
        return

    tapper = update.effective_user
    actor = sanitize(tapper.username or tapper.first_name, limit=40)
    orig = sanitize(card.orig_username, limit=40)
    hide = await hide_amounts_for(context.application.bot_data["sessions"], tapper.id)
    size_text = "" if hide else f" for ${size}"
    template = FADE_REPLY_TEXT if mode == "fd" else COPY_REPLY_TEXT
    reply = template.format(actor=actor, orig=orig, size_text=size_text, price=result.fill_price)

    await query.edit_message_text(reply)
    # F-19: also post as a reply to the original broadcast card, building the thread.
    queue = context.application.bot_data.get("broadcasts")
    if queue is not None and card.message_id is not None:
        queue.enqueue(
            PendingMessage(chat_id=card.chat_id, text=reply, reply_to_message_id=card.message_id)
        )


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    parts = query.data.split(":")
    if len(parts) == 3 and update.effective_user.id != int(parts[2]):
        await query.answer(NOT_YOUR_PICKER_ALERT, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("Cancelled. Cold feet noted.")
