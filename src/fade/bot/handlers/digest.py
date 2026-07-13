"""F-26: daily ops digest to the admin, plus the /stats on-demand view.

Ops copy is deliberately plain — this is instrumentation, not product voice.
Aggregates only: never a user's wallet address or balance (group-safety rule
applies to the admin DM too, since screenshots of it may travel).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from telegram.ext import ContextTypes

from fade.db.models import Deposit, Order, TradingPauseFlag, User, Withdrawal

DIGEST_TEXT = (
    "Fade daily digest — {date}\n"
    "New users (24h): {new_users}\n"
    "Active users (placed an order, 24h): {active_users}\n"
    "Filled orders (24h): {filled_orders} — ${filled_volume} volume\n"
    "Builder fees earned (24h): ${builder_fees}\n"
    "Failed/rejected orders (24h): {failed_orders}\n"
    "Deposits (24h): {deposits}\n"
    "Withdrawals (24h): {withdrawals}\n"
    "Trading paused: {paused}"
)


@dataclass(frozen=True)
class DailyDigest:
    new_users: int
    active_users: int
    filled_orders: int
    filled_volume: Decimal
    builder_fees: Decimal
    failed_orders: int  # failed + rejected
    deposits: int
    withdrawals: int
    paused: bool


async def compute_digest(session_factory, now: datetime) -> DailyDigest:
    """The last 24 hours, straight from the tables the app already writes."""
    since = (now - timedelta(hours=24)).astimezone(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        new_users = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= since)
        )
        active_users = await session.scalar(
            select(func.count(func.distinct(Order.telegram_user_id))).where(
                Order.created_at >= since
            )
        )
        filled_orders = await session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.status == "filled", Order.created_at >= since)
        )
        filled_volume = await session.scalar(
            select(func.coalesce(func.sum(Order.fill_usdc), 0)).where(
                Order.status == "filled", Order.created_at >= since
            )
        )
        builder_fees = await session.scalar(
            select(func.coalesce(func.sum(Order.fee), 0)).where(
                Order.status == "filled", Order.created_at >= since
            )
        )
        failed_orders = await session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.status.in_(("failed", "rejected")), Order.created_at >= since)
        )
        deposits = await session.scalar(
            select(func.count()).select_from(Deposit).where(Deposit.detected_at >= since)
        )
        withdrawals = await session.scalar(
            select(func.count()).select_from(Withdrawal).where(Withdrawal.created_at >= since)
        )
        flag = await session.get(TradingPauseFlag, 1)
    return DailyDigest(
        new_users=new_users or 0,
        active_users=active_users or 0,
        filled_orders=filled_orders or 0,
        filled_volume=Decimal(str(filled_volume)),
        builder_fees=Decimal(str(builder_fees)),
        failed_orders=failed_orders or 0,
        deposits=deposits or 0,
        withdrawals=withdrawals or 0,
        paused=bool(flag and flag.paused),
    )


def render_digest(digest: DailyDigest, now: datetime) -> str:
    return DIGEST_TEXT.format(
        date=f"{now:%Y-%m-%d}",
        new_users=digest.new_users,
        active_users=digest.active_users,
        filled_orders=digest.filled_orders,
        filled_volume=f"{digest.filled_volume:.2f}",
        builder_fees=f"{digest.builder_fees:.2f}",
        failed_orders=digest.failed_orders,
        deposits=digest.deposits,
        withdrawals=digest.withdrawals,
        paused="yes" if digest.paused else "no",
    )


class DigestSender:
    """Once-per-calendar-day gate around the digest DM.

    run_daily is the scheduler; this gate makes the send idempotent against
    scheduler duplicates (e.g. a restart re-registering jobs mid-day). `now`
    is injectable for tests, same pattern as the broadcast queue's clock.
    """

    def __init__(self, session_factory, admin_user_id: int, now=None) -> None:
        self._sessions = session_factory
        self._admin = admin_user_id
        self._now = now or (lambda: datetime.now(UTC))
        self._last_sent_day: date | None = None

    async def maybe_send(self, bot: Any) -> bool:
        if not self._admin:
            return False  # no admin configured: digest is off, not an error
        today = self._now().date()
        if self._last_sent_day == today:
            return False
        digest = await compute_digest(self._sessions, self._now())
        await bot.send_message(chat_id=self._admin, text=render_digest(digest, self._now()))
        self._last_sent_day = today
        return True


async def digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    sender: DigestSender = context.application.bot_data["digest_sender"]
    await sender.maybe_send(context.bot)
