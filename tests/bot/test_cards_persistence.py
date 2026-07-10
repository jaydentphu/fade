"""Durable card store survives a simulated process restart, and expires by TTL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fade.bot.cards import CardStore
from fade.bot.handlers.clan import FadeCard
from fade.trading.markets import MarketCard
from fade.trading.service import PositionView

MARKET = MarketCard(
    market_id="1",
    question="Will it rain tomorrow?",
    slug="rain",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.61"),
    no_price=Decimal("0.39"),
    end_date="2026-09-30",
    open=True,
)
VIEW = PositionView(
    token_id="111",
    title="Will it rain tomorrow?",
    outcome="Yes",
    shares=Decimal("16.4"),
    avg_price=Decimal("0.61"),
    cur_price=Decimal("0.70"),
    cash_pnl=Decimal("1.48"),
    percent_pnl=14.8,
)
FADE = FadeCard(
    chat_id=-100,
    orig_user_id=1,
    orig_username="priya",
    question="Will it rain tomorrow?",
    side_label="YES",
    yes_token="111",
    no_token="222",
    message_id=7,
)


class FrozenClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


async def test_market_card_survives_a_simulated_restart(session_factory):
    # Store A writes the card (memory + DB).
    store_a = CardStore(session_factory=session_factory)
    key = await store_a.put("market", MARKET)

    # Restart: a brand-new store with empty memory but the same DB.
    store_b = CardStore(session_factory=session_factory)
    revived = await store_b.get("market", key)

    assert revived is not None
    assert revived.question == MARKET.question
    assert revived.yes_token == "111"
    assert revived.yes_price == Decimal("0.61")  # Decimal survived JSON round-trip


async def test_position_and_fade_cards_survive_restart(session_factory):
    store_a = CardStore(session_factory=session_factory)
    pos_key = await store_a.put("position", VIEW)
    fade_key = await store_a.put("fade", FADE)

    store_b = CardStore(session_factory=session_factory)
    pos = await store_b.get("position", pos_key)
    fade = await store_b.get("fade", fade_key)

    assert pos is not None and pos.shares == Decimal("16.4")
    assert fade is not None and fade.message_id == 7 and fade.side_label == "YES"


async def test_card_older_than_ttl_is_treated_as_expired(session_factory):
    born = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    clock = FrozenClock(born)
    store_a = CardStore(session_factory=session_factory, ttl=timedelta(hours=24), now=clock)
    key = await store_a.put("market", MARKET)

    # New process (empty memory), clock advanced past the TTL.
    later = FrozenClock(born + timedelta(hours=25))
    store_b = CardStore(session_factory=session_factory, ttl=timedelta(hours=24), now=later)
    assert await store_b.get("market", key) is None  # expired, not a crash


async def test_wrong_kind_lookup_returns_none(session_factory):
    store = CardStore(session_factory=session_factory)
    key = await store.put("market", MARKET)
    assert await store.get("position", key) is None


async def test_update_persists_mutation_across_restart(session_factory):
    store_a = CardStore(session_factory=session_factory)
    card = FadeCard(
        chat_id=-100,
        orig_user_id=1,
        orig_username="priya",
        question="Q",
        side_label="YES",
        yes_token="111",
        no_token="222",
    )
    key = await store_a.put("fade", card)
    card.message_id = 42
    await store_a.update("fade", key, card)

    store_b = CardStore(session_factory=session_factory)
    revived = await store_b.get("fade", key)
    assert revived.message_id == 42


async def test_memory_only_store_works_without_a_database():
    store = CardStore(session_factory=None)
    key = await store.put("market", MARKET)
    assert (await store.get("market", key)).question == MARKET.question
