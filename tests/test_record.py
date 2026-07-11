"""/record (PRD F-22): the five stats, computed from the same realization
logic the leaderboard uses, plus group-safety of the rendered output."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers.record import record
from fade.db.models import FadeEvent, Order, Resolution, User
from fade.social.record import compute_record

MARCUS, PRIYA, DEV = 995, 996, 997
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

_update_ids = count(99500)


async def seed_user(session_factory, user_id: int, username: str) -> None:
    async with session_factory() as session:
        session.add(
            User(
                telegram_user_id=user_id,
                username=username,
                attestation_accepted_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def seed_sell_pair(session_factory, user_id: int, sell_usdc: str, at: datetime) -> None:
    """Buy $10 of 20 shares then sell all for `sell_usdc`: realizes (sell-10)."""
    # Buys and sells must share a token to realize; give each pair its own token.
    uid_base = next(_update_ids)
    token = f"pair{uid_base}"
    async with session_factory() as session:
        for side, usdc, offset in (("BUY", "10", 0), ("SELL", sell_usdc, 1)):
            uid = next(_update_ids)
            session.add(
                Order(
                    telegram_user_id=user_id,
                    telegram_update_id=uid,
                    idempotency_key=f"k{uid}",
                    token_id=token,
                    market_title="Will it rain?",
                    side=side,
                    amount=Decimal(usdc),
                    status="filled",
                    fill_price=Decimal("0.5"),
                    fill_shares=Decimal("20"),
                    fill_usdc=Decimal(usdc),
                    created_at=(at + timedelta(minutes=offset)).replace(tzinfo=None),
                )
            )
        await session.commit()


async def test_record_computes_all_five_stats(settings, session_factory):
    """Win rate, biggest win, biggest loss, streak, most-faded — from seeded
    Orders, a Resolution, and FadeEvents, on the leaderboard's own math."""
    await seed_user(session_factory, MARCUS, "marcus")
    await seed_user(session_factory, PRIYA, "priya")
    await seed_user(session_factory, DEV, "dev")

    # Chronological outcomes for Marcus:
    await seed_sell_pair(session_factory, MARCUS, "22", T0 - timedelta(days=5))  # +12 win
    await seed_sell_pair(session_factory, MARCUS, "4", T0 - timedelta(days=4))  # -6 loss
    async with session_factory() as session:
        session.add(  # held-to-resolution loss, -8, three days ago
            Resolution(
                telegram_user_id=MARCUS,
                token_id="res1",
                market_title="Will the Fed cut?",
                shares=Decimal("16"),
                avg_price=Decimal("0.5"),
                final_price=Decimal("0"),
                pnl=Decimal("-8"),
                won=False,
                resolved_at=(T0 - timedelta(days=3)).replace(tzinfo=None),
            )
        )
        # Marcus faded priya twice, dev once; copied dev once (copies don't count).
        for target, mode in ((PRIYA, "fd"), (PRIYA, "fd"), (DEV, "fd"), (DEV, "cp")):
            session.add(FadeEvent(actor_id=MARCUS, target_id=target, mode=mode, chat_id=-1))
        await session.commit()

    rec = await compute_record(session_factory, MARCUS)

    assert rec.closed_trades == 3
    assert rec.wins == 1
    assert rec.win_rate == 1 / 3  # (1) win rate
    assert rec.biggest_win == Decimal("12")  # (2) biggest single win
    assert rec.biggest_loss == Decimal("-8")  # (3) biggest single loss
    assert rec.streak == -2  # (4) two consecutive losses, most recent first
    assert rec.most_faded_username == "priya"  # (5) faded most often (2x; copy ignored)
    assert rec.most_faded_count == 2


async def test_record_streak_counts_consecutive_wins(settings, session_factory):
    await seed_user(session_factory, MARCUS, "marcus")
    await seed_sell_pair(session_factory, MARCUS, "4", T0 - timedelta(days=3))  # loss
    await seed_sell_pair(session_factory, MARCUS, "22", T0 - timedelta(days=2))  # win
    await seed_sell_pair(session_factory, MARCUS, "15", T0 - timedelta(days=1))  # win

    rec = await compute_record(session_factory, MARCUS)
    assert rec.streak == 2  # the loss before the wins breaks the chain


async def test_record_command_renders_without_wallet_or_balance(settings, session_factory):
    """Group-safety: /record output never contains an address or balance."""
    await seed_user(session_factory, MARCUS, "marcus")
    await seed_user(session_factory, PRIYA, "priya")
    await seed_sell_pair(session_factory, MARCUS, "22", T0 - timedelta(days=1))
    async with session_factory() as session:
        session.add(FadeEvent(actor_id=MARCUS, target_id=PRIYA, mode="fd", chat_id=-1))
        await session.commit()

    update = MagicMock(spec=Update)
    update.effective_chat.type = "supergroup"  # invoked in a group on purpose
    update.effective_user.id = PRIYA
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["@marcus"]
    context.application.bot_data = {"sessions": session_factory}

    await record(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "marcus" in text
    assert "Win rate" in text and "streak" in text.lower()
    assert "priya" in text  # most-faded callout
    lowered = text.lower()
    assert "0x" not in lowered and "balance" not in lowered and "wallet" not in lowered


async def test_record_unknown_user(settings, session_factory):
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = MARCUS
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["@nobody"]
    context.application.bot_data = {"sessions": session_factory}

    await record(update, context)

    from fade.bot.handlers.record import NOT_FOUND_TEXT

    assert update.message.reply_text.call_args.args[0] == NOT_FOUND_TEXT
