"""F-26 daily digest: correct numbers, once-per-day send, admin-only /stats.

All data seeded directly; injectable clocks; no live network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import admin
from fade.bot.handlers.digest import DigestSender, compute_digest, render_digest
from fade.db.models import Deposit, Order, TradingPauseFlag, User, Withdrawal

ADMIN_ID = 1  # matches the settings fixture
RANDO_ID = 999

NOW = datetime(2026, 7, 12, 15, 0, tzinfo=UTC)
RECENT = NOW.replace(tzinfo=None) - timedelta(hours=1)
OLD = NOW.replace(tzinfo=None) - timedelta(hours=30)


def order(uid: int, status: str, when, usdc="10", fee="0.10", update_id=None) -> Order:
    order.counter = getattr(order, "counter", 0) + 1
    return Order(
        telegram_user_id=uid,
        telegram_update_id=update_id or (50_000 + order.counter),
        idempotency_key=f"idem-{order.counter}",
        token_id="111",
        market_title="Will it resolve?",
        side="BUY",
        amount=Decimal(usdc),
        status=status,
        fill_usdc=Decimal(usdc) if status == "filled" else None,
        fee=Decimal(fee) if status == "filled" else None,
        created_at=when,
    )


async def seed_a_day(session_factory) -> None:
    async with session_factory() as session:
        # Users: two new inside 24h, one old.
        session.add(User(telegram_user_id=101, created_at=RECENT))
        session.add(User(telegram_user_id=102, created_at=RECENT))
        session.add(User(telegram_user_id=103, created_at=OLD))
        # Orders inside 24h: user 101 fills twice, user 103 gets rejected once,
        # plus one failed. User 102 places nothing.
        session.add(order(101, "filled", RECENT, usdc="25", fee="0.25"))
        session.add(order(101, "filled", RECENT, usdc="15", fee="0.15"))
        session.add(order(103, "rejected", RECENT))
        session.add(order(103, "failed", RECENT))
        # An old filled order that must NOT count.
        session.add(order(103, "filled", OLD, usdc="500", fee="5.00"))
        # Deposits: one recent, one old. Withdrawals: one recent.
        session.add(
            Deposit(
                telegram_user_id=101,
                tx_hash="0x" + "aa" * 32,
                token="pUSD",
                amount=Decimal("50"),
                block_number=1,
                status="confirmed",
                detected_at=RECENT,
            )
        )
        session.add(
            Deposit(
                telegram_user_id=103,
                tx_hash="0x" + "bb" * 32,
                token="pUSD",
                amount=Decimal("50"),
                block_number=1,
                status="confirmed",
                detected_at=OLD,
            )
        )
        session.add(
            Withdrawal(
                telegram_user_id=101,
                address="0x" + "cd" * 20,
                amount=Decimal("20"),
                tx_hash="0x" + "cc" * 32,
                created_at=RECENT,
            )
        )
        session.add(TradingPauseFlag(id=1, paused=True))
        await session.commit()


async def test_digest_numbers_computed_correctly_from_a_seeded_day(session_factory):
    await seed_a_day(session_factory)

    digest = await compute_digest(session_factory, NOW)

    assert digest.new_users == 2  # 103 is older than 24h
    assert digest.active_users == 2  # 101 and 103 placed orders; 102 didn't
    assert digest.filled_orders == 2  # the old fill doesn't count
    assert digest.filled_volume == Decimal("40")  # 25 + 15
    assert digest.builder_fees == Decimal("0.40")  # 0.25 + 0.15
    assert digest.failed_orders == 2  # one rejected + one failed
    assert digest.deposits == 1
    assert digest.withdrawals == 1
    assert digest.paused is True

    text = render_digest(digest, NOW)
    assert "New users (24h): 2" in text
    assert "$40.00 volume" in text
    assert "Builder fees earned (24h): $0.40" in text
    assert "Trading paused: yes" in text
    # Aggregates only — never an address or a user balance.
    assert "0x" not in text
    assert "balance" not in text.lower()


async def test_digest_sends_exactly_once_per_day_boundary(session_factory):
    clock = {"now": NOW}
    sender = DigestSender(session_factory, ADMIN_ID, now=lambda: clock["now"])
    bot = MagicMock()
    bot.send_message = AsyncMock()

    assert await sender.maybe_send(bot) is True
    clock["now"] += timedelta(hours=3)  # later the same day: a duplicate firing
    assert await sender.maybe_send(bot) is False
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == ADMIN_ID

    clock["now"] += timedelta(days=1)  # next day: sends again
    assert await sender.maybe_send(bot) is True
    assert bot.send_message.await_count == 2


async def test_digest_without_admin_configured_is_off(session_factory):
    sender = DigestSender(session_factory, admin_user_id=0)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    assert await sender.maybe_send(bot) is False
    bot.send_message.assert_not_awaited()


# -- /stats ----------------------------------------------------------------------


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.application.bot_data = {"settings": settings, "sessions": session_factory}
    return context


def make_update(user_id: int, chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


async def test_stats_refuses_non_admin(settings, session_factory):
    update = make_update(RANDO_ID)
    await admin.stats(update, make_context(settings, session_factory))
    assert update.message.reply_text.call_args.args[0] == admin.NOT_ADMIN_TEXT


async def test_stats_gives_admin_the_same_digest(settings, session_factory):
    await seed_a_day(session_factory)
    update = make_update(ADMIN_ID)

    await admin.stats(update, make_context(settings, session_factory))

    text = update.message.reply_text.call_args.args[0]
    assert "Fade daily digest" in text
    assert "New users (24h): 2" in text  # same computation as the job
    assert "0x" not in text and "balance" not in text.lower()


async def test_stats_in_group_reveals_nothing(settings, session_factory):
    await seed_a_day(session_factory)
    update = make_update(ADMIN_ID, chat_type="supergroup")

    await admin.stats(update, make_context(settings, session_factory))

    assert update.message.reply_text.call_args.args[0] == admin.STATS_DM_ONLY_TEXT
