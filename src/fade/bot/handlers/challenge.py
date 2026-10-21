"""Head-to-head challenges (PRD F-24).

`/challenge @user <market url>` in a group proposes a wager: the challenger
picks a side and size, the opponent accepts (auto-taking the OPPOSITE side,
the same one-market PvP as Fade) or declines. Acceptance executes both legs
through TradingService — FOK, slippage guard, builder code, idempotency —
no new order-placement logic.

Idempotency model:
- Each real Telegram update_id is consumed by at most one order (PRD §6):
  the challenger's leg reuses the side-pick callback's update_id (stored on
  the challenge row), the opponent's leg uses the accept tap's update_id.
- Every status transition is an atomic UPDATE ... WHERE status = <expected>
  (pending -> executing -> accepted, pending -> declined/expired,
  accepted -> settled), so a double tap, webhook retry, or second settle
  pass can never act twice.
- Settlement rides the Stage 6 resolution scanner: `settle_and_expire` (called
  from resolutions.scan_job) matches Resolution rows against accepted
  challenges' token pairs and announces the winner in the group exactly once.

Opponent identity (Stage 14): a text_mention entity (user without a username)
carries the real user id, captured at propose time — those challenges are
owner-locked by id. Plain @username challenges stay locked by username, which
means a pending challenge whose opponent RENAMES their username can no longer
be accepted by them; that's accepted behavior, not a bug: the challenger can
cancel it from the card (pending -> cancelled), and the 24h TTL expires it
regardless, so no challenge is ever stuck and no funds are ever locked up
(nothing moves before acceptance).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.cards import get_store
from fade.bot.handlers.clan import (
    GROUP_ONLY_TEXT,
    NEED_ACCOUNT_ALERT,
    NOT_YOUR_PICKER_ALERT,
    _is_group,
)
from fade.bot.handlers.common import sanitize
from fade.bot.handlers.trade import MARKET_CLOSED_TEXT, user_onboarded
from fade.db.models import Challenge, GroupChat, Resolution
from fade.errors import ErrorReporter, report_safely
from fade.trading.markets import MarketLookupError

logger = logging.getLogger(__name__)

CHALLENGE_TTL = timedelta(hours=24)
CHALLENGE_SIZES = ("10", "25", "50")

USAGE_TEXT = "Usage: /challenge @user <polymarket url>. Pick your fight properly."
SELF_CHALLENGE_TEXT = "You can't challenge yourself. Therapy is cheaper."
CARD_EXPIRED_ALERT = "That challenge card expired. Run /challenge again."
NOT_YOUR_CHALLENGE_ALERT = "This challenge isn't yours to answer."
NOT_YOUR_WITHDRAW_ALERT = "Only the challenger can withdraw this."
ALREADY_HANDLED_ALERT = "Too late — this challenge was already answered or expired."
WITHDRAWN_TEXT = (
    "🏳️ {challenger} withdrew the challenge against @{opponent}. No money moved."
)
CHALLENGE_EXPIRED_TEXT = (
    "⏳ Challenge expired unanswered: {challenger} vs @{opponent} on\n"
    "{question}\nNo money moved. Cowardice is also a position."
)
PROPOSE_TEXT = (
    "⚔️ {challenger} is calling out @{opponent}:\n"
    "{question}\n\n"
    "{challenger}: pick your side and size."
)
ACCEPT_PROMPT_TEXT = (
    "⚔️ Challenge live: {challenger} takes {side} ${size} on:\n"
    "{question}\n\n"
    "@{opponent}: Accept takes {opposite} ${size}. "
    "Market order, fill-or-kill. Slippage guard and 1% builder fee apply."
)
ACCEPTED_TEXT = (
    "🤝 It's on. {challenger} holds {side}, @{opponent} holds {opposite}, "
    "${size} a side on:\n{question}\n"
    "Winner takes the bragging rights. The market takes care of the rest."
)
DECLINED_TEXT = "🏳️ @{opponent} declined. {challenger}'s call stands unanswered. No money moved."
CHALLENGER_LEG_FAILED_TEXT = (
    "Challenge void: {challenger}'s order didn't fill ({reason}). Nothing was spent."
)
OPPONENT_LEG_FAILED_TEXT = (
    "Challenge void: {challenger}'s side filled but @{opponent}'s didn't ({reason}). "
    "{challenger} keeps their position; the wager is off."
)
PAUSED_TEXT = "Trading is paused right now. The challenge stays open; try again later."
SETTLED_TEXT = (
    "🏁 Challenge settled: {question}\n"
    "Resolved {outcome}. {winner} called it; {loser} paid ${size} for the lesson."
)


@dataclass
class ChallengeCard:
    """Proposal context cached until the challenger picks side and size."""

    chat_id: int
    challenger_id: int
    challenger_username: str
    opponent_username: str  # lowercase, no @ (display name for id-based)
    question: str
    yes_token: str
    no_token: str
    # Real user id when the opponent came from a text_mention (no username);
    # None for @username challenges. Default keeps cached cards revivable.
    opponent_id: int | None = None


def _born_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def _transition(session_factory, challenge_id: int, from_status: str, **values) -> bool:
    """Atomic status transition; False means someone else got there first."""
    async with session_factory() as session:
        result = await session.execute(
            sa_update(Challenge)
            .where(Challenge.id == challenge_id, Challenge.status == from_status)
            .values(**values)
        )
        await session.commit()
    return result.rowcount > 0


# -- /challenge command --------------------------------------------------------


async def challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/challenge @user <market url>: propose a head-to-head wager (F-24)."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_group(update):
        await update.message.reply_text(GROUP_ONLY_TEXT)
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NEED_ACCOUNT_ALERT)
        return

    opponent, url = None, None
    for arg in context.args or []:
        if arg.startswith("@") and len(arg) > 1:
            opponent = arg[1:]
        elif arg.startswith("http"):
            url = arg
    # A user without a username arrives as a text_mention entity carrying
    # their real id — sturdier than a username, so it wins when present.
    opponent_id: int | None = None
    entities = update.message.entities
    for entity in entities if isinstance(entities, (list, tuple)) else ():
        if getattr(entity, "type", "") == "text_mention" and entity.user is not None:
            opponent_id = entity.user.id
            opponent = entity.user.first_name or str(entity.user.id)
            break
    if opponent is None or url is None:
        await update.message.reply_text(USAGE_TEXT)
        return
    me = update.effective_user
    if opponent_id == me.id or (
        opponent_id is None and opponent.lower() == (me.username or "").lower()
    ):
        await update.message.reply_text(SELF_CHALLENGE_TEXT)
        return

    trading = context.application.bot_data["trading"]
    try:
        market = await trading.get_market_card(url)
    except MarketLookupError as error:
        await update.message.reply_text(str(error))
        return
    if not market.open:
        await update.message.reply_text(MARKET_CLOSED_TEXT)
        return

    card = ChallengeCard(
        chat_id=update.effective_chat.id,
        challenger_id=me.id,
        challenger_username=me.username or me.first_name,
        # Usernames are lowercased for the accept-lock match; a text_mention
        # opponent's display name is kept as-is (their lock is the id).
        opponent_username=opponent if opponent_id is not None else opponent.lower(),
        question=market.question,
        yes_token=market.yes_token,
        no_token=market.no_token,
        opponent_id=opponent_id,
    )
    key = await get_store(context).put("challenge", card)
    rows = [
        [
            InlineKeyboardButton(
                f"🟢 YES ${size}", callback_data=f"hp:{key}:{me.id}:y:{size}"
            )
            for size in CHALLENGE_SIZES
        ],
        [
            InlineKeyboardButton(f"🔴 NO ${size}", callback_data=f"hp:{key}:{me.id}:n:{size}")
            for size in CHALLENGE_SIZES
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"hx:{key}:{me.id}")],
    ]
    await update.message.reply_text(
        PROPOSE_TEXT.format(
            challenger=sanitize(card.challenger_username, limit=40),
            opponent=sanitize(card.opponent_username, limit=40),
            question=sanitize(card.question),
        ),
        reply_markup=InlineKeyboardMarkup(rows),
    )


# -- challenger picks side + size -----------------------------------------------


async def pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Side+size tapped: create the pending challenge, prompt the opponent."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    _, key, owner_id, side_letter, size = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer(NOT_YOUR_PICKER_ALERT, show_alert=True)
        return
    card = await get_store(context).get("challenge", key)
    if card is None:
        await query.answer(CARD_EXPIRED_ALERT, show_alert=True)
        return
    await query.answer()

    side = "YES" if side_letter == "y" else "NO"
    row = Challenge(
        chat_id=card.chat_id,
        challenger_id=card.challenger_id,
        challenger_username=card.challenger_username,
        opponent_username=card.opponent_username,
        opponent_id=card.opponent_id,  # pre-set only for text_mention opponents
        market_title=card.question[:200],
        yes_token=card.yes_token,
        no_token=card.no_token,
        challenger_side=side,
        size_usdc=Decimal(size),
        challenger_update_id=update.update_id,
        status="pending",
    )
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        session.add(row)
        await session.commit()
        challenge_id = row.id

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🤝 Accept", callback_data=f"ha:{challenge_id}"),
                InlineKeyboardButton("🏳️ Decline", callback_data=f"hd:{challenge_id}"),
            ],
            [InlineKeyboardButton("Withdraw challenge", callback_data=f"hc:{challenge_id}")],
        ]
    )
    await query.edit_message_text(
        ACCEPT_PROMPT_TEXT.format(
            challenger=sanitize(card.challenger_username, limit=40),
            opponent=sanitize(card.opponent_username, limit=40),
            side=side,
            opposite="NO" if side == "YES" else "YES",
            size=size,
            question=sanitize(card.question),
        ),
        reply_markup=keyboard,
    )


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    parts = query.data.split(":")
    if update.effective_user.id != int(parts[2]):
        await query.answer(NOT_YOUR_PICKER_ALERT, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("Challenge withdrawn. The beef stays theoretical.")


# -- opponent answers ------------------------------------------------------------


async def _load_challenge(session_factory, challenge_id: int) -> Challenge | None:
    async with session_factory() as session:
        return await session.get(Challenge, challenge_id)


async def _answer_guard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, challenge_id: int
) -> Challenge | None:
    """Common accept/decline checks. Returns the challenge or None (alerted)."""
    query = update.callback_query
    sessions = context.application.bot_data["sessions"]
    row = await _load_challenge(sessions, challenge_id)
    if row is None:
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return None
    tapper = update.effective_user
    # Owner lock: text_mention challenges carry the opponent's real id (set at
    # propose time) and lock on it; @username challenges lock on the username.
    if row.opponent_id is not None:
        if tapper.id != row.opponent_id:
            await query.answer(NOT_YOUR_CHALLENGE_ALERT, show_alert=True)
            return None
    elif (tapper.username or "").lower() != row.opponent_username:
        await query.answer(NOT_YOUR_CHALLENGE_ALERT, show_alert=True)
        return None
    if not await user_onboarded(context, tapper.id, tapper):
        await query.answer(NEED_ACCOUNT_ALERT, show_alert=True)
        return None
    if row.status != "pending":
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return None
    if _born_utc(row.created_at) < datetime.now(UTC) - CHALLENGE_TTL:
        if await _transition(sessions, challenge_id, "pending", status="expired"):
            await query.answer()
            await query.edit_message_text(
                CHALLENGE_EXPIRED_TEXT.format(
                    challenger=sanitize(row.challenger_username, limit=40),
                    opponent=sanitize(row.opponent_username, limit=40),
                    question=sanitize(row.market_title),
                )
            )
        else:
            await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return None
    return row


async def accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept tapped: execute both legs through TradingService."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    challenge_id = int(query.data.split(":")[1])
    sessions = context.application.bot_data["sessions"]
    row = await _answer_guard(update, context, challenge_id)
    if row is None:
        return
    # Claim the challenge before touching the CLOB: a double tap or webhook
    # retry loses this UPDATE and stops here, never re-executing.
    if not await _transition(sessions, challenge_id, "pending", status="executing"):
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return
    await query.answer()

    opponent = update.effective_user
    challenger_token = row.yes_token if row.challenger_side == "YES" else row.no_token
    opponent_token = row.no_token if row.challenger_side == "YES" else row.yes_token
    trading = context.application.bot_data["trading"]
    names = {
        "challenger": sanitize(row.challenger_username, limit=40),
        "opponent": sanitize(row.opponent_username, limit=40),
    }

    first = await trading.execute_market_order(
        telegram_user_id=row.challenger_id,
        telegram_update_id=row.challenger_update_id,
        token_id=challenger_token,
        market_title=row.market_title,
        side="BUY",
        amount=row.size_usdc,
    )
    if first.status == "paused" or first.status == "capped":
        await _transition(sessions, challenge_id, "executing", status="pending")
        await query.edit_message_text(PAUSED_TEXT if first.status == "paused" else first.error)
        return
    if first.status not in ("filled", "duplicate"):
        await _transition(sessions, challenge_id, "executing", status="failed")
        await query.edit_message_text(
            CHALLENGER_LEG_FAILED_TEXT.format(
                challenger=names["challenger"], reason=first.error or first.status
            )
        )
        return

    second = await trading.execute_market_order(
        telegram_user_id=opponent.id,
        telegram_update_id=update.update_id,
        token_id=opponent_token,
        market_title=row.market_title,
        side="BUY",
        amount=row.size_usdc,
    )
    if second.status not in ("filled", "duplicate"):
        await _transition(sessions, challenge_id, "executing", status="failed")
        await query.edit_message_text(
            OPPONENT_LEG_FAILED_TEXT.format(
                reason=second.error or second.status, **names
            )
        )
        return

    await _transition(
        sessions, challenge_id, "executing", status="accepted", opponent_id=opponent.id
    )
    await query.edit_message_text(
        ACCEPTED_TEXT.format(
            side=row.challenger_side,
            opposite="NO" if row.challenger_side == "YES" else "YES",
            size=row.size_usdc.quantize(Decimal("1")),
            question=sanitize(row.market_title),
            **names,
        )
    )


async def decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Decline tapped: cancel cleanly, no funds moved."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    challenge_id = int(query.data.split(":")[1])
    sessions = context.application.bot_data["sessions"]
    row = await _answer_guard(update, context, challenge_id)
    if row is None:
        return
    if not await _transition(sessions, challenge_id, "pending", status="declined"):
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        DECLINED_TEXT.format(
            challenger=sanitize(row.challenger_username, limit=40),
            opponent=sanitize(row.opponent_username, limit=40),
        )
    )


async def withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Challenger withdraws their own pending challenge: atomic
    pending -> cancelled, no funds moved. The lock is inverted vs. accept:
    ONLY the challenger may withdraw (the opponent has Decline)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    challenge_id = int(query.data.split(":")[1])
    sessions = context.application.bot_data["sessions"]
    row = await _load_challenge(sessions, challenge_id)
    if row is None:
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return
    if update.effective_user.id != row.challenger_id:
        await query.answer(NOT_YOUR_WITHDRAW_ALERT, show_alert=True)
        return
    if not await _transition(sessions, challenge_id, "pending", status="cancelled"):
        await query.answer(ALREADY_HANDLED_ALERT, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        WITHDRAWN_TEXT.format(
            challenger=sanitize(row.challenger_username, limit=40),
            opponent=sanitize(row.opponent_username, limit=40),
        )
    )


# -- settlement + expiry (driven by the Stage 6 resolution job) -------------------


async def _chat_deactivated(session_factory, chat_id: int) -> bool:
    """True when the chat is a clan the bot was removed from (GroupChat.active=False).
    Non-clan group chats have no Clan row and are never considered deactivated."""
    async with session_factory() as session:
        clan = await session.get(GroupChat, chat_id)
    return clan is not None and not clan.active


async def _send_to_group(session_factory, bot: Any, reporter, chat_id: int, text: str) -> bool:
    """Group send with Stage 11 isolation: skips deactivated clan chats, and a
    raising send is logged + reported without aborting the caller's loop."""
    if await _chat_deactivated(session_factory, chat_id):
        logger.info("skipping challenge message to deactivated clan %s", chat_id)
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as error:
        logger.exception("challenge message failed for chat %s", chat_id)
        await report_safely(reporter, error)
        return False


