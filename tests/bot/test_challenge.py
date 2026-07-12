"""Head-to-head challenges end to end (PRD F-24).

Simulates: Priya challenges Marcus on a market -> she picks side+size ->
pending record -> Marcus accepts (both legs execute via TradingService) or
declines -> resolution settles the wager in the group exactly once.

Trading and market lookup are mocked; no live network anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import challenge as ch
from fade.db.models import Challenge, Resolution
from fade.trading.markets import MarketCard
from fade.trading.service import OrderResult

CHAT_ID = -100777
PRIYA, MARCUS, LURKER = 901, 902, 999
URL = "https://polymarket.com/event/fed-cuts"

MARKET = MarketCard(
    market_id="1",
    question="Will the Fed cut rates in September?",
    slug="fed-cuts",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.61"),
    no_price=Decimal("0.39"),
    end_date="2026-09-30",
    open=True,
)

FILLED = OrderResult(
    status="filled",
    side="BUY",
    fill_price=Decimal("0.61"),
    fill_shares=Decimal("40.9"),
    fill_usdc=Decimal("25"),
    fee=Decimal("0.25"),
    new_balance=Decimal("75"),
)


def make_context(settings, session_factory):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    trading = MagicMock()
    trading.execute_market_order = AsyncMock(return_value=FILLED)
    trading.get_market_card = AsyncMock(return_value=MARKET)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
    }
    return context


def group_message(text_args: list[str], user_id: int, username: str) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = 9000
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = username.title()
    update.message.reply_text = AsyncMock()
    return update


def group_callback(data: str, user_id: int, username: str | None, update_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = (username or "someone").title()
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


async def propose(context, side_letter: str = "y", size: str = "25", pick_update_id: int = 9100):
    """Run /challenge + the challenger's side/size pick. Returns challenge id."""
    cmd = group_message(["@marcus", URL], PRIYA, "priya")
    context.args = ["@marcus", URL]
    await ch.challenge(cmd, context)
    markup = cmd.message.reply_text.call_args.kwargs["reply_markup"]
    key = markup.inline_keyboard[0][0].callback_data.split(":")[1]

    pick = group_callback(f"hp:{key}:{PRIYA}:{side_letter}:{size}", PRIYA, "priya", pick_update_id)
    await ch.pick_callback(pick, context)
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        row = await session.scalar(select(Challenge))
    return row.id, pick


async def test_propose_creates_pending_challenge_record(settings, session_factory, onboard):
    """F-24: proposing records both users, the market, and the side/size picked."""
    await onboard(PRIYA)
    context = make_context(settings, session_factory)

    await propose(context, side_letter="y", size="25")

    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row is not None
    assert row.challenger_id == PRIYA
    assert row.challenger_username == "priya"
    assert row.opponent_username == "marcus"
    assert row.market_title == MARKET.question
    assert row.challenger_side == "YES"
    assert row.size_usdc == Decimal("25")
    assert row.status == "pending"
    assert row.chat_id == CHAT_ID
    # Proposing moves no money.
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()


async def test_third_user_cannot_accept_or_decline(settings, session_factory, onboard):
    """Owner lock: only the named opponent can answer (same pattern as pickers)."""
    await onboard(PRIYA)
    await onboard(LURKER)
    context = make_context(settings, session_factory)
    challenge_id, _ = await propose(context)

    for data, handler in ((f"ha:{challenge_id}", ch.accept_callback),
                          (f"hd:{challenge_id}", ch.decline_callback)):
        intruder = group_callback(data, LURKER, "lurker", update_id=9200)
        await handler(intruder, context)
        intruder.callback_query.answer.assert_awaited_once_with(
            ch.NOT_YOUR_CHALLENGE_ALERT, show_alert=True
        )

    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row.status == "pending"  # untouched
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()


async def test_accept_places_two_orders_one_per_side(settings, session_factory, onboard):
    """F-24: acceptance executes BOTH legs via TradingService, opposite sides."""
    await onboard(PRIYA)
    await onboard(MARCUS)
    context = make_context(settings, session_factory)
    challenge_id, pick = await propose(context, side_letter="y", size="25", pick_update_id=9100)

    accept = group_callback(f"ha:{challenge_id}", MARCUS, "marcus", update_id=9300)
    await ch.accept_callback(accept, context)

    calls = context.application.bot_data["trading"].execute_market_order.call_args_list
    assert len(calls) == 2
    first, second = calls[0].kwargs, calls[1].kwargs
    # Challenger's leg: her side (YES), her id, the PROPOSAL update_id.
    assert first["telegram_user_id"] == PRIYA
    assert first["token_id"] == MARKET.yes_token
    assert first["telegram_update_id"] == 9100
    # Opponent's leg: the OPPOSITE side, his id, the ACCEPT update_id.
    assert second["telegram_user_id"] == MARCUS
    assert second["token_id"] == MARKET.no_token
    assert second["telegram_update_id"] == 9300
    for leg in (first, second):
        assert leg["side"] == "BUY"
        assert leg["amount"] == Decimal("25")

    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row.status == "accepted"
    assert row.opponent_id == MARCUS


async def test_double_accept_does_not_execute_twice(settings, session_factory, onboard):
    """The pending->executing transition is the double-tap barrier."""
    await onboard(PRIYA)
    await onboard(MARCUS)
    context = make_context(settings, session_factory)
    challenge_id, _ = await propose(context)

    first = group_callback(f"ha:{challenge_id}", MARCUS, "marcus", update_id=9300)
    await ch.accept_callback(first, context)
    again = group_callback(f"ha:{challenge_id}", MARCUS, "marcus", update_id=9301)
    await ch.accept_callback(again, context)

    calls = context.application.bot_data["trading"].execute_market_order.call_args_list
    assert len(calls) == 2  # the two legs of the FIRST accept only
    again.callback_query.answer.assert_awaited_once_with(ch.ALREADY_HANDLED_ALERT, show_alert=True)


