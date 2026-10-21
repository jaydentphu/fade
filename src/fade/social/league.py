"""Clan standings and the weekly clan war (PRD F-21, D-6, D-15).

Two boards per window (today / this week / all time):
- $ PnL: the sum of members' realized PnL.
- % ROI: that PnL over the cost basis of the positions closed — capital
  weighted, so one member's $2 lottery ticket can't carry the clan.

Attribution: a closed trade (sell or resolution) counts for the clan its
trader was in WHEN IT CLOSED. Memberships are stints, so leaving doesn't
erase a clan's history and switching doesn't import PnL from the last one.
Realized PnL only, same math as the per-group leaderboard (D-6).

Announced Sunday 18:00 ET — the D-6 boundary the weekly board resets on.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from fade.bot.handlers.common import sanitize
from fade.db.models import Clan, ClanMembership, GroupChat, Order, Resolution, User
from fade.social.broadcast import PendingMessage
from fade.social.leaderboard import (
    EASTERN,
    RESET_HOUR,
    closed_trades,
    resolution_trade,
    to_naive_utc,
    week_start,
)

MIN_MEMBERS = 3  # pvp.trade's rule: a one-person "clan" isn't a clan
MIN_ROI_COST = Decimal("100")  # tiny samples can't top the % board
BOARD_SIZE = 10

WINDOWS = {"d": "Today", "w": "This week", "a": "All time"}
METRICS = {"roi": "% ROI", "usd": "$ PnL"}

ANNOUNCEMENT_TEXT = (
    "🏁 Clan war, week settled.\n"
    "{icon} {name}\n"
    "ROI board: {roi_rank}\n"
    "$ board: {usd_rank}\n"
    "Top of the pile: {top_clan}.\n"
    "{mvp_line}\n"
    "New week starts now. /clans to see the table."
)
MVP_LINE = "Clan MVP: {mvp} ({mvp_pnl} realized). Buy them nothing, fade them often."
MVP_HIDDEN_LINE = "Clan MVP: {mvp}. Size classified, damage confirmed."
NO_MVP_LINE = "No trades this week. Cowardice is also a position."
UNRANKED_MEMBERS = f"not ranked (needs {MIN_MEMBERS} members)"
UNRANKED_COST = f"not ranked (needs ${MIN_ROI_COST:.0f} of closed trades)"
NO_CLANS_TEXT = "No clans on the board yet. /clan to start one."


@dataclass(frozen=True)
class ClanStanding:
    clan_id: int
    name: str
    icon: str
    owner_id: int
    home_chat_id: int | None
    members: int
    pnl: Decimal
    cost: Decimal
    closed_trades: int
    mvp_username: str | None
    mvp_pnl: Decimal | None
    mvp_hides_amounts: bool = False

    @property
    def roi_pct(self) -> Decimal | None:
        if not self.cost:
            return None
        return (self.pnl / self.cost * 100).quantize(Decimal("0.1"))

    @property
    def ranked_usd(self) -> bool:
        return self.members >= MIN_MEMBERS

    @property
    def ranked_roi(self) -> bool:
        return self.ranked_usd and self.cost >= MIN_ROI_COST


def day_start(now: datetime) -> datetime:
    """The most recent 18:00 US Eastern at or before `now` (daily reset, on
    the same hour as the weekly one)."""
    local = now.astimezone(EASTERN)
    candidate = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if candidate > local:
        candidate -= timedelta(days=1)
    return candidate


def window_start(window: str, now: datetime) -> datetime | None:
    if window == "d":
        return day_start(now)
    if window == "w":
        return week_start(now)
    return None


async def compute_standings(
    session_factory, now: datetime, since: datetime | None = None
) -> list[ClanStanding]:
    """Every live clan's numbers for the window [since, now]; unsorted."""
    since_utc = to_naive_utc(since) if since is not None else None
    now_utc = to_naive_utc(now)
    async with session_factory() as session:
        clans = (await session.scalars(select(Clan).where(Clan.disbanded_at.is_(None)))).all()
        if not clans:
            return []
        clan_ids = [c.id for c in clans]
        stints = (
            await session.scalars(
                select(ClanMembership).where(ClanMembership.clan_id.in_(clan_ids))
            )
        ).all()
        user_ids = sorted({s.telegram_user_id for s in stints})
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.telegram_user_id.in_(user_ids), Order.status == "filled")
                .order_by(Order.created_at)
            )
        ).all()
        resolutions = (
            await session.scalars(
                select(Resolution).where(Resolution.telegram_user_id.in_(user_ids))
            )
        ).all()
        users = {
            u.telegram_user_id: u
            for u in await session.scalars(select(User).where(User.telegram_user_id.in_(user_ids)))
        }

    # Each user's closed trades inside the window, basis from FULL history.
    by_user_orders: dict[int, list[Order]] = defaultdict(list)
    for order in orders:
        by_user_orders[order.telegram_user_id].append(order)
    trades: dict[int, list] = defaultdict(list)
    for uid, own in by_user_orders.items():
        trades[uid].extend(closed_trades(own))
    for res in resolutions:
        trades[res.telegram_user_id].append(resolution_trade(res))

    def in_window(at: datetime) -> bool:
        at = to_naive_utc(at)
        return (since_utc is None or at >= since_utc) and at <= now_utc

    pnl: dict[int, Decimal] = defaultdict(Decimal)
    cost: dict[int, Decimal] = defaultdict(Decimal)
    count: dict[int, int] = defaultdict(int)
    per_member: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    open_members: dict[int, int] = defaultdict(int)
    for stint in stints:
        if stint.left_at is None:
            open_members[stint.clan_id] += 1
        start = to_naive_utc(stint.joined_at)
        end = to_naive_utc(stint.left_at) if stint.left_at is not None else None
        for trade in trades.get(stint.telegram_user_id, ()):
            if not trade.basis_known:
                continue  # proceeds with no recorded cost aren't profit
            at = to_naive_utc(trade.at)
            if not in_window(at) or at < start or (end is not None and at >= end):
                continue
            pnl[stint.clan_id] += trade.pnl
            cost[stint.clan_id] += trade.cost
            count[stint.clan_id] += 1
            per_member[stint.clan_id][stint.telegram_user_id] += trade.pnl

    standings = []
    for clan in clans:
        contributions = per_member.get(clan.id, {})
        mvp_id = max(contributions, key=contributions.get, default=None)
        mvp_user = users.get(mvp_id) if mvp_id is not None else None
        standings.append(
            ClanStanding(
                clan_id=clan.id,
                name=clan.name,
                icon=clan.icon,
                owner_id=clan.owner_id,
                home_chat_id=clan.home_chat_id,
                members=open_members.get(clan.id, 0),
                pnl=pnl.get(clan.id, Decimal(0)),
                cost=cost.get(clan.id, Decimal(0)),
                closed_trades=count.get(clan.id, 0),
                mvp_username=((mvp_user.username or str(mvp_id)) if mvp_id is not None else None),
                mvp_pnl=contributions.get(mvp_id) if mvp_id is not None else None,
                mvp_hides_amounts=bool(mvp_user and mvp_user.hide_amounts),
            )
        )
    return standings