async def settle_and_expire(
    session_factory,
    bot: Any,
    now: datetime | None = None,
    reporter: ErrorReporter | None = None,
) -> int:
    """Settle accepted challenges whose market resolved; expire stale proposals.

    Called from resolutions.scan_job after each resolution scan. A challenge's
    outcome comes from the Resolution rows the scanner just recorded (either
    participant's row for either of the market's two tokens is enough to know
    the result). Returns the number of settlement messages posted.
    """
    now = now or datetime.now(UTC)
    cutoff = now - CHALLENGE_TTL

    async with session_factory() as session:
        pending = (
            await session.scalars(select(Challenge).where(Challenge.status == "pending"))
        ).all()
    for row in pending:
        if _born_utc(row.created_at) < cutoff:
            if await _transition(session_factory, row.id, "pending", status="expired"):
                await _send_to_group(
                    session_factory,
                    bot,
                    reporter,
                    row.chat_id,
                    CHALLENGE_EXPIRED_TEXT.format(
                        challenger=sanitize(row.challenger_username, limit=40),
                        opponent=sanitize(row.opponent_username, limit=40),
                        question=sanitize(row.market_title),
                    ),
                )

    async with session_factory() as session:
        accepted = (
            await session.scalars(select(Challenge).where(Challenge.status == "accepted"))
        ).all()
        outcomes: list[tuple[Challenge, str]] = []
        for row in accepted:
            resolution = await session.scalar(
                select(Resolution)
                .where(Resolution.token_id.in_((row.yes_token, row.no_token)))
                .limit(1)
            )
            if resolution is None:
                continue
            resolved_yes = (resolution.token_id == row.yes_token) == (
                resolution.final_price >= Decimal("0.5")
            )
            outcomes.append((row, "YES" if resolved_yes else "NO"))

    posted = 0
    for row, outcome in outcomes:
        challenger_won = row.challenger_side == outcome
        winner_id = row.challenger_id if challenger_won else row.opponent_id
        if not await _transition(
            session_factory,
            row.id,
            "accepted",
            status="settled",
            winner_id=winner_id,
            settled_at=now.astimezone(UTC).replace(tzinfo=None),
        ):
            continue  # another pass already settled it
        challenger = sanitize(row.challenger_username, limit=40)
        opponent = "@" + sanitize(row.opponent_username, limit=40)
        if await _send_to_group(
            session_factory,
            bot,
            reporter,
            row.chat_id,
            SETTLED_TEXT.format(
                question=sanitize(row.market_title),
                outcome=outcome,
                winner=challenger if challenger_won else opponent,
                loser=opponent if challenger_won else challenger,
                size=row.size_usdc.quantize(Decimal("1")),
            ),
        ):
            posted += 1
    return posted