async def test_decline_cancels_cleanly_with_no_funds_moved(settings, session_factory, onboard):
    """F-24: declining cancels; no orders are placed."""
    await onboard(PRIYA)
    await onboard(MARCUS)
    context = make_context(settings, session_factory)
    challenge_id, _ = await propose(context)

    decline = group_callback(f"hd:{challenge_id}", MARCUS, "marcus", update_id=9400)
    await ch.decline_callback(decline, context)

    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row.status == "declined"
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()
    text = decline.callback_query.edit_message_text.call_args.args[0]
    assert "No money moved" in text


async def test_unanswered_challenge_expires_cleanly(settings, session_factory, onboard):
    """F-24: past the TTL the challenge cancels itself; no funds move."""
    await onboard(PRIYA)
    await onboard(MARCUS)
    context = make_context(settings, session_factory)
    challenge_id, _ = await propose(context)
    stale = datetime.now(UTC).replace(tzinfo=None) - ch.CHALLENGE_TTL - timedelta(hours=1)
    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
        row.created_at = stale
        await session.commit()

    bot = MagicMock()
    bot.send_message = AsyncMock()
    await ch.settle_and_expire(session_factory, bot)

    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
    assert row.status == "expired"
    bot.send_message.assert_awaited_once()
    assert "No money moved" in bot.send_message.call_args.kwargs["text"]

    # A late accept tap after expiry also can't move funds.
    late = group_callback(f"ha:{challenge_id}", MARCUS, "marcus", update_id=9500)
    await ch.accept_callback(late, context)
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()


async def seed_accepted_challenge(session_factory, side: str) -> int:
    async with session_factory() as session:
        row = Challenge(
            chat_id=CHAT_ID,
            challenger_id=PRIYA,
            challenger_username="priya",
            opponent_username="marcus",
            opponent_id=MARCUS,
            market_title=MARKET.question,
            yes_token=MARKET.yes_token,
            no_token=MARKET.no_token,
            challenger_side=side,
            size_usdc=Decimal("25"),
            challenger_update_id=9100,
            status="accepted",
        )
        session.add(row)
        await session.commit()
        return row.id


async def seed_resolution(session_factory, token_id: str, final: str) -> None:
    async with session_factory() as session:
        session.add(
            Resolution(
                telegram_user_id=PRIYA,
                token_id=token_id,
                market_title=MARKET.question,
                shares=Decimal("40"),
                avg_price=Decimal("0.61"),
                final_price=Decimal(final),
                pnl=Decimal("1"),
                won=final == "1",
            )
        )
        await session.commit()


async def test_settlement_declares_challenger_when_their_side_resolves(
    settings, session_factory, onboard
):
    """F-24: YES resolves, challenger took YES -> challenger declared winner."""
    await onboard(PRIYA)
    challenge_id = await seed_accepted_challenge(session_factory, side="YES")
    await seed_resolution(session_factory, MARKET.yes_token, final="1")
    bot = MagicMock()
    bot.send_message = AsyncMock()

    posted = await ch.settle_and_expire(session_factory, bot)

    assert posted == 1
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == CHAT_ID
    text = kwargs["text"]
    assert "Resolved YES" in text
    assert text.index("priya") < text.index("@marcus")  # winner named first
    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
    assert row.status == "settled"
    assert row.winner_id == PRIYA


async def test_settlement_declares_opponent_when_market_goes_the_other_way(
    settings, session_factory, onboard
):
    """F-24: the challenger's own token settling at 0 crowns the opponent."""
    await onboard(PRIYA)
    challenge_id = await seed_accepted_challenge(session_factory, side="YES")
    # The scanner recorded the challenger's YES position settling worthless.
    await seed_resolution(session_factory, MARKET.yes_token, final="0")
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await ch.settle_and_expire(session_factory, bot)

    text = bot.send_message.call_args.kwargs["text"]
    assert "Resolved NO" in text
    assert text.index("@marcus") < text.index("priya")  # opponent named first
    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
    assert row.winner_id == MARCUS


async def test_settlement_posts_exactly_once(settings, session_factory, onboard):
    """F-24: a second scan pass (or a restart) never re-announces."""
    await onboard(PRIYA)
    await seed_accepted_challenge(session_factory, side="YES")
    await seed_resolution(session_factory, MARKET.yes_token, final="1")
    bot = MagicMock()
    bot.send_message = AsyncMock()

    first = await ch.settle_and_expire(session_factory, bot)
    second = await ch.settle_and_expire(session_factory, bot)
    third = await ch.settle_and_expire(session_factory, bot)  # simulated restart pass

    assert (first, second, third) == (1, 0, 0)
    bot.send_message.assert_awaited_once()


async def test_challenge_requires_group_and_valid_args(settings, session_factory, onboard):
    await onboard(PRIYA)
    context = make_context(settings, session_factory)

    dm = group_message(["@marcus", URL], PRIYA, "priya")
    dm.effective_chat.type = "private"
    context.args = ["@marcus", URL]
    await ch.challenge(dm, context)
    assert dm.message.reply_text.call_args.args[0] == ch.GROUP_ONLY_TEXT

    missing = group_message(["@marcus"], PRIYA, "priya")
    context.args = ["@marcus"]
    await ch.challenge(missing, context)
    assert missing.message.reply_text.call_args.args[0] == ch.USAGE_TEXT

    context.args = ["@priya", URL]
    selfie = group_message(["@priya", URL], PRIYA, "priya")
    await ch.challenge(selfie, context)
    assert selfie.message.reply_text.call_args.args[0] == ch.SELF_CHALLENGE_TEXT
