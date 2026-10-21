"""Resolution lifecycle (PRD F-14): crown/roast DMs, idempotent polling, and
resolution-realized PnL on the leaderboard (D-6's second half).

The resolution data source is injected (like the deposit scanner's Rpc):
no live network anywhere.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from fade.bot.handlers.resolutions import FLEX_LOSS_CAPTION, ResolutionScanner, ResolvedPosition
from fade.db.models import Clan, ClanMember, Resolution, User, Wallet
from fade.social.leaderboard import compute_leaderboard

USER_ID = 980
CHAT_ID = -100980
WALLET_ADDR = "0xFadE0000000000000000000000000000000000aa"

WIN = ResolvedPosition(
    token_id="111",
    title="Will the Fed cut rates in September?",
    shares=Decimal("40"),
    avg_price=Decimal("0.61"),
    final_price=Decimal("1"),  # +0.39 * 40 = +15.60
)
LOSS = ResolvedPosition(
    token_id="222",
    title="Will it rain tomorrow?",
    shares=Decimal("20"),
    avg_price=Decimal("0.55"),
    final_price=Decimal("0"),  # -0.55 * 20 = -11.00
)


def make_fetch(positions):
    async def fetch(address: str):
        assert address == WALLET_ADDR
        return positions

    return fetch


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


async def seed_wallet(session_factory, onboard) -> None:
    await onboard(USER_ID)
    async with session_factory() as session:
        session.add(Wallet(telegram_user_id=USER_ID, deposit_wallet_address=WALLET_ADDR))
        await session.commit()


async def test_winning_resolution_sends_exactly_one_crown_dm(settings, session_factory, onboard):
    await seed_wallet(session_factory, onboard)
    scanner = ResolutionScanner(session_factory, fetch=make_fetch([WIN]))
    bot = make_bot()

    sent = await scanner.scan_once(bot)

    assert sent == 1
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == USER_ID
    text = kwargs["text"]
    assert "👑" in text and "Called it" in text  # crown voice, not plain
    assert "15.60" in text  # settlement PnL
    assert "Fed cut rates" in text
    # PRD F-23: a winning resolution also gets a shareable flex card, DM'd once.
    bot.send_photo.assert_awaited_once()
    photo_kwargs = bot.send_photo.call_args.kwargs
    assert photo_kwargs["chat_id"] == USER_ID
    assert len(photo_kwargs["photo"]) > 0


async def test_losing_resolution_sends_exactly_one_roast_dm(settings, session_factory, onboard):
    await seed_wallet(session_factory, onboard)
    scanner = ResolutionScanner(session_factory, fetch=make_fetch([LOSS]))
    bot = make_bot()

    sent = await scanner.scan_once(bot)

    assert sent == 1
    text = bot.send_message.call_args.kwargs["text"]
    assert "💀" in text and "against you" in text  # roast voice
    assert "11.00" in text  # settlement PnL (magnitude)
    assert "rain" in text
    # Spec change (flex card redesign): losses now get a red "GOT FADED" card
    # too, memecoin-bot style. This assertion previously pinned the old
    # wins-only rule (assert_not_awaited); it's updated, not weakened —
    # still exactly-once, and now also checks it's the loss variant.
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.call_args.kwargs["caption"].startswith(FLEX_LOSS_CAPTION)


async def test_polling_same_resolution_twice_never_double_notifies(
    settings, session_factory, onboard
):
    """Second poll — or a poll after a restart — hits the unique-row barrier."""
    await seed_wallet(session_factory, onboard)
    bot = make_bot()

    scanner = ResolutionScanner(session_factory, fetch=make_fetch([WIN]))
    await scanner.scan_once(bot)
    await scanner.scan_once(bot)  # same data again
    # Simulated restart: a brand-new scanner instance, same database.
    rebooted = ResolutionScanner(session_factory, fetch=make_fetch([WIN]))
    await rebooted.scan_once(bot)

    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_awaited_once()  # flex card DM is idempotent too
    async with session_factory() as session:
        rows = (await session.scalars(select(Resolution))).all()
    assert len(rows) == 1 and rows[0].won is True


async def test_held_to_resolution_position_counts_on_the_leaderboard(
    settings, session_factory, onboard
):
    """D-6: resolution-realized PnL ranks identically in shape to sell-based."""
    await seed_wallet(session_factory, onboard)
    async with session_factory() as session:
        session.add(Clan(chat_id=CHAT_ID, title="Degens", created_by=USER_ID))
        session.add(ClanMember(chat_id=CHAT_ID, telegram_user_id=USER_ID))
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
        user.username = f"user{USER_ID}"
        await session.commit()

    # The user NEVER sells: the scanner records the settlement.
    scanner = ResolutionScanner(session_factory, fetch=make_fetch([WIN]))
    await scanner.scan_once(make_bot())

    rows = await compute_leaderboard(session_factory, CHAT_ID)

    assert len(rows) == 1
    row = rows[0]
    assert row.realized_pnl == Decimal("15.60")  # (1 - 0.61) * 40
    assert row.volume == Decimal("40")  # settlement proceeds: 40 shares at $1
    assert row.wins == 1 and row.closed_trades == 1
    assert row.win_rate == 1.0


async def test_losing_resolution_counts_as_a_loss_on_the_leaderboard(
    settings, session_factory, onboard
):
    await seed_wallet(session_factory, onboard)
    async with session_factory() as session:
        session.add(Clan(chat_id=CHAT_ID, title="Degens", created_by=USER_ID))
        session.add(ClanMember(chat_id=CHAT_ID, telegram_user_id=USER_ID))
        await session.commit()

    scanner = ResolutionScanner(session_factory, fetch=make_fetch([LOSS]))
    await scanner.scan_once(make_bot())

    rows = await compute_leaderboard(session_factory, CHAT_ID)

    assert rows[0].realized_pnl == Decimal("-11.00")
    assert rows[0].volume == Decimal("0")  # settled worthless: no proceeds
    assert rows[0].wins == 0 and rows[0].closed_trades == 1
    assert rows[0].win_rate == 0.0


async def test_fetch_failure_for_one_wallet_does_not_break_the_scan(
    settings, session_factory, onboard
):
    await seed_wallet(session_factory, onboard)

    async def exploding_fetch(address: str):
        raise RuntimeError("data api down")

    scanner = ResolutionScanner(session_factory, fetch=exploding_fetch)
    bot = make_bot()
    sent = await scanner.scan_once(bot)  # must not raise

    assert sent == 0
    bot.send_message.assert_not_awaited()