def rank(standings: list[ClanStanding], metric: str) -> list[ClanStanding]:
    """Eligible clans in board order. Losing beats not playing: on the $ board
    clans that closed nothing rank below every clan that actually traded."""
    if metric == "roi":
        eligible = [s for s in standings if s.ranked_roi]
        return sorted(eligible, key=lambda s: (s.roi_pct, s.pnl), reverse=True)
    eligible = [s for s in standings if s.ranked_usd]
    return sorted(eligible, key=lambda s: (s.closed_trades > 0, s.pnl), reverse=True)


async def compute_league(session_factory, now: datetime) -> list[ClanStanding]:
    """This week's standings in ROI-board order, then the clans the ROI board
    can't rank yet (kept so every clan still gets its weekly result)."""
    standings = await compute_standings(session_factory, now, since=week_start(now))
    ranked = rank(standings, "roi")
    ranked_ids = {s.clan_id for s in ranked}
    rest = sorted(
        (s for s in standings if s.clan_id not in ranked_ids),
        key=lambda s: (s.pnl, s.members),
        reverse=True,
    )
    return ranked + rest


# -- rendering --------------------------------------------------------------------


def clan_label(icon: str, name: str, limit: int = 24) -> str:
    return f"{icon} {sanitize(name, limit=limit)}"


def _money(amount: Decimal) -> str:
    sign = "+" if amount >= 0 else "−"
    return f"{sign}${abs(amount):,.0f}"


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value)}%"


