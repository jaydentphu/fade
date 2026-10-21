"""Stage 11: background-job resilience and data hygiene.

One user's failure (blocked bot, dead chat, Telegram error) must never take
down a background loop for everyone else, and every caught failure is logged
AND reported through the error-reporter interface. No live network anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram.error import Forbidden

from fade.bot.cards import CardStore
from fade.bot.handlers import challenge as ch
from fade.bot.handlers.deposits import DepositScanner
from fade.bot.handlers.resolutions import ResolutionScanner, ResolvedPosition
from fade.db.models import (
    ChainScanState,
    Challenge,
    Clan,
    Deposit,
    GroupChat,
    GroupMember,
    Resolution,
    UiCard,
    User,
    Wallet,
)
from fade.social.broadcast import MAX_SEND_ATTEMPTS, BroadcastQueue, PendingMessage
from fade.social.league import announce_league
from fade.trading.markets import MarketCard

USER_A, USER_B = 601, 602
CHAT_GOOD, CHAT_DEAD = -100601, -100602

MARKET = MarketCard(
    market_id="1",
    question="Will it resolve?",
    slug="resolve",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.5"),
    no_price=Decimal("0.5"),
    end_date="2026-12-31",
    open=True,
)


class RecordingReporter:
    def __init__(self) -> None:
        self.reported: list[tuple[BaseException, int | None]] = []

    def report(self, error: BaseException, *, user_id: int | None) -> None:
        self.reported.append((error, user_id))


def failing_bot(bad_chat_id: int, error: Exception | None = None) -> MagicMock:
    """A bot whose send_message raises for one chat/user and works for the rest."""
    error = error or RuntimeError("telegram is having a day")

    async def send_message(chat_id: int, **kwargs):
        if chat_id == bad_chat_id:
            raise error
        return MagicMock()

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=send_message)
    bot.send_photo = AsyncMock()
    return bot


async def seed_two_users(session_factory) -> None:
    async with session_factory() as session:
        for uid, addr_suffix in ((USER_A, "aa"), (USER_B, "bb")):
            session.add(User(telegram_user_id=uid))
            session.add(
                Wallet(
                    telegram_user_id=uid,
                    deposit_wallet_address=f"0xFadE00000000000000000000000000000000{addr_suffix}",
                )
            )
        await session.commit()


# -- (1) per-item isolation: deposit confirms ----------------------------------


async def test_one_failed_deposit_dm_does_not_block_others_and_stays_pending(session_factory):
    await seed_two_users(session_factory)
    async with session_factory() as session:
        session.add(ChainScanState(id=1, last_scanned_block=150))  # watermark initialized
        for i, uid in enumerate((USER_A, USER_B)):
            session.add(
                Deposit(
                    telegram_user_id=uid,
                    tx_hash=f"0x{i:02d}" + "ab" * 31,
                    token="pUSD",
                    amount=Decimal("5"),
                    block_number=100,
                    status="pending",
                )
            )
        await session.commit()

    reporter = RecordingReporter()

    class HeadOnlyRpc:
        async def block_number(self) -> int:
            return 200  # both deposits are past the confirmation threshold

        async def transfers_to(self, *a, **k):
            return []

    scanner = DepositScanner(HeadOnlyRpc(), session_factory, confirmations=12, reporter=reporter)
    bot = failing_bot(USER_A)  # user A blocked the bot

    await scanner.scan_once(bot)

    async with session_factory() as session:
        rows = {d.telegram_user_id: d.status for d in (await session.scalars(select(Deposit)))}
    assert rows[USER_B] == "confirmed"  # the loop continued past the failure
    assert rows[USER_A] == "pending"  # failed DM: NOT marked confirmed, retries next scan
    assert len(reporter.reported) == 1
    assert reporter.reported[0][1] == USER_A

    # Next scan, user A unblocks the bot: the pending deposit confirms.
    ok_bot = MagicMock()
    ok_bot.send_message = AsyncMock()

    class HeadOnlyRpc2(HeadOnlyRpc):
        pass

    scanner2 = DepositScanner(HeadOnlyRpc2(), session_factory, confirmations=12)
    await scanner2.scan_once(ok_bot)
    async with session_factory() as session:
        rows = {d.telegram_user_id: d.status for d in (await session.scalars(select(Deposit)))}
    assert rows[USER_A] == "confirmed"


# -- (1) per-item isolation: resolution scan ------------------------------------


async def test_one_failed_resolution_dm_does_not_block_other_users(session_factory):
    await seed_two_users(session_factory)
    reporter = RecordingReporter()

    async def fetch(address: str):
        return [
            ResolvedPosition(
                token_id="900",
                title="Will it resolve?",
                shares=Decimal("10"),
                avg_price=Decimal("0.5"),
                final_price=Decimal("1"),
            )
        ]

    scanner = ResolutionScanner(session_factory, fetch=fetch, reporter=reporter)
    bot = failing_bot(USER_A)

    notified = await scanner.scan_once(bot)

    assert notified == 1  # user B got theirs despite user A's failure
    assert len(reporter.reported) == 1 and reporter.reported[0][1] == USER_A
    async with session_factory() as session:
        rows = (await session.scalars(select(Resolution))).all()
    assert len(rows) == 2  # both recorded (at-most-once semantics preserved)


async def test_failed_flex_card_send_is_reported_not_fatal(session_factory):
    await seed_two_users(session_factory)
    reporter = RecordingReporter()

    async def fetch(address: str):
        return [
            ResolvedPosition(
                token_id="901",
                title="Winner winner",
                shares=Decimal("10"),
                avg_price=Decimal("0.5"),
                final_price=Decimal("1"),
            )
        ]

    scanner = ResolutionScanner(session_factory, fetch=fetch, reporter=reporter)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock(side_effect=RuntimeError("photo upload failed"))

    notified = await scanner.scan_once(bot)

    assert notified == 2  # crown DMs landed; flex-card failures are cosmetic
    assert bot.send_message.await_count == 2
    assert len(reporter.reported) == 2  # each failed photo send was reported


# -- (1) per-item isolation + bounded retry: broadcast drain ---------------------


async def test_poisoned_broadcast_does_not_block_chat_and_is_dropped(session_factory):
    reporter = RecordingReporter()
    sent: list[str] = []

    async def send(chat_id: int, text: str, **kwargs):
        if text == "poison":
            raise RuntimeError("this message can never send")
        sent.append(text)
        return MagicMock()

    queue = BroadcastQueue(send=send, reporter=reporter)
    queue.enqueue(PendingMessage(chat_id=CHAT_GOOD, text="poison"))
    queue.enqueue(PendingMessage(chat_id=CHAT_GOOD, text="ok 1"))
    queue.enqueue(PendingMessage(chat_id=CHAT_GOOD, text="ok 2"))

    await queue.drain_once()

    assert sent == ["ok 1", "ok 2"]  # the rest of the chat's queue flowed
    assert queue.pending_count(CHAT_GOOD) == 0  # poison dropped, not retried forever
    assert len(reporter.reported) == MAX_SEND_ATTEMPTS  # each failed attempt reported


async def test_failure_in_one_chat_does_not_block_other_chats(session_factory):
    reporter = RecordingReporter()
    sent: list[int] = []

    async def send(chat_id: int, **kwargs):
        if chat_id == CHAT_DEAD:
            raise RuntimeError("dead chat")
        sent.append(chat_id)
        return MagicMock()

    queue = BroadcastQueue(send=send, reporter=reporter)
    queue.enqueue(PendingMessage(chat_id=CHAT_DEAD, text="never"))
    queue.enqueue(PendingMessage(chat_id=CHAT_GOOD, text="always"))

    await queue.drain_once()

    assert sent == [CHAT_GOOD]
    assert reporter.reported  # the dead chat's failure was reported


# -- (2) Forbidden -> clan deactivation ------------------------------------------


async def seed_clans(session_factory) -> None:
    async with session_factory() as session:
        session.add(User(telegram_user_id=USER_A))
        session.add(GroupChat(chat_id=CHAT_GOOD, title="Alive", created_by=USER_A))
        session.add(GroupChat(chat_id=CHAT_DEAD, title="Kicked", created_by=USER_A))
        session.add(GroupMember(chat_id=CHAT_GOOD, telegram_user_id=USER_A))
        session.add(GroupMember(chat_id=CHAT_DEAD, telegram_user_id=USER_A))
        await session.commit()


async def test_forbidden_deactivates_clan_and_stops_future_sends(session_factory):
    await seed_clans(session_factory)
    reporter = RecordingReporter()
    sent: list[int] = []

    async def send(chat_id: int, **kwargs):
        if chat_id == CHAT_DEAD:
            raise Forbidden("bot was kicked from the group chat")
        sent.append(chat_id)
        return MagicMock()

    async def deactivate(chat_id: int) -> None:
        async with session_factory() as session:
            clan = await session.get(GroupChat, chat_id)
            clan.active = False
            await session.commit()

    queue = BroadcastQueue(send=send, on_forbidden=deactivate, reporter=reporter)
    queue.enqueue(PendingMessage(chat_id=CHAT_DEAD, text="msg 1"))
    queue.enqueue(PendingMessage(chat_id=CHAT_DEAD, text="msg 2"))
    queue.enqueue(PendingMessage(chat_id=CHAT_GOOD, text="unaffected"))

    await queue.drain_once()

    assert sent == [CHAT_GOOD]  # the healthy clan is unaffected
    assert queue.pending_count(CHAT_DEAD) == 0  # the dead chat's queue was dropped
    async with session_factory() as session:
        dead = await session.get(GroupChat, CHAT_DEAD)
        good = await session.get(GroupChat, CHAT_GOOD)
    assert dead.active is False
    assert good.active is True
    assert any(isinstance(err, Forbidden) for err, _ in reporter.reported)


async def test_deactivated_clan_is_skipped_by_broadcast_fill_league_and_settlement(
    session_factory, settings
):
    """The three downstream surfaces all skip an inactive clan."""
    await seed_clans(session_factory)
    async with session_factory() as session:
        clan = await session.get(GroupChat, CHAT_DEAD)
        clan.active = False
        await session.commit()

    # broadcast_fill: only the active clan's chat receives the card.
    from fade.bot.handlers.clan import broadcast_fill

    context = MagicMock()
    context.application.bot_data = {
        "sessions": session_factory,
        "broadcasts": BroadcastQueue(send=AsyncMock()),
    }
    await broadcast_fill(
        context,
        telegram_user_id=USER_A,
        username="usera",
        question=MARKET.question,
        side_label="YES",
        yes_token=MARKET.yes_token,
        no_token=MARKET.no_token,
        amount_usdc=Decimal("10"),
        price=Decimal("0.5"),
    )
    queue = context.application.bot_data["broadcasts"]
    assert queue.pending_count(CHAT_GOOD) == 1
    assert queue.pending_count(CHAT_DEAD) == 0

    # league (D-15: clans are teams now; a dead group can only be a clan's
    # HOME chat): the weekly result must not be queued into the dead chat —
    # it falls back to the owner's DM instead.
    async with session_factory() as session:
        session.add(
            Clan(
                name="Kicked Clan",
                name_key="kicked clan",
                icon="⚔️",
                owner_id=USER_A,
                home_chat_id=CHAT_DEAD,
                invite_code="deadhome01",
            )
        )
        await session.commit()
    league_queue = BroadcastQueue(send=AsyncMock())
    assert await announce_league(session_factory, league_queue, datetime.now(UTC)) == 1
    assert league_queue.pending_count(CHAT_DEAD) == 0
    assert league_queue.pending_count(USER_A) == 1

    # challenge settlement: no message to the deactivated chat, but the
    # challenge still transitions to settled (it can never deliver anyway).
    async with session_factory() as session:
        session.add(User(telegram_user_id=USER_B))
        row = Challenge(
            chat_id=CHAT_DEAD,
            challenger_id=USER_A,
            challenger_username="usera",
            opponent_username="userb",
            opponent_id=USER_B,
            market_title=MARKET.question,
            yes_token=MARKET.yes_token,
            no_token=MARKET.no_token,
            challenger_side="YES",
            size_usdc=Decimal("10"),
            challenger_update_id=1,
            status="accepted",
        )
        session.add(row)
        session.add(
            Resolution(
                telegram_user_id=USER_A,
                token_id=MARKET.yes_token,
                market_title=MARKET.question,
                shares=Decimal("10"),
                avg_price=Decimal("0.5"),
                final_price=Decimal("1"),
                pnl=Decimal("5"),
                won=True,
            )
        )
        await session.commit()
        challenge_id = row.id

    bot = MagicMock()
    bot.send_message = AsyncMock()
    posted = await ch.settle_and_expire(session_factory, bot)

    bot.send_message.assert_not_awaited()
    assert posted == 0
    async with session_factory() as session:
        settled = await session.get(Challenge, challenge_id)
    assert settled.status == "settled"  # bounded: never retried forever


# -- (3) UiCard hygiene ----------------------------------------------------------


async def test_card_key_collision_retries_with_fresh_key(session_factory):
    keys = iter(["dead", "dead", "beef"])  # second put collides once, then recovers
    store = CardStore(session_factory=session_factory, keygen=lambda: next(keys))

    first = await store.put("market", MARKET)
    second = await store.put("market", MARKET)

    assert first == "dead"
    assert second == "beef"  # stored under a fresh key, not an IntegrityError
    assert await store.get("market", "beef") is not None


async def test_card_key_exhaustion_raises_after_bounded_attempts(session_factory):
    store = CardStore(session_factory=session_factory, keygen=lambda: "same")
    await store.put("market", MARKET)

    with pytest.raises(RuntimeError, match="unique card key"):
        await store.put("market", MARKET)


async def test_purge_deletes_expired_cards_and_keeps_fresh_ones(session_factory):
    clock = {"now": datetime.now(UTC)}
    store = CardStore(session_factory=session_factory, now=lambda: clock["now"])

    old_key = await store.put("market", MARKET)
    clock["now"] += timedelta(hours=25)  # past the 24h TTL
    fresh_key = await store.put("market", MARKET)

    removed = await store.purge_expired()

    assert removed == 1
    async with session_factory() as session:
        remaining = [c.key for c in (await session.scalars(select(UiCard)))]
    assert remaining == [fresh_key]
    assert await store.get("market", old_key) is None
    assert await store.get("market", fresh_key) is not None
