"""Clan-vs-clan weekly league (PRD F-21): ranking math and announcements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from fade.db.models import Clan, ClanMember, Order, User
from fade.social.broadcast import BroadcastQueue
from fade.social.leaderboard import EASTERN
from fade.social.league import announce_league, compute_league

ALPHA_CHAT, BRAVO_CHAT, GHOST_CHAT = -111, -222, -333
ALICE, BOB = 991, 992

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=EASTERN)  # Friday: mid-week

_update_ids = count(99100)


async def seed_clans(session_factory) -> None:
    async with session_factory() as session:
        session.add(Clan(chat_id=ALPHA_CHAT, title="Alpha Apes", created_by=ALICE))
        session.add(Clan(chat_id=BRAVO_CHAT, title="Bravo Bears", created_by=BOB))
        session.add(Clan(chat_id=GHOST_CHAT, title="Ghost Town", created_by=1))
        for user_id, chat_id in ((ALICE, ALPHA_CHAT), (BOB, BRAVO_CHAT)):
            session.add(
                User(
                    telegram_user_id=user_id,
                    username=f"user{user_id}",
                    attestation_accepted_at=datetime.now(UTC),
                )
            )
            session.add(ClanMember(chat_id=chat_id, telegram_user_id=user_id))
        await session.commit()


async def seed_round_trip(session_factory, user_id: int, sell_usdc: str) -> None:
    """Buy $10 of 20 shares, sell all for `sell_usdc` — inside this week."""
    async with session_factory() as session:
        for side, usdc, at in (
            ("BUY", "10", NOW - timedelta(hours=5)),
            ("SELL", sell_usdc, NOW - timedelta(hours=4)),
        ):
            uid = next(_update_ids)
            session.add(
                Order(
                    telegram_user_id=user_id,
                    telegram_update_id=uid,
                    idempotency_key=f"k{uid}",
                    token_id="111",
                    market_title="Will it rain?",
                    side=side,
                    amount=Decimal(usdc),
                    status="filled",
                    fill_price=Decimal("0.5"),
                    fill_shares=Decimal("20"),
                    fill_usdc=Decimal(usdc),
                    created_at=at.astimezone(UTC).replace(tzinfo=None),
                )
            )
        await session.commit()


async def test_league_ranks_clans_by_aggregate_weekly_pnl_pct(settings, session_factory):
    """PRD F-21: clans ordered by aggregate weekly PnL%."""
    await seed_clans(session_factory)
    # Alpha (Alice): +$10 on $30 volume -> +33.3%. Bravo (Bob): -$4 on $16 -> -25%.
    await seed_round_trip(session_factory, ALICE, sell_usdc="20")
    await seed_round_trip(session_factory, BOB, sell_usdc="6")

    standings = await compute_league(session_factory, NOW)

    assert [s.title for s in standings] == ["Alpha Apes", "Bravo Bears", "Ghost Town"]
    assert standings[0].pnl_pct > standings[1].pnl_pct
    assert standings[0].mvp_username == f"user{ALICE}"
    assert standings[2].pnl_pct == Decimal("0.0")  # no trades, still ranked


async def test_announcement_names_rank_top_clan_and_mvp(settings, session_factory):
    """PRD F-21: each clan's own chat gets its rank, the leader, and its MVP."""
    await seed_clans(session_factory)
    await seed_round_trip(session_factory, ALICE, sell_usdc="20")  # Alpha wins
    await seed_round_trip(session_factory, BOB, sell_usdc="6")  # Bravo loses

    sent: list[dict] = []

    async def send(**kwargs):
        sent.append(kwargs)
        return type("Msg", (), {"message_id": len(sent)})()

    queue = BroadcastQueue(send=send)
    announced = await announce_league(session_factory, queue, NOW)
    await queue.drain_once()

    assert announced == 3
    by_chat = {m["chat_id"]: m["text"] for m in sent}

    bravo = by_chat[BRAVO_CHAT]
    assert "#2 of 3" in bravo  # own rank, out of all clans
    assert "Alpha Apes" in bravo  # the leading clan is named
    assert f"user{BOB}" in bravo  # own MVP callout (best of a bad week)
    assert "Clan MVP" in bravo

    alpha = by_chat[ALPHA_CHAT]
    assert "#1 of 3" in alpha
    assert f"user{ALICE}" in alpha

    ghost = by_chat[GHOST_CHAT]
    assert "#3 of 3" in ghost
    assert "No trades this week" in ghost  # no-MVP line for a silent clan