def _row(position: int, s: ClanStanding, metric: str) -> str:
    lead = _pct(s.roi_pct) if metric == "roi" else _money(s.pnl)
    other = _money(s.pnl) if metric == "roi" else _pct(s.roi_pct)
    members = f"{s.members} member" + ("" if s.members == 1 else "s")
    return f"{position}. {clan_label(s.icon, s.name)}  {lead} · {other} · {members}"


def render_board(
    standings: list[ClanStanding], window: str, metric: str, my_clan_id: int | None = None
) -> str:
    """Top 10, then the viewer's own clan if it isn't on the page (pvp.trade's
    pattern: you always see where you stand)."""
    header = f"🏆 Clans · {WINDOWS[window]} · {METRICS[metric]}"
    if not standings:
        return f"{header}\n\n{NO_CLANS_TEXT}"
    ranked = rank(standings, metric)
    lines = [header, ""]
    if not ranked:
        lines.append(f"Nobody qualifies yet. Clans need {MIN_MEMBERS}+ members to rank.")
    for position, s in enumerate(ranked[:BOARD_SIZE], start=1):
        lines.append(_row(position, s, metric))
    if my_clan_id is not None:
        mine = next((s for s in standings if s.clan_id == my_clan_id), None)
        position = next((i for i, s in enumerate(ranked, start=1) if s.clan_id == my_clan_id), None)
        if mine is not None and (position is None or position > BOARD_SIZE):
            lines.append("…")
            if position is not None:
                lines.append(_row(position, mine, metric))
            else:
                why = UNRANKED_COST if mine.ranked_usd else UNRANKED_MEMBERS
                lines.append(f"{clan_label(mine.icon, mine.name)}: {why}")
    footer = {"d": "Resets daily 6pm ET", "w": "Resets Sun 6pm ET", "a": "Since day one"}[window]
    rule = f"min {MIN_MEMBERS} members"
    if metric == "roi":
        rule += f" · min ${MIN_ROI_COST:.0f} closed"
    lines += ["", f"{footer} · {rule} · closed trades only"]
    return "\n".join(lines)


def describe_rank(standings: list[ClanStanding], clan_id: int, metric: str) -> str:
    """'#2 of 14 (+41.2%)' or why the clan isn't ranked."""
    mine = next((s for s in standings if s.clan_id == clan_id), None)
    if mine is None:
        return "—"
    ranked = rank(standings, metric)
    position = next((i for i, s in enumerate(ranked, start=1) if s.clan_id == clan_id), None)
    if position is None:
        return UNRANKED_COST if mine.ranked_usd else UNRANKED_MEMBERS
    value = _pct(mine.roi_pct) if metric == "roi" else _money(mine.pnl)
    return f"#{position} of {len(ranked)} ({value})"


def render_announcement(
    standing: ClanStanding, standings: list[ClanStanding], top: ClanStanding
) -> str:
    if standing.mvp_username is not None:
        mvp = sanitize(standing.mvp_username, limit=40)
        if standing.mvp_hides_amounts:
            mvp_line = MVP_HIDDEN_LINE.format(mvp=mvp)
        else:
            pnl = standing.mvp_pnl
            mvp_line = MVP_LINE.format(
                mvp=mvp, mvp_pnl=f"{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}"
            )
    else:
        mvp_line = NO_MVP_LINE
    return ANNOUNCEMENT_TEXT.format(
        icon=standing.icon,
        name=sanitize(standing.name, limit=40),
        roi_rank=describe_rank(standings, standing.clan_id, "roi"),
        usd_rank=describe_rank(standings, standing.clan_id, "usd"),
        top_clan=clan_label(top.icon, top.name, limit=40),
        mvp_line=mvp_line,
    )


async def announce_league(session_factory, queue, now: datetime) -> int:
    """Queue each clan's weekly result to its home chat — or, with no live
    home chat, to the owner's DM. Returns clans announced."""
    standings = await compute_league(session_factory, now)
    if not standings:
        return 0
    async with session_factory() as session:
        dead_chats = set(
            await session.scalars(select(GroupChat.chat_id).where(GroupChat.active.is_(False)))
        )
    top = standings[0]
    for standing in standings:
        chat_id = standing.home_chat_id
        if chat_id is None or chat_id in dead_chats:
            chat_id = standing.owner_id
        queue.enqueue(
            PendingMessage(chat_id=chat_id, text=render_announcement(standing, standings, top))
        )
    return len(standings)
