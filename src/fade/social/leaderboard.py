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

from fade.db.models import GroupMember, Order, Resolution, User

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
                select(GroupMember.telegram_user_id).where(GroupMember.chat_id == chat_id)
            )
        )
        if not member_ids:
            return []
        # ALL filled orders, not just this window's: a sell this week realizes
        # against what it cost, and the buy may predate the window. Filtering
        # orders by date first made such a sell count its full proceeds as profit.
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.telegram_user_id.in_(member_ids), Order.status == "filled")
                .order_by(Order.created_at)
            )
        ).all()
        since_utc = to_naive_utc(since) if since is not None else None
        # D-6's other half: positions realized by market RESOLUTION rather than
        # a manual sell (recorded by the F-14 resolution scanner).
        res_query = select(Resolution).where(Resolution.telegram_user_id.in_(member_ids))
        if since_utc is not None:
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
        all_own = [o for o in orders if o.telegram_user_id == user_id]
        own = [o for o in all_own if since_utc is None or to_naive_utc(o.created_at) >= since_utc]
        pnl, volume, wins, closed = _realize(all_own, since_utc)
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


@dataclass(frozen=True)
class ClosedTrade:
    """One realized close: a sell, or a resolution. `cost` is what the closed
    shares cost to buy — the denominator of ROI (D-15)."""

    at: datetime
    pnl: Decimal
    cost: Decimal
    # False when the shares had no recorded buy (e.g. acquired outside the
    # bot): the "PnL" is then the whole proceeds. Clan scoring skips these.
    basis_known: bool = True


def closed_trades(orders: list[Order]) -> list[ClosedTrade]:
    """Per-sell realized PnL and cost basis, chronologically.

    THE single sell-realization calculation: the leaderboard totals it, /record
    (F-22) inspects individual events, and clan ROI divides by its cost — same
    numbers by construction.
    """
    trades: list[ClosedTrade] = []
    # Per-token running average cost basis.
    basis: dict[str, tuple[Decimal, Decimal]] = {}  # token -> (shares, usdc cost)
    for order in orders:
        shares = order.fill_shares or Decimal(0)
        usdc = order.fill_usdc or Decimal(0)
        if order.side == "BUY":
            held_shares, held_cost = basis.get(order.token_id, (Decimal(0), Decimal(0)))
            basis[order.token_id] = (held_shares + shares, held_cost + usdc)
            continue
        # SELL: realize against average cost of what's held (if anything known).
        held_shares, held_cost = basis.get(order.token_id, (Decimal(0), Decimal(0)))
        avg_cost = (held_cost / held_shares) if held_shares else Decimal(0)
        cost = avg_cost * shares
        known = held_shares > 0
        trades.append(ClosedTrade(order.created_at, usdc - cost, cost, basis_known=known))
        remaining = max(held_shares - shares, Decimal(0))
        basis[order.token_id] = (remaining, avg_cost * remaining)
    return trades


def resolution_trade(res: Resolution) -> ClosedTrade:
    """A settlement as a closed trade: cost is what the settled shares cost."""
    return ClosedTrade(at=res.resolved_at, pnl=res.pnl, cost=res.shares * res.avg_price)


def realized_events(orders: list[Order]) -> list[tuple[datetime, Decimal]]:
    """(when, realized PnL) per sell — closed_trades without the cost column."""
    return [(t.at, t.pnl) for t in closed_trades(orders)]


def to_naive_utc(moment: datetime) -> datetime:
    """Timestamps are stored naive-UTC on SQLite (server_default
    CURRENT_TIMESTAMP) and come back tz-aware from Postgres; compare them
    only after normalizing both sides to naive UTC."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _realize(
    orders: list[Order], since_utc: datetime | None = None
) -> tuple[Decimal, Decimal, int, int]:
    """Totals over the sells closed since `since_utc` (basis from full history)
    plus filled-notional volume of the orders placed in the window."""
    events = [
        t for t in closed_trades(orders) if since_utc is None or to_naive_utc(t.at) >= since_utc
    ]
    in_window = [o for o in orders if since_utc is None or to_naive_utc(o.created_at) >= since_utc]
    pnl = sum((t.pnl for t in events), Decimal(0))
    volume = sum((o.fill_usdc or Decimal(0) for o in in_window), Decimal(0))
    wins = sum(1 for t in events if t.pnl > 0)
    return pnl, volume, wins, len(events)
