"""Safety hardening (PRD §7.7, §6; D-12): daily volume cap, global broadcast
rate cap, and the admin anomaly alert. No live network; fake clocks throughout.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from types import SimpleNamespace

from sqlalchemy import select

from fade.db.models import Order
from fade.social.broadcast import GLOBAL_PER_SECOND, BroadcastQueue, PendingMessage
from fade.trading.service import TradingService

USER_ID = 970
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

_update_ids = count(97000)


class FakeClob:
    """Records placed orders; no network. Local to keep this file self-contained."""

    def __init__(self) -> None:
        self.placed: list[dict] = []

    async def estimate_market_price(self, **kwargs):
        return Decimal("0.50")

    async def place_market_order(self, **kwargs):
        self.placed.append(kwargs)
        if kwargs["side"] == "BUY":
            return SimpleNamespace(
                ok=True, making_amount=Decimal("10"), taking_amount=Decimal("20")
            )
        return SimpleNamespace(ok=True, making_amount=Decimal("20"), taking_amount=Decimal("9.8"))

    async def get_builder_fee_rates(self, code: str):
        return SimpleNamespace(taker=Decimal("0.01"), maker=Decimal("0"))

    async def get_balance_allowance(self, *, asset_type: str):
        return SimpleNamespace(balance=90_000_000)


class FrozenClock:
    def __init__(self, t) -> None:
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, delta) -> None:
        self.t = self.t + delta


def make_service(settings, session_factory, clock=None, alert=None):
    clob = FakeClob()

    class FakeWallets:
        @asynccontextmanager
        async def secure_client(self, telegram_user_id: int):
            yield clob

    service = TradingService(settings, session_factory, FakeWallets(), now=clock, alert=alert)
    return service, clob


async def seed_filled_buy(session_factory, usdc: str, created_at: datetime) -> None:
    update_id = next(_update_ids)
    async with session_factory() as session:
        session.add(
            Order(
                telegram_user_id=USER_ID,
                telegram_update_id=update_id,
                idempotency_key=f"k{update_id}",
                token_id="111",
                market_title="Will it rain?",
                side="BUY",
                amount=Decimal(usdc),
                status="filled",
                fill_price=Decimal("0.5"),
                fill_shares=Decimal(usdc) * 2,
                fill_usdc=Decimal(usdc),
                created_at=created_at.astimezone(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()


async def buy(service, amount: str):
    return await service.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=next(_update_ids),
        token_id="111",
        market_title="Will it rain?",
        side="BUY",
        amount=Decimal(amount),
    )


# -- (1) daily volume cap ------------------------------------------------------


async def test_order_over_daily_cap_is_rejected_before_the_clob(settings, session_factory, onboard):
    """Rolling-24h buys + this order > cap -> plain rejection, CLOB untouched."""
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    service, clob = make_service(settings, session_factory, clock=clock)
    # $950 of filled buys in the last 24h against the $1000 default cap.
    await seed_filled_buy(session_factory, "950", T0 - timedelta(hours=3))

    result = await buy(service, "100")  # would make $1050 > $1000

    assert result.status == "capped"
    assert "Daily trade limit reached" in result.error
    assert "sell" in result.error and "withdraw" in result.error  # exits stay open
    assert clob.placed == []  # never reached the CLOB
    async with session_factory() as session:
        orders = (await session.scalars(select(Order).where(Order.status != "filled"))).all()
    assert orders == []  # no order row was even created


async def test_order_under_daily_cap_is_unaffected(settings, session_factory, onboard):
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    service, clob = make_service(settings, session_factory, clock=clock)
    await seed_filled_buy(session_factory, "950", T0 - timedelta(hours=3))

    result = await buy(service, "25")  # $975 total, under the $1000 cap

    assert result.status == "filled"
    assert len(clob.placed) == 1


async def test_cap_resets_after_24_hours(settings, session_factory, onboard):
    """Advancing the clock past the rolling window frees the budget again."""
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    service, clob = make_service(settings, session_factory, clock=clock)
    await seed_filled_buy(session_factory, "995", T0 - timedelta(hours=3))

    blocked = await buy(service, "100")
    assert blocked.status == "capped"

    clock.advance(timedelta(hours=25))  # the $995 falls out of the window
    allowed = await buy(service, "100")
    assert allowed.status == "filled"
    assert len(clob.placed) == 1


async def test_sells_are_never_capped(settings, session_factory, onboard):
    """Exits stay open even when the buy budget is exhausted (D-12)."""
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    service, clob = make_service(settings, session_factory, clock=clock)
    await seed_filled_buy(session_factory, "1000", T0 - timedelta(hours=3))  # cap fully used

    result = await service.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=next(_update_ids),
        token_id="111",
        market_title="Will it rain?",
        side="SELL",
        amount=Decimal("20"),
    )

    assert result.status == "filled"
    assert len(clob.placed) == 1


# -- (2) global broadcast rate cap ---------------------------------------------


class TickClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def test_global_cap_limits_a_drain_even_when_every_chat_is_under_its_own_budget():
    """PRD §6: ~30 msgs/second across ALL chats, on top of per-chat 20/min."""
    sent: list[dict] = []

    async def send(**kwargs):
        sent.append(kwargs)
        return type("Msg", (), {"message_id": len(sent)})()

    clock = TickClock()
    queue = BroadcastQueue(send=send, now=clock)
    # 45 different chats, one message each: every chat is far under its own
    # 20/minute budget, so only the GLOBAL cap can be the limiter.
    for chat_id in range(45):
        queue.enqueue(PendingMessage(chat_id=chat_id, text=f"m{chat_id}"))

    await queue.drain_once()
    assert len(sent) == GLOBAL_PER_SECOND  # 30, not 45

    # Within the same second: still capped.
    await queue.drain_once()
    assert len(sent) == GLOBAL_PER_SECOND

    # A second later the window clears and the remainder flows.
    clock.advance(1.1)
    await queue.drain_once()
    assert len(sent) == 45


# -- (3) anomaly alert -----------------------------------------------------------


def make_alert_recorder():
    calls: list[str] = []

    async def alert(text: str) -> None:
        calls.append(text)

    return alert, calls


async def test_anomaly_alert_fires_on_volume_spike_and_only_once_per_incident(
    settings, session_factory, onboard
):
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    alert, calls = make_alert_recorder()
    service, _ = make_service(settings, session_factory, clock=clock, alert=alert)

    # Baseline: ~$10/day over the prior week -> threshold = max(5*10, 200) = $200.
    for day in range(2, 8):
        await seed_filled_buy(session_factory, "10", T0 - timedelta(days=day))
    # Spike: $240 already filled in the last 24h, far over the $200 threshold.
    await seed_filled_buy(session_factory, "240", T0 - timedelta(hours=2))

    await buy(service, "10")  # the fill that trips the check
    assert len(calls) == 1
    assert str(USER_ID) in calls[0]
    assert "anomaly" in calls[0].lower()

    await buy(service, "10")  # still in the same incident: no alert spam
    assert len(calls) == 1


async def test_anomaly_alert_does_not_fire_for_normal_volume(settings, session_factory, onboard):
    await onboard(USER_ID)
    clock = FrozenClock(T0)
    alert, calls = make_alert_recorder()
    service, _ = make_service(settings, session_factory, clock=clock, alert=alert)

    # Steady $50/day baseline; today is another ordinary $10 buy (recent stays
    # far under max(5*50, 200) = $250).
    for day in range(2, 8):
        await seed_filled_buy(session_factory, "50", T0 - timedelta(days=day))

    await buy(service, "10")
    assert calls == []
