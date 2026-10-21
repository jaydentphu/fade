"""Leaderboard math: realized PnL, volume, win rate, weekly reset boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

from fade.db.models import GroupChat, GroupMember, Order, User
from fade.social.leaderboard import EASTERN, compute_leaderboard, week_start

CHAT_ID = -100500
ALICE, BOB = 901, 902


def et(year, month, day, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN)


def test_week_start_is_most_recent_sunday_6pm_eastern():
    # Wednesday July 8 2026 noon ET -> Sunday July 5 2026 18:00 ET
    assert week_start(et(2026, 7, 8)) == et(2026, 7, 5, 18)
    # Sunday 17:59 is still the PREVIOUS week
    assert week_start(et(2026, 7, 5, 17, 59)) == et(2026, 6, 28, 18)
    # Sunday 18:00 exactly starts the new week
    assert week_start(et(2026, 7, 5, 18, 0)) == et(2026, 7, 5, 18)


def test_week_start_handles_other_timezones():
    utc_now = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert week_start(utc_now) == et(2026, 7, 5, 18)


async def seed_clan(session_factory, members: list[int]) -> None:
    async with session_factory() as session:
        session.add(GroupChat(chat_id=CHAT_ID, title="Degens", created_by=members[0]))
        for user_id in members:
            session.add(
                User(
                    telegram_user_id=user_id,
                    username=f"user{user_id}",
                    attestation_accepted_at=datetime.now(UTC),
                )
            )
            session.add(GroupMember(chat_id=CHAT_ID, telegram_user_id=user_id))
        await session.commit()


_update_ids = count(90000)


def make_order(
    user_id: int,
    side: str,
    usdc: str,
    shares: str,
    created_at: datetime,
    token: str = "111",
) -> Order:
    update_id = next(_update_ids)
    return Order(
        telegram_user_id=user_id,
        telegram_update_id=update_id,
        idempotency_key=f"k{update_id}",
        token_id=token,
        market_title="Will it rain?",
        side=side,
        amount=Decimal(usdc),
        status="filled",
        fill_price=Decimal("0.5"),
        fill_shares=Decimal(shares),
        fill_usdc=Decimal(usdc),
        created_at=created_at.astimezone(UTC).replace(tzinfo=None),
    )


async def test_leaderboard_ranks_by_realized_pnl_with_volume_and_win_rate(
    settings, session_factory
):
    """PRD F-20: realized PnL ranking with volume and win-rate columns."""
    await seed_clan(session_factory, [ALICE, BOB])
    now = et(2026, 7, 8)
    async with session_factory() as session:
        # Alice: buys $10 for 20 shares (avg 0.50), sells all for $15 -> +$5, win.
        session.add(make_order(ALICE, "BUY", "10", "20", now - timedelta(hours=3)))
        session.add(make_order(ALICE, "SELL", "15", "20", now - timedelta(hours=2)))
        # Bob: buys $10 for 20 shares, sells all for $8 -> -$2, loss.
        session.add(make_order(BOB, "BUY", "10", "20", now - timedelta(hours=3)))
        session.add(make_order(BOB, "SELL", "8", "20", now - timedelta(hours=2)))
        await session.commit()

    rows = await compute_leaderboard(session_factory, CHAT_ID)

    assert [r.telegram_user_id for r in rows] == [ALICE, BOB]  # ranked by PnL
    alice, bob = rows
    assert alice.realized_pnl == Decimal("5")
    assert alice.volume == Decimal("25")  # $10 buy + $15 sell
    assert alice.win_rate == 1.0
    assert bob.realized_pnl == Decimal("-2")
    assert bob.win_rate == 0.0


async def test_weekly_reset_drops_last_weeks_trades_when_clock_advances(settings, session_factory):
    """PRD F-20/D-6: the weekly board resets at Sunday 18:00 ET."""
    await seed_clan(session_factory, [ALICE])
    # A winning round trip on Saturday July 4 2026.
    saturday = et(2026, 7, 4, 12)
    async with session_factory() as session:
        session.add(make_order(ALICE, "BUY", "10", "20", saturday))
        session.add(make_order(ALICE, "SELL", "15", "20", saturday + timedelta(hours=1)))
        await session.commit()

    # Saturday evening: still this week -> trades count.
    before_reset = et(2026, 7, 4, 20)
    weekly = await compute_leaderboard(session_factory, CHAT_ID, since=week_start(before_reset))
    assert len(weekly) == 1 and weekly[0].realized_pnl == Decimal("5")

    # Advance the clock past Sunday 18:00 ET: weekly board is empty again...
    after_reset = et(2026, 7, 5, 18, 1)
    weekly = await compute_leaderboard(session_factory, CHAT_ID, since=week_start(after_reset))
    assert weekly == []

    # ...but the all-time board still remembers.
    all_time = await compute_leaderboard(session_factory, CHAT_ID)
    assert len(all_time) == 1 and all_time[0].realized_pnl == Decimal("5")


async def test_partial_sell_realizes_against_average_cost(settings, session_factory):
    await seed_clan(session_factory, [ALICE])
    now = et(2026, 7, 8)
    async with session_factory() as session:
        # Buy 20 shares for $10 (avg 0.50); sell 10 of them for $7 -> +$2 realized.
        session.add(make_order(ALICE, "BUY", "10", "20", now - timedelta(hours=2)))
        session.add(make_order(ALICE, "SELL", "7", "10", now - timedelta(hours=1)))
        await session.commit()

    rows = await compute_leaderboard(session_factory, CHAT_ID)
    assert rows[0].realized_pnl == Decimal("2")
    assert rows[0].closed_trades == 1


async def test_empty_clan_returns_no_rows(settings, session_factory):
    async with session_factory() as session:
        session.add(GroupChat(chat_id=CHAT_ID, title="Ghost town", created_by=1))
        await session.commit()
    assert await compute_leaderboard(session_factory, CHAT_ID) == []


async def test_weekly_sell_realizes_against_a_buy_from_before_the_week(settings, session_factory):
    """Regression (found building D-15): the weekly board filtered orders by
    date BEFORE computing cost basis, so selling this week a position bought
    last week counted the full $15 proceeds as profit instead of +$5."""
    await seed_clan(session_factory, [ALICE])
    async with session_factory() as session:
        session.add(make_order(ALICE, "BUY", "10", "20", et(2026, 7, 3)))  # last week
        session.add(make_order(ALICE, "SELL", "15", "20", et(2026, 7, 8)))  # this week
        await session.commit()

    weekly = await compute_leaderboard(session_factory, CHAT_ID, since=week_start(et(2026, 7, 8)))
    assert weekly[0].realized_pnl == Decimal("5")
    assert weekly[0].volume == Decimal("15")  # volume is still this week's orders only
    assert weekly[0].closed_trades == 1
