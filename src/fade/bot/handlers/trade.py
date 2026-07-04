"""Paste-a-URL trading (PRD F-8, F-10, F-11, F-13).

Market cards, size picker ($10/$25/$50/custom), confirm step, execution.
Confirmation screens are money screens: plain and precise.
"""

from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import sanitize
from fade.db.models import User
from fade.trading.markets import MarketCard, MarketLookupError

PRESET_SIZES = ("10", "25", "50")
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
    "New balance: ${balance}"
)

REJECTED_TEXT = "Order not filled: {reason}. Nothing was spent."
FAILED_TEXT = "Order failed: {reason}. Nothing was spent."


def cache_card(context: ContextTypes.DEFAULT_TYPE, card: MarketCard) -> str:
    cards = context.application.bot_data.setdefault("market_cards", {})
    key = secrets.token_hex(4)
    cards[key] = card
    return key


def get_card(context: ContextTypes.DEFAULT_TYPE, key: str) -> MarketCard | None:
    return context.application.bot_data.get("market_cards", {}).get(key)


async def user_onboarded(context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int) -> bool:
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    return bool(user and user.onboarded)


def render_card_text(card: MarketCard) -> str:
    yes = f"{card.yes_price:.2f}" if card.yes_price is not None else "?"
    no = f"{card.no_price:.2f}" if card.no_price is not None else "?"
    ends = f"\nEnds: {card.end_date}" if card.end_date else ""
    return f"{sanitize(card.question)}\n\nYES {yes} · NO {no}{ends}"


def card_keyboard(key: str) -> InlineKeyboardMarkup:
    def row(side_letter: str, label: str) -> list[InlineKeyboardButton]:
        buttons = [
            InlineKeyboardButton(f"{label} ${size}", callback_data=f"t:{key}:{side_letter}:{size}")
            for size in PRESET_SIZES
        ]
        buttons.append(
            InlineKeyboardButton(f"{label} custom", callback_data=f"t:{key}:{side_letter}:c")
        )
        return buttons

    return InlineKeyboardMarkup([row("y", "Buy YES"), row("n", "Buy NO")])


async def show_market_card(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Fetch a market by URL and post the trade card."""
    if update.message is None or update.effective_user is None:
        return
    if not await user_onboarded(context, update.effective_user.id):
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

    key = cache_card(context, card)
    await update.message.reply_text(render_card_text(card), reply_markup=card_keyboard(key))


async def size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A size button was tapped: show the confirm step (or ask for a custom amount)."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    _, key, side_letter, size = query.data.split(":")
    card = get_card(context, key)
    if card is None:
        await query.edit_message_text(CARD_EXPIRED_TEXT)
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
    card = get_card(context, key)
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
    await query.edit_message_text(
        CONFIRM_TEXT.format(action=action, question=sanitize(card.question), amount=amount),
        reply_markup=keyboard,
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm tapped: execute the order. update_id is the idempotency key."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    if not await user_onboarded(context, update.effective_user.id):
        await query.edit_message_text(NOT_ONBOARDED_TEXT)
        return
    _, key, side_letter, amount_raw = query.data.split(":")
    card = get_card(context, key)
    if card is None:
        await query.edit_message_text(CARD_EXPIRED_TEXT)
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


async def render_result(query, result, action: str, question: str) -> None:
    if result.status == "filled":
        await query.edit_message_text(
            FILLED_TEXT.format(
                action=action,
                question=sanitize(question),
                shares=result.fill_shares,
                price=result.fill_price,
                fee=result.fee,
                balance=result.new_balance,
            )
        )
    elif result.status == "duplicate":
        await query.answer(DUPLICATE_TEXT)
    elif result.status == "paused":
        await query.edit_message_text(PAUSED_TEXT)
    elif result.status == "rejected":
        await query.edit_message_text(REJECTED_TEXT.format(reason=result.error))
    else:
        await query.edit_message_text(FAILED_TEXT.format(reason=result.error))


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(CANCELLED_TEXT)
