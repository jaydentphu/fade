"""Clan standings and the weekly clan war (PRD F-21, D-15).

Rewritten for D-15: clans are teams, not group chats. The old tests ranked
group chats by PnL / volume; that model no longer exists. Their intents are
kept — clans are ranked, the result names your rank, the top clan and your
MVP, and a silent clan still hears about its week — on the new rules: % ROI
over closed cost basis, $ PnL, a 3-member floor, and stint attribution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from fade.db.models import Clan, ClanMembership, GroupChat, Order, Resolution, User
from fade.social.broadcast import BroadcastQueue
from fade.social.leaderboard import EASTERN, week_start
from fade.social.league import (
    MIN_MEMBERS,
    announce_league,
    compute_league,
    compute_standings,
    day_start,
    rank,
    render_board,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=EASTERN)  # Friday: mid-week
WEEK = week_start(NOW)
ALPHA_CHAT, BRAVO_CHAT = -111, -222
ALPHA = (101, 102, 103)
BRAVO = (201, 202, 203)
GHOST = (301, 302, 303)

_ids = count(77000)


def naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


async def add_users(session_factory, *user_ids, hide=()):
    async with session_factory() as session:
        for uid in user_ids:
            session.add(
                User(
                    telegram_user_id=uid,
                    username=f"user{uid}",
                    attestation_accepted_at=datetime.now(UTC),
                    hide_amounts=uid in hide,
                )
            )
        await session.commit()


async def add_clan(session_factory, name, members, *, home=None, joined=None, icon="⚔️"):
    """A clan whose first member owns it; everyone joined `joined` (default:
    long before any trade)."""
    joined = naive(joined or NOW - timedelta(days=60))
    async with session_factory() as session:
        clan = Clan(
            name=name,
            name_key=name.casefold(),
            icon=icon,
            owner_id=members[0],
            home_chat_id=home,
            invite_code=f"code{next(_ids)}",
        )
        session.add(clan)
        await session.flush()
        for index, uid in enumerate(members):
            session.add(
                ClanMembership(
                    clan_id=clan.id,
                    telegram_user_id=uid,
                    role="owner" if index == 0 else "member",
                    joined_at=joined,
                )
            )
        await session.commit()
        return clan.id


async def round_trip(session_factory, user_id, cost, proceeds, *, at=NOW, bought=None):
    """Buy `cost` of 100 shares, sell them all for `proceeds` at `at`."""
    bought = bought or at - timedelta(hours=1)
    async with session_factory() as session:
        for side, usdc, when in (("BUY", cost, bought), ("SELL", proceeds, at)):
            uid = next(_ids)
            session.add(
                Order(
                    telegram_user_id=user_id,
                    telegram_update_id=uid,
                    idempotency_key=f"k{uid}",
                    token_id=f"tok{user_id}",
                    market_title="Will it rain?",
                    side=side,
                    amount=Decimal(usdc),
                    status="filled",
                    fill_price=Decimal("0.5"),
                    fill_shares=Decimal("100"),
                    fill_usdc=Decimal(usdc),
                    created_at=naive(when),
                )
            )
        await session.commit()


async def seed_war(session_factory):
    """Alpha: +$50 on $100 closed (+50%). Bravo: +$100 on $400 (+25%) — more
    dollars, worse ROI. Ghost: 3 members, no trades."""
    await add_users(session_factory, *ALPHA, *BRAVO, *GHOST)
    alpha = await add_clan(session_factory, "Alpha Apes", ALPHA, home=ALPHA_CHAT)
    bravo = await add_clan(session_factory, "Bravo Bears", BRAVO, home=BRAVO_CHAT, icon="🐻")
    ghost = await add_clan(session_factory, "Ghost Town", GHOST)
    await round_trip(session_factory, ALPHA[0], "100", "150")
    await round_trip(session_factory, BRAVO[0], "300", "420")
    await round_trip(session_factory, BRAVO[1], "100", "80")
    return alpha, bravo, ghost


# -- the two boards ---------------------------------------------------------------


async def test_roi_and_usd_boards_rank_differently(session_factory):
    alpha, bravo, ghost = await seed_war(session_factory)
    standings = await compute_standings(session_factory, NOW, since=WEEK)
    by_id = {s.clan_id: s for s in standings}

    assert by_id[alpha].roi_pct == Decimal("50.0")
    assert by_id[bravo].roi_pct == Decimal("25.0")  # +$100 on $400 closed
    assert by_id[bravo].pnl == Decimal("100")

    assert [s.clan_id for s in rank(standings, "roi")] == [alpha, bravo]
    # $ board: Bravo's bigger dollars win; Ghost (no trades) ranks below
    # everyone who actually traded — losing beats not playing.
    assert [s.clan_id for s in rank(standings, "usd")] == [bravo, alpha, ghost]


async def test_clans_below_three_members_are_not_ranked(session_factory):
    await add_users(session_factory, 1, 2)
    duo = await add_clan(session_factory, "Dynamic Duo", (1, 2))
    await round_trip(session_factory, 1, "200", "400")
    standings = await compute_standings(session_factory, NOW, since=WEEK)

    assert MIN_MEMBERS == 3
    assert rank(standings, "usd") == [] and rank(standings, "roi") == []
    board = render_board(standings, "w", "roi", my_clan_id=duo)
    assert "Dynamic Duo: not ranked (needs 3 members)" in board


async def test_roi_board_needs_100_dollars_closed(session_factory):
    """A $10 flip at +500% must not top the % board."""
    await add_users(session_factory, *ALPHA)
    tiny = await add_clan(session_factory, "Tiny Flip", ALPHA)
    await round_trip(session_factory, ALPHA[0], "10", "60")
    standings = await compute_standings(session_factory, NOW, since=WEEK)

    assert rank(standings, "roi") == []
    assert [s.clan_id for s in rank(standings, "usd")] == [tiny]  # $ board still counts it


# -- windows and attribution --------------------------------------------------------


async def test_daily_window_resets_at_6pm_eastern(session_factory):
    await add_users(session_factory, *ALPHA)
    await add_clan(session_factory, "Alpha Apes", ALPHA)
    today = day_start(NOW)
    assert today == datetime(2026, 7, 9, 18, 0, tzinfo=EASTERN)  # Thursday 6pm
    await round_trip(session_factory, ALPHA[0], "100", "130", at=today + timedelta(hours=1))
    await round_trip(session_factory, ALPHA[1], "100", "90", at=today - timedelta(hours=1))

    day = await compute_standings(session_factory, NOW, since=today)
    week = await compute_standings(session_factory, NOW, since=WEEK)
    assert day[0].pnl == Decimal("30")  # only the trade after Thursday 6pm
    assert week[0].pnl == Decimal("20")


async def test_trade_counts_for_the_clan_it_closed_in(session_factory):
    """D-15 attribution: stints, not current membership."""
    hopper = 9
    await add_users(session_factory, *ALPHA, *BRAVO, hopper)
    alpha = await add_clan(session_factory, "Alpha Apes", ALPHA)
    bravo = await add_clan(session_factory, "Bravo Bears", BRAVO)
    switch = NOW - timedelta(days=2)
    async with session_factory() as session:
        session.add(
            ClanMembership(
                clan_id=alpha, telegram_user_id=hopper, role="member",
                joined_at=naive(NOW - timedelta(days=5)), left_at=naive(switch),
                left_reason="left",
            )
        )  # fmt: skip
        session.add(
            ClanMembership(
                clan_id=bravo, telegram_user_id=hopper, role="member", joined_at=naive(switch)
            )
        )
        await session.commit()
    await round_trip(session_factory, hopper, "100", "150", at=switch - timedelta(hours=3))
    await round_trip(session_factory, hopper, "100", "70", at=switch + timedelta(hours=3))

    by_id = {s.clan_id: s for s in await compute_standings(session_factory, NOW, since=WEEK)}
    assert by_id[alpha].pnl == Decimal("50")  # the win stays where it was earned
    assert by_id[bravo].pnl == Decimal("-30")  # the new clan only gets what closed with it
    assert by_id[alpha].members == 3  # leavers don't count as current members


async def test_trades_closed_before_joining_never_count(session_factory):
    await add_users(session_factory, *ALPHA)
    alpha = await add_clan(session_factory, "Alpha Apes", ALPHA, joined=NOW - timedelta(hours=2))
    await round_trip(session_factory, ALPHA[0], "100", "900", at=NOW - timedelta(hours=3))
    standings = await compute_standings(session_factory, NOW)
    assert standings[0].clan_id == alpha and standings[0].pnl == Decimal("0")


async def test_sell_this_week_realizes_against_a_buy_from_last_week(session_factory):
    """The buy predates the window; the sell must still subtract its cost."""
    await add_users(session_factory, *ALPHA)
    await add_clan(session_factory, "Alpha Apes", ALPHA)
    await round_trip(
        session_factory, ALPHA[0], "100", "120", at=NOW, bought=WEEK - timedelta(days=3)
    )
    standings = await compute_standings(session_factory, NOW, since=WEEK)
    assert standings[0].pnl == Decimal("20")  # not $120
    assert standings[0].cost == Decimal("100")


async def test_resolutions_count_with_their_cost_basis(session_factory):
    await add_users(session_factory, *ALPHA)
    await add_clan(session_factory, "Alpha Apes", ALPHA)
    async with session_factory() as session:
        session.add(
            Resolution(
                telegram_user_id=ALPHA[0], token_id="t", market_title="M?",
                shares=Decimal("200"), avg_price=Decimal("0.5"), final_price=Decimal("1"),
                pnl=Decimal("100"), won=True, resolved_at=naive(NOW - timedelta(hours=1)),
            )
        )  # fmt: skip
        await session.commit()
    standings = await compute_standings(session_factory, NOW, since=WEEK)
    assert standings[0].pnl == Decimal("100")
    assert standings[0].cost == Decimal("100")
    assert standings[0].roi_pct == Decimal("100.0")


async def test_disbanded_clans_leave_the_board(session_factory):
    alpha, bravo, _ = await seed_war(session_factory)
    async with session_factory() as session:
        (await session.get(Clan, bravo)).disbanded_at = naive(NOW)
        await session.commit()
    ids = [s.clan_id for s in await compute_standings(session_factory, NOW, since=WEEK)]
    assert bravo not in ids and alpha in ids


# -- the board text and the weekly announcement ---------------------------------------


async def test_board_shows_top_and_your_own_rank(session_factory):
    alpha, bravo, _ = await seed_war(session_factory)
    standings = await compute_standings(session_factory, NOW, since=WEEK)
    board = render_board(standings, "w", "roi", my_clan_id=bravo)

    assert board.splitlines()[0] == "🏆 Clans · This week · % ROI"
    assert "1. ⚔️ Alpha Apes  +50.0% · +$50 · 3 members" in board
    assert "2. 🐻 Bravo Bears  +25.0% · +$100 · 3 members" in board
    assert "Resets Sun 6pm ET" in board
    assert "0x" not in board  # group-safe: no addresses, ever


async def test_announcement_names_ranks_top_clan_and_mvp(session_factory):
    """PRD F-21: each clan hears its rank on both boards, the leader, its MVP."""
    alpha, bravo, ghost = await seed_war(session_factory)
    sent: list[dict] = []

    async def send(**kwargs):
        sent.append(kwargs)
        return type("Msg", (), {"message_id": len(sent)})()

    queue = BroadcastQueue(send=send)
    announced = await announce_league(session_factory, queue, NOW)
    await queue.drain_once()

    assert announced == 3
    by_chat = {m["chat_id"]: m["text"] for m in sent}

    alpha_text = by_chat[ALPHA_CHAT]  # home chat
    assert "ROI board: #1 of 2 (+50.0%)" in alpha_text
    assert "$ board: #2 of 3 (+$50)" in alpha_text
    assert f"user{ALPHA[0]}" in alpha_text and "Clan MVP" in alpha_text

    bravo_text = by_chat[BRAVO_CHAT]
    assert "ROI board: #2 of 2" in bravo_text
    assert "Top of the pile: ⚔️ Alpha Apes" in bravo_text
    assert f"user{BRAVO[0]}" in bravo_text  # +$120 beats a teammate's -$20

    ghost_text = by_chat[GHOST[0]]  # no home chat -> the owner's DM
    assert "No trades this week" in ghost_text
    assert "needs $100 of closed trades" in ghost_text


async def test_announcement_falls_back_to_owner_dm_when_home_chat_is_dead(session_factory):
    alpha, _, _ = await seed_war(session_factory)
    async with session_factory() as session:
        session.add(GroupChat(chat_id=ALPHA_CHAT, title="Alpha", created_by=ALPHA[0], active=False))
        await session.commit()
    queue = BroadcastQueue(send=lambda **_: None)
    await announce_league(session_factory, queue, NOW)
    assert queue.pending_count(ALPHA_CHAT) == 0
    assert queue.pending_count(ALPHA[0]) == 1


async def test_private_mvp_gets_no_dollar_figure(session_factory):
    await add_users(session_factory, *ALPHA, hide=(ALPHA[0],))
    await add_clan(session_factory, "Alpha Apes", ALPHA, home=ALPHA_CHAT)
    await round_trip(session_factory, ALPHA[0], "100", "175")
    queue = BroadcastQueue(send=lambda **_: None)
    await announce_league(session_factory, queue, NOW)
    text = queue._queues[ALPHA_CHAT][0].text
    assert f"user{ALPHA[0]}" in text
    assert "75" not in text.split("Clan MVP")[1]  # size classified


async def test_league_orders_ranked_clans_before_unranked(session_factory):
    alpha, bravo, ghost = await seed_war(session_factory)
    standings = await compute_league(session_factory, NOW)
    assert [s.clan_id for s in standings] == [alpha, bravo, ghost]


async def test_sells_with_no_recorded_buy_do_not_score(session_factory):
    """Shares acquired outside the bot have no cost basis here; their whole
    sale proceeds must not be scored as clan profit (or inflate ROI)."""
    await add_users(session_factory, *ALPHA)
    await add_clan(session_factory, "Alpha Apes", ALPHA)
    await round_trip(session_factory, ALPHA[0], "100", "130")
    async with session_factory() as session:
        uid = next(_ids)
        session.add(
            Order(
                telegram_user_id=ALPHA[1], telegram_update_id=uid, idempotency_key=f"k{uid}",
                token_id="from-elsewhere", market_title="M?", side="SELL", amount=Decimal("50"),
                status="filled", fill_price=Decimal("0.9"), fill_shares=Decimal("50"),
                fill_usdc=Decimal("45"), created_at=naive(NOW - timedelta(minutes=5)),
            )
        )  # fmt: skip
        await session.commit()
    standings = await compute_standings(session_factory, NOW, since=WEEK)
    assert standings[0].pnl == Decimal("30") and standings[0].cost == Decimal("100")
    assert standings[0].closed_trades == 1


async def test_mvp_line_formats_money_plainly(session_factory):
    await add_users(session_factory, *ALPHA)
    await add_clan(session_factory, "Alpha Apes", ALPHA, home=ALPHA_CHAT)
    await round_trip(session_factory, ALPHA[0], "1000", "2234.5")
    queue = BroadcastQueue(send=lambda **_: None)
    await announce_league(session_factory, queue, NOW)
    assert f"Clan MVP: user{ALPHA[0]} (+$1,234.50 realized)" in queue._queues[ALPHA_CHAT][0].text
