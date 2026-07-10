"""Clan leaderboards (PRD F-20, D-6).

Realized PnL is computed from filled orders: each SELL realizes
(sell price - average buy price for that token) x shares sold. Win rate is per
closed trade (each SELL counts once; a win is positive realized PnL). Volume is
total filled notional (buys + sells). The weekly window starts Sunday 18:00
US Eastern (D-6), the same boundary as clan-war announcements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from fade.db.models import ClanMember, Order, Resolution, User

EASTERN = ZoneInfo("America/New_York")
RESET_WEEKDAY = 6  # Sunday
RESET_HOUR = 18  # 6pm


@dataclass(frozen=True)
class LeaderboardRow:
    telegram_user_id: int
    username: str
    realized_pnl: Decimal
    volume: Decimal
    wins: int
    closed_trades: int

    @property
    def win_rate(self) -> float | None:
        if self.closed_trades == 0:
            return None
        return self.wins / self.closed_trades


def week_start(now: datetime) -> datetime:
    """The most recent Sunday 18:00 US Eastern at or before `now`."""
    local = now.astimezone(EASTERN)
    days_back = (local.weekday() - RESET_WEEKDAY) % 7
    candidate = (local - timedelta(days=days_back)).replace(
        hour=RESET_HOUR, minute=0, second=0, microsecond=0
    )
    if candidate > local:
        candidate -= timedelta(days=7)
    return candidate


async def compute_leaderboard(
    session_factory, chat_id: int, since: datetime | None = None
) -> list[LeaderboardRow]:
    """Rank a clan's members by realized PnL over filled orders since `since`."""
    async with session_factory() as session:
        member_ids = list(
            await session.scalars(
                select(ClanMember.telegram_user_id).where(ClanMember.chat_id == chat_id)
            )
        )
        if not member_ids:
            return []
        query = select(Order).where(
            Order.telegram_user_id.in_(member_ids),
            Order.status == "filled",
        )
        if since is not None:
            # created_at is stored naive-UTC (server_default CURRENT_TIMESTAMP);
            # normalize the tz-aware boundary to match or the comparison is bogus.
            since_utc = since.astimezone(UTC).replace(tzinfo=None)
            query = query.where(Order.created_at >= since_utc)
        orders = (await session.scalars(query.order_by(Order.created_at))).all()
        # D-6's other half: positions realized by market RESOLUTION rather than
        # a manual sell (recorded by the F-14 resolution scanner).
        res_query = select(Resolution).where(Resolution.telegram_user_id.in_(member_ids))
        if since is not None:
            res_query = res_query.where(Resolution.resolved_at >= since_utc)
        resolutions = (await session.scalars(res_query)).all()
        users = {
            u.telegram_user_id: u
            for u in await session.scalars(
                select(User).where(User.telegram_user_id.in_(member_ids))
            )
        }

    rows: list[LeaderboardRow] = []
    for user_id in member_ids:
        own = [o for o in orders if o.telegram_user_id == user_id]
        pnl, volume, wins, closed = _realize(own)
        for res in resolutions:
            if res.telegram_user_id != user_id:
                continue
            # Each resolution closes one trade: settlement proceeds count as
            # volume, PnL was fixed at settlement, a win is positive PnL.
            pnl += res.pnl
            volume += res.shares * res.final_price
            closed += 1
            if res.pnl > 0:
                wins += 1
        if not own and not any(r.telegram_user_id == user_id for r in resolutions):
            continue
        user = users.get(user_id)
        name = user.username if user and user.username else str(user_id)
        rows.append(
            LeaderboardRow(
                telegram_user_id=user_id,
                username=name,
                realized_pnl=pnl,
                volume=volume,
                wins=wins,
                closed_trades=closed,
            )
        )
    rows.sort(key=lambda r: r.realized_pnl, reverse=True)
    return rows


def _realize(orders: list[Order]) -> tuple[Decimal, Decimal, int, int]:
    """Walk a user's fills chronologically, realizing PnL on each sell."""
    pnl = Decimal(0)
    volume = Decimal(0)
    wins = 0
    closed = 0
    # Per-token running average cost basis.
    basis: dict[str, tuple[Decimal, Decimal]] = {}  # token -> (shares, usdc cost)
    for order in orders:
        shares = order.fill_shares or Decimal(0)
        usdc = order.fill_usdc or Decimal(0)
        volume += usdc
        if order.side == "BUY":
            held_shares, held_cost = basis.get(order.token_id, (Decimal(0), Decimal(0)))
            basis[order.token_id] = (held_shares + shares, held_cost + usdc)
            continue
        # SELL: realize against average cost of what's held (if anything known).
        held_shares, held_cost = basis.get(order.token_id, (Decimal(0), Decimal(0)))
        avg_cost = (held_cost / held_shares) if held_shares else Decimal(0)
        realized = usdc - (avg_cost * shares)
        pnl += realized
        closed += 1
        if realized > 0:
            wins += 1
        remaining = max(held_shares - shares, Decimal(0))
        basis[order.token_id] = (remaining, avg_cost * remaining)
    return pnl, volume, wins, closed
