"""Clan-vs-clan weekly league (PRD F-21).

Ranks every registered clan by aggregate weekly PnL% and announces results to
each clan's own chat on Sunday 18:00 ET — the same D-6 boundary the weekly
leaderboard resets on, so the recap and the reset always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from fade.bot.handlers.common import sanitize
from fade.db.models import Clan
from fade.social.broadcast import PendingMessage
from fade.social.leaderboard import compute_leaderboard, week_start

ANNOUNCEMENT_TEXT = (
    "🏁 Clan war, week settled.\n"
    "{title}: ranked #{rank} of {total} clans ({pnl_pct}% on the week).\n"
    "Top of the pile: {top_clan}.\n"
    "{mvp_line}\n"
    "New week starts now. /leaderboard to see who's carrying."
)
MVP_LINE = "Clan MVP: {mvp} ({mvp_pnl}$ realized). Buy them nothing, fade them often."
NO_MVP_LINE = "No trades this week. Cowardice is also a position."


@dataclass(frozen=True)
class ClanStanding:
    chat_id: int
    title: str
    pnl_pct: Decimal
    total_pnl: Decimal
    active: bool  # traded this week; inactive clans rank below ALL active ones
    mvp_username: str | None
    mvp_pnl: Decimal | None


async def compute_league(session_factory, now: datetime) -> list[ClanStanding]:
    """All registered clans ranked by aggregate weekly PnL% (volume-weighted:
    total realized PnL / total volume). Reuses the D-6 weekly window and the
    leaderboard's realization math wholesale."""
    since = week_start(now)
    async with session_factory() as session:
        clans = (await session.scalars(select(Clan))).all()

    standings: list[ClanStanding] = []
    for clan in clans:
        rows = await compute_leaderboard(session_factory, clan.chat_id, since=since)
        total_pnl = sum((r.realized_pnl for r in rows), Decimal(0))
        total_volume = sum((r.volume for r in rows), Decimal(0))
        pnl_pct = (
            (total_pnl / total_volume * 100).quantize(Decimal("0.1"))
            if total_volume
            else Decimal("0.0")
        )
        mvp = max(rows, key=lambda r: r.realized_pnl, default=None)
        standings.append(
            ClanStanding(
                chat_id=clan.chat_id,
                title=clan.title,
                pnl_pct=pnl_pct,
                total_pnl=total_pnl,
                active=total_volume > 0,
                mvp_username=mvp.username if mvp else None,
                mvp_pnl=mvp.realized_pnl if mvp else None,
            )
        )
    # Losing beats not playing: clans with zero weekly volume rank below every
    # clan that actually traded, however badly.
    standings.sort(key=lambda s: (s.active, s.pnl_pct, s.total_pnl), reverse=True)
    return standings


def render_announcement(standing: ClanStanding, rank: int, total: int, top: ClanStanding) -> str:
    if standing.mvp_username is not None:
        mvp_line = MVP_LINE.format(
            mvp=sanitize(standing.mvp_username, limit=40), mvp_pnl=f"{standing.mvp_pnl:+.2f}"
        )
    else:
        mvp_line = NO_MVP_LINE
    return ANNOUNCEMENT_TEXT.format(
        title=sanitize(standing.title, limit=60),
        rank=rank,
        total=total,
        pnl_pct=standing.pnl_pct,
        top_clan=sanitize(top.title, limit=60),
        mvp_line=mvp_line,
    )


async def announce_league(session_factory, queue, now: datetime) -> int:
    """Queue each clan's own result into its chat. Returns clans announced."""
    standings = await compute_league(session_factory, now)
    if not standings:
        return 0
    top = standings[0]
    for rank, standing in enumerate(standings, start=1):
        queue.enqueue(
            PendingMessage(
                chat_id=standing.chat_id,
                text=render_announcement(standing, rank, len(standings), top),
            )
        )
    return len(standings)
