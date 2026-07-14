"""/history: the user's recent orders, from the table that always had them.

DM-only with the standard group redirect (order history is nobody else's
business, same rule as /positions). Titles are sanitized before rendering;
no balances or addresses appear here.
"""

from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import GROUP_REDIRECT_TEXT, dm_button, sanitize
from fade.bot.handlers.trade import NOT_ONBOARDED_TEXT, user_onboarded
from fade.db.models import Order

HISTORY_LIMIT = 10

HEADER = "Your last {count} orders (newest first):"
EMPTY_TEXT = "No orders yet. Paste a Polymarket link to change that."


def render_order_line(order: Order) -> str:
    if order.side == "BUY":
        amount = f"${order.amount}"
    else:
        amount = f"{order.amount} shares"
    line = f"{order.side} {sanitize(order.market_title, limit=60)} — {amount} — {order.status}"
    if order.status == "filled" and order.fill_price is not None:
        line += f" @ {order.fill_price}"
    return line


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if update.effective_chat is not None and update.effective_chat.type != "private":
        await update.message.reply_text(
            GROUP_REDIRECT_TEXT,
            reply_markup=dm_button(context.bot.username, "history"),
        )
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NOT_ONBOARDED_TEXT)
        return

    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.telegram_user_id == update.effective_user.id)
                .order_by(Order.id.desc())
                .limit(HISTORY_LIMIT)
            )
        ).all()

    if not orders:
        await update.message.reply_text(EMPTY_TEXT)
        return
    lines = [HEADER.format(count=len(orders))]
    lines.extend(f"{i}. {render_order_line(o)}" for i, o in enumerate(orders, start=1))
    await update.message.reply_text("\n".join(lines))
