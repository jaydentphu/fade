"""Paste-a-URL trading (PRD F-8, F-10, F-11, F-13).

Market cards, size picker ($10/$25/$50/custom), confirm step, execution.
Confirmation screens are money screens: plain and precise.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.cards import get_store
from fade.bot.handlers.common import edit_card, sanitize
from fade.db.models import User
from fade.trading.markets import MarketCard, MarketLookupError

logger = logging.getLogger(__name__)

PRESET_SIZES = ("10", "25", "50", "100", "250")
BUTTONS_PER_ROW = 3
MIN_ORDER_USD = Decimal("1")
MAX_ORDER_USD = Decimal("1000")

NOT_ONBOARDED_TEXT = "You need an account first. Run /start and accept the terms."
MARKET_CLOSED_TEXT = "That market isn't open for trading."
CARD_EXPIRED_TEXT = "That card expired (bot restarted). Paste the link again."
CUSTOM_PROMPT_TEXT = "Reply with the USD amount to spend (min $1, max $1000)."
BAD_AMOUNT_TEXT = "That's not a valid amount. Send a number between 1 and 1000."
CANCELLED_TEXT = "Cancelled. No order was placed."
PAUSED_TEXT = "Trading is paused right now. Withdrawals still work: /withdraw."
DUPLICATE_TEXT = "Already processed — that tap was a duplicate delivery, not a new order."

CONFIRM_TEXT = (
    "Confirm order (market order, fill-or-kill):\n\n"
    "{action} — {question}\n"
    "Spend: ${amount}\n"
    "Slippage guard and Fade's 1% builder fee apply. Tap Confirm to execute."
)

FILLED_TEXT = (
    "Filled.\n\n"
    "{action} — {question}\n"
    "Shares: {shares}\n"
    "Avg price: {price}\n"
    "Fee (builder): ${fee}\n"
    "Fee (Polymarket, estimated): {platform_fee}\n"
    "New balance: ${balance}"
)
# The fill response carries no platform-fee figure; the estimate comes from the
# market's fee params (F-13). When even that isn't available, say so plainly.
PLATFORM_FEE_UNAVAILABLE = "estimate unavailable"

REJECTED_TEXT = "Order not filled: {reason}. Nothing was spent."
FAILED_TEXT = "Order failed: {reason}. Nothing was spent."


async def cache_card(context: ContextTypes.DEFAULT_TYPE, card: MarketCard) -> str:
    return await get_store(context).put("market", card)


async def get_card(context: ContextTypes.DEFAULT_TYPE, key: str) -> MarketCard | None:
    return await get_store(context).get("market", key)


async def user_onboarded(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int, tg_user=None
) -> bool:
    """Onboarding gate; also keeps usernames fresh. Handlers that have the
    Telegram user object pass it, and a rename is picked up inside the SELECT
    this check already does — no extra queries per message. Leaderboards and
    /record then show the current name after the user's next interaction."""
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        current = getattr(tg_user, "username", None)
        if (
            user is not None
            and user.onboarded
            and isinstance(current, str)
            and current
            and current != user.username
        ):
            user.username = current
            await session.commit()
    return bool(user and user.onboarded)


def render_card_text(card: MarketCard) -> str:
    yes = f"{card.yes_price:.2f}" if card.yes_price is not None else "?"
    no = f"{card.no_price:.2f}" if card.no_price is not None else "?"
    ends = f"\nEnds: {card.end_date}" if card.end_date else ""
    return f"{sanitize(card.question)}\n\nYES {yes} · NO {no}{ends}"


def _side_buttons(key: str, side_letter: str, prefix: str) -> list[list[InlineKeyboardButton]]:
    """Amount buttons for one side, chunked so labels never truncate.

    Short labels ("YES $10") 3-per-row stay fully visible on a phone, unlike
    the old 4-wide "Buy YES $10" that Telegram cut to "Buy YE...".
    """
    buttons = [
        InlineKeyboardButton(f"{prefix} ${size}", callback_data=f"t:{key}:{side_letter}:{size}")
        for size in PRESET_SIZES
    ]
    buttons.append(
        InlineKeyboardButton(f"{prefix} custom", callback_data=f"t:{key}:{side_letter}:c")
    )
    return [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]


def card_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        _side_buttons(key, "y", "🟢 YES") + _side_buttons(key, "n", "🔴 NO")
    )


