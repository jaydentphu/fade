"""/setup, /join, /privacy, /leaderboard handlers (PRD F-16, F-17, F-20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import clan
from fade.db.models import GroupChat, GroupMember, Order, User

CHAT_ID = -100777
USER_ID = 901


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
    }
    return context


def make_update(chat_type: str = "supergroup", user_id: int = USER_ID) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = chat_type
    update.effective_chat.id = CHAT_ID
    update.effective_chat.title = "Degens Anonymous"
    update.effective_user.id = user_id
    update.effective_user.username = f"user{user_id}"
    update.effective_user.first_name = "Tester"
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


async def test_setup_registers_group_as_clan(settings, session_factory, onboard):
    """PRD F-16: /setup in a group registers it as a clan."""
    await onboard(USER_ID)
    update = make_update()
    context = make_context(settings, session_factory)

    await clan.setup(update, context)

    async with session_factory() as session:
        row = await session.get(GroupChat, CHAT_ID)
        member = await session.get(GroupMember, (CHAT_ID, USER_ID))
    assert row is not None
    assert row.title == "Degens Anonymous"
    assert row.created_by == USER_ID
    assert member is not None  # the registrar is enrolled automatically
    assert "Clan registered" in update.message.reply_text.call_args.args[0]


async def test_setup_rejected_in_dm(settings, session_factory, onboard):
    await onboard(USER_ID)
    update = make_update(chat_type="private")
    context = make_context(settings, session_factory)

    await clan.setup(update, context)

    assert update.message.reply_text.call_args.args[0] == clan.GROUP_ONLY_TEXT
    async with session_factory() as session:
        assert await session.get(GroupChat, CHAT_ID) is None


async def test_setup_twice_does_not_duplicate(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await clan.setup(make_update(), context)

    again = make_update()
    await clan.setup(again, context)

    assert again.message.reply_text.call_args.args[0] == clan.ALREADY_SETUP_TEXT


async def test_join_enrolls_member_after_setup(settings, session_factory, onboard):
    await onboard(USER_ID)
    await onboard(902)
    context = make_context(settings, session_factory)
    await clan.setup(make_update(), context)

    joiner = make_update(user_id=902)
    await clan.join(joiner, context)

    async with session_factory() as session:
        assert await session.get(GroupMember, (CHAT_ID, 902)) is not None
    assert "joined the clan" in joiner.message.reply_text.call_args.args[0]


async def test_join_requires_account_and_setup(settings, session_factory, onboard):
    context = make_context(settings, session_factory)
    stranger = make_update(user_id=903)
    await clan.join(stranger, context)
    assert stranger.message.reply_text.call_args.args[0] == clan.JOIN_NEED_ACCOUNT_TEXT

    await onboard(903)
    no_clan = make_update(user_id=903)
    await clan.join(no_clan, context)
    assert no_clan.message.reply_text.call_args.args[0] == clan.JOIN_NEED_SETUP_TEXT


async def test_privacy_toggles_hide_amounts(settings, session_factory, onboard):
    """PRD F-17: per-user toggle for hiding dollar amounts in broadcasts."""
    await onboard(USER_ID)
    context = make_context(settings, session_factory)

    update = make_update(chat_type="private")
    await clan.privacy(update, context)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
    assert user.hide_amounts is True
    assert update.message.reply_text.call_args.args[0] == clan.PRIVACY_ON_TEXT

    update2 = make_update(chat_type="private")
    await clan.privacy(update2, context)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
    assert user.hide_amounts is False


_update_ids = count(95000)


async def _seed_round_trip(session_factory, user_id: int, sell_usdc: str) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        for side, usdc, shares, at in (
            ("BUY", "10", "20", now - timedelta(hours=2)),
            ("SELL", sell_usdc, "20", now - timedelta(hours=1)),
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
                    fill_shares=Decimal(shares),
                    fill_usdc=Decimal(usdc),
                    created_at=at,
                )
            )
        await session.commit()


async def test_leaderboard_shows_weekly_and_all_time_with_columns(
    settings, session_factory, onboard
):
    """PRD F-20: weekly + all-time rankings with PnL, volume, win-rate columns."""
    await onboard(USER_ID)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
        user.username = f"user{USER_ID}"
        await session.commit()
    context = make_context(settings, session_factory)
    await clan.setup(make_update(), context)
    await _seed_round_trip(session_factory, USER_ID, sell_usdc="15")

    update = make_update()
    await clan.leaderboard(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "This week" in text and "All time" in text
    assert "PnL +$5.00" in text
    assert "Vol $25.00" in text
    assert "Win 100%" in text
    assert f"user{USER_ID}" in text


async def test_leaderboard_empty_clan_message(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    await clan.setup(make_update(), context)

    update = make_update()
    await clan.leaderboard(update, context)

    assert update.message.reply_text.call_args.args[0] == clan.NO_LEADERBOARD_TEXT


async def test_leaderboard_group_only(settings, session_factory, onboard):
    context = make_context(settings, session_factory)
    update = make_update(chat_type="private")

    await clan.leaderboard(update, context)

    assert update.message.reply_text.call_args.args[0] == clan.GROUP_ONLY_TEXT
