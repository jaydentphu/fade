"""All-time trading record for one user (PRD F-22).

Reuses fade.social.leaderboard's realization math — realized_events for sells
plus Resolution rows for settlements — so /record can never disagree with the
leaderboard about the same trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from fade.db.models import FadeEvent, Order, Resolution, User
from fade.social.leaderboard import realized_events


@dataclass(frozen=True)
class UserRecord:
    username: str
    wins: int
    closed_trades: int
    biggest_win: Decimal | None
    biggest_loss: Decimal | None
    streak: int  # +N = N consecutive wins, -N = N consecutive losses, 0 = none
    most_faded_username: str | None
    most_faded_count: int

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.closed_trades if self.closed_trades else None


async def compute_record(session_factory, telegram_user_id: int) -> UserRecord | None:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.telegram_user_id == telegram_user_id, Order.status == "filled")
                .order_by(Order.created_at)
            )
        ).all()
        resolutions = (
            await session.scalars(
                select(Resolution).where(Resolution.telegram_user_id == telegram_user_id)
            )
        ).all()
        target_row = (
            await session.execute(
                select(FadeEvent.target_id, func.count())
                .where(FadeEvent.actor_id == telegram_user_id, FadeEvent.mode == "fd")
                .group_by(FadeEvent.target_id)
                .order_by(func.count().desc())
                .limit(1)
            )
        ).first()
        most_faded_username = None
        most_faded_count = 0
        if target_row is not None:
            target_user = await session.scalar(
                select(User).where(User.telegram_user_id == target_row[0])
            )
            most_faded_username = (
                target_user.username if target_user and target_user.username else str(target_row[0])
            )
            most_faded_count = target_row[1]

    # One merged, chronological stream of realized outcomes: sells (via the
    # leaderboard's own event function) and resolution settlements.
    events = realized_events(list(orders))
    events += [(r.resolved_at, r.pnl) for r in resolutions]
    events.sort(key=lambda e: e[0])
    pnls = [pnl for _, pnl in events]

    streak = 0
    for pnl in reversed(pnls):
        won = pnl > 0
        if streak == 0:
            streak = 1 if won else -1
        elif (streak > 0) == won:
            streak += 1 if won else -1
        else:
            break

    return UserRecord(
        username=user.username or str(telegram_user_id),
        wins=sum(1 for p in pnls if p > 0),
        closed_trades=len(pnls),
        biggest_win=max((p for p in pnls if p > 0), default=None),
        biggest_loss=min((p for p in pnls if p < 0), default=None),
        streak=streak,
        most_faded_username=most_faded_username,
        most_faded_count=most_faded_count,
    )
