"""/positions with one-tap sell (PRD F-12). DM-only: positions reveal balances."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.cards import get_store
from fade.bot.handlers.common import GROUP_REDIRECT_TEXT, dm_button, sanitize
from fade.bot.handlers.trade import NOT_ONBOARDED_TEXT, render_result, user_onboarded

NO_POSITIONS_TEXT = "No open positions. Paste a Polymarket link to change that."
FETCHING_TEXT = "Fetching positions..."
NOT_YOUR_POSITION_ALERT = "That position belongs to someone else."
SELL_CONFIRM_TEXT = (
    "Confirm sell (market order, fill-or-kill):\n\n"
    "Sell {shares} {outcome} — {title}\n"
    "Slippage guard and Fade's 1% builder fee apply. Tap Confirm to execute."
)

# Partial sells (Stage 17). Share precision: fractions are quantized to the
# position ledger's 6 decimal places, ROUNDED DOWN so a partial sell can
# never exceed what's held. 100% uses the exact held amount, as always.
PARTIAL_PERCENTS = (25, 50)
SHARE_PRECISION = Decimal("0.000001")


def sell_amount(shares: Decimal, percent: int) -> Decimal:
    if percent >= 100:
        return Decimal(shares)
    return (Decimal(shares) * percent / 100).quantize(SHARE_PRECISION, rounding=ROUND_DOWN)


async def _cache_position(context: ContextTypes.DEFAULT_TYPE, view) -> str:
    return await get_store(context).put("position", view)


async def _get_position(context: ContextTypes.DEFAULT_TYPE, key: str):
    return await get_store(context).get("position", key)


def render_position_line(view) -> str:
    pnl = ""
    if view.cash_pnl is not None:
        sign = "+" if view.cash_pnl >= 0 else ""
        pnl = f" · PnL {sign}${view.cash_pnl:.2f}"
        if view.percent_pnl is not None:
            pnl += f" ({sign}{view.percent_pnl:.1f}%)"
    prices = ""
    if view.avg_price is not None and view.cur_price is not None:
        prices = f" · {view.avg_price:.2f} → {view.cur_price:.2f}"
    return f"{sanitize(view.title)}\n{view.outcome}: {view.shares} shares{prices}{pnl}"


async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "positions"),
        )
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    wallets = context.application.bot_data["wallets"]
    trading = context.application.bot_data["trading"]
    address = await wallets.get_wallet_address(update.effective_user.id)
    if address is None:
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    progress = await update.message.reply_text(FETCHING_TEXT)
    views = await trading.list_positions(address)
    if not views:
        await progress.edit_text(NO_POSITIONS_TEXT)
        return

    for view in views:
        # Stamp the card with its owner so sell taps can be owner-locked.
        view = replace(view, owner_id=update.effective_user.id)
        key = await _cache_position(context, view)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Sell", callback_data=f"ps:{key}")]])
        await update.message.reply_text(render_position_line(view), reply_markup=keyboard)
    await progress.delete()


async def sell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sell tapped: show a confirm step for selling the full position."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    key = query.data.split(":")[1]
    view = await _get_position(context, key)
    if view is None:
        await query.answer()
        await query.edit_message_text("That card expired. Run /positions again.")
        return
    if view.owner_id and update.effective_user.id != view.owner_id:
        await query.answer(NOT_YOUR_POSITION_ALERT, show_alert=True)
        return
    await query.answer()
    # Confirm defaults to the full position (today's behavior, the 100% path);
    # the second row offers partial sells, each with its own confirm step.
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"psc:{key}"),
                InlineKeyboardButton("Cancel", callback_data="tx"),
            ],
            [
                InlineKeyboardButton(f"Sell {pct}%", callback_data=f"psp:{key}:{pct}")
                for pct in PARTIAL_PERCENTS
            ],
        ]
    )
    await query.edit_message_text(
        SELL_CONFIRM_TEXT.format(
            shares=view.shares, outcome=view.outcome, title=sanitize(view.title)
        ),
        reply_markup=keyboard,
    )


async def sell_partial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A partial percentage was picked: show its own confirm step."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    _, key, pct_raw = query.data.split(":")
    percent = int(pct_raw)
    view = await _get_position(context, key)
    if view is None:
        await query.answer()
        await query.edit_message_text("That card expired. Run /positions again.")
        return
    if view.owner_id and update.effective_user.id != view.owner_id:
        await query.answer(NOT_YOUR_POSITION_ALERT, show_alert=True)
        return
    if percent not in PARTIAL_PERCENTS:  # forged callback: not offered, not sold
        await query.answer(NOT_YOUR_POSITION_ALERT, show_alert=True)
        return
    await query.answer()
    amount = sell_amount(view.shares, percent)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"psc:{key}:{percent}"),
                InlineKeyboardButton("Cancel", callback_data="tx"),
            ]
        ]
    )
    await query.edit_message_text(
        SELL_CONFIRM_TEXT.format(
            shares=f"{amount} ({percent}% of {view.shares})",
            outcome=view.outcome,
            title=sanitize(view.title),
        ),
        reply_markup=keyboard,
    )


async def sell_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    parts = query.data.split(":")
    key = parts[1]
    percent = int(parts[2]) if len(parts) > 2 else 100  # bare psc: = full sell
    view = await _get_position(context, key)
    if view is None:
        await query.answer()
        await query.edit_message_text("That card expired. Run /positions again.")
        return
    if view.owner_id and update.effective_user.id != view.owner_id:
        await query.answer(NOT_YOUR_POSITION_ALERT, show_alert=True)
        return
    if percent != 100 and percent not in PARTIAL_PERCENTS:
        await query.answer(NOT_YOUR_POSITION_ALERT, show_alert=True)
        return
    await query.answer()

    trading = context.application.bot_data["trading"]
    result = await trading.execute_market_order(
        telegram_user_id=update.effective_user.id,
        telegram_update_id=update.update_id,
        token_id=view.token_id,
        market_title=view.title,
        side="SELL",
        amount=sell_amount(view.shares, percent),
    )
    await render_result(query, result, f"Sold {view.outcome}", view.title)

    if result.status == "filled":
        # Late import: clan imports helpers from the trade module family.
        from fade.bot.handlers.clan import broadcast_fill

        user = update.effective_user
        await broadcast_fill(
            context,
            telegram_user_id=user.id,
            username=user.username or user.first_name,
            question=view.title,
            side_label=view.outcome.upper(),
            yes_token="",
            no_token="",
            amount_usdc=result.fill_usdc,
            price=result.fill_price,
            is_sell=True,
        )