async def show_market_card(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Fetch a market by URL and post the trade card."""
    if update.message is None or update.effective_user is None:
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    trading = context.application.bot_data["trading"]
    try:
        card = await trading.get_market_card(url)
    except MarketLookupError as error:
        await update.message.reply_text(str(error))
        return
    if not card.open:
        await update.message.reply_text(MARKET_CLOSED_TEXT)
        return

    key = await cache_card(context, card)
    await send_market_card(update.message, card, key)


async def send_market_card(message, card: MarketCard, key: str) -> None:
    """Post the market card as a reply to `message`: the rendered image, the
    facts again as its caption (notification previews, search), and the side
    and size buttons under it. If rendering fails it falls back to the text
    card — trading must never depend on an image rendering."""
    from fade.social.trade_card import generate_market_card

    caption = render_card_text(card)
    keyboard = card_keyboard(key)
    try:
        # Rendering is CPU-bound (~200 ms): keep it off the event loop.
        image = await asyncio.to_thread(generate_market_card, card)
    except Exception:
        logger.exception("market card render failed; sending the text card")
        await message.reply_text(caption, reply_markup=keyboard)
        return
    await message.reply_photo(photo=image, caption=caption, reply_markup=keyboard)


async def size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A size button was tapped: show the confirm step (or ask for a custom amount)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    _, key, side_letter, size = query.data.split(":")
    card = await get_card(context, key)
    if card is None:
        await edit_card(query, CARD_EXPIRED_TEXT)
        return
    if size == "c":
        context.user_data["pending_custom"] = (key, side_letter)
        await query.message.reply_text(CUSTOM_PROMPT_TEXT)
        return
    await _show_confirm(query, card, key, side_letter, Decimal(size))


async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume a pending custom-amount reply. Returns True if it handled the text."""
    pending = context.user_data.get("pending_custom")
    if pending is None or update.message is None or update.message.text is None:
        return False
    key, side_letter = pending
    card = await get_card(context, key)
    try:
        amount = Decimal(update.message.text.strip().lstrip("$"))
    except InvalidOperation:
        await update.message.reply_text(BAD_AMOUNT_TEXT)
        return True
    if not (MIN_ORDER_USD <= amount <= MAX_ORDER_USD):
        await update.message.reply_text(BAD_AMOUNT_TEXT)
        return True
    del context.user_data["pending_custom"]
    if card is None:
        await update.message.reply_text(CARD_EXPIRED_TEXT)
        return True
    action = "Buy YES" if side_letter == "y" else "Buy NO"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"tc:{key}:{side_letter}:{amount}"),
                InlineKeyboardButton("Cancel", callback_data="tx"),
            ]
        ]
    )
    await update.message.reply_text(
        CONFIRM_TEXT.format(action=action, question=sanitize(card.question), amount=amount),
        reply_markup=keyboard,
    )
    return True


async def _show_confirm(query, card: MarketCard, key: str, side_letter: str, amount) -> None:
    action = "Buy YES" if side_letter == "y" else "Buy NO"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"tc:{key}:{side_letter}:{amount}"),
                InlineKeyboardButton("Cancel", callback_data="tx"),
            ]
        ]
    )
    await edit_card(
        query,
        CONFIRM_TEXT.format(action=action, question=sanitize(card.question), amount=amount),
        reply_markup=keyboard,
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm tapped: execute the order. update_id is the idempotency key."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await edit_card(query, NOT_ONBOARDED_TEXT)
        return
    _, key, side_letter, amount_raw = query.data.split(":")
    card = await get_card(context, key)
    if card is None:
        await edit_card(query, CARD_EXPIRED_TEXT)
        return

    trading = context.application.bot_data["trading"]
    token_id = card.yes_token if side_letter == "y" else card.no_token
    action = "Bought YES" if side_letter == "y" else "Bought NO"
    result = await trading.execute_market_order(
        telegram_user_id=update.effective_user.id,
        telegram_update_id=update.update_id,
        token_id=token_id,
        market_title=card.question,
        side="BUY",
        amount=Decimal(amount_raw),
    )
    await render_result(query, result, action, card.question)

    if result.status == "filled":
        # Late import: clan imports helpers from this module.
        from fade.bot.handlers.clan import broadcast_fill

        user = update.effective_user
        await broadcast_fill(
            context,
            telegram_user_id=user.id,
            username=user.username or user.first_name,
            question=card.question,
            side_label="YES" if side_letter == "y" else "NO",
            yes_token=card.yes_token,
            no_token=card.no_token,
            amount_usdc=result.fill_usdc,
            price=result.fill_price,
        )


async def render_result(query, result, action: str, question: str) -> None:
    if result.status == "filled":
        platform_fee = (
            f"${result.platform_fee}"
            if getattr(result, "platform_fee", None) is not None
            else PLATFORM_FEE_UNAVAILABLE
        )
        await edit_card(
            query,
            FILLED_TEXT.format(
                action=action,
                question=sanitize(question),
                shares=result.fill_shares,
                price=result.fill_price,
                fee=result.fee,
                platform_fee=platform_fee,
                balance=result.new_balance,
            )
        )
    elif result.status == "duplicate":
        await query.answer(DUPLICATE_TEXT)
    elif result.status == "paused":
        await edit_card(query, PAUSED_TEXT)
    elif result.status == "capped":
        # Money screen: the service's plain-language limit message, verbatim.
        await edit_card(query, result.error)
    elif result.status == "rejected":
        await edit_card(query, REJECTED_TEXT.format(reason=result.error))
    else:
        await edit_card(query, FAILED_TEXT.format(reason=result.error))


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await edit_card(query, CANCELLED_TEXT)
