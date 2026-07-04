"""TradingService: builder code, worst-price guard, FOK, idempotency, pause.

All CLOB calls hit a fake client; no network.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from fade.db.models import Order
from fade.trading.service import TradingService

FAKE_TOKEN = "1234567890"


class FakeClobClient:
    def __init__(self) -> None:
        self.placed: list[dict] = []
        self.reject_next = False

    async def estimate_market_price(self, **kwargs) -> Decimal:
        return Decimal("0.50")

    async def place_market_order(self, **kwargs):
        self.placed.append(kwargs)
        if self.reject_next:
            return SimpleNamespace(ok=False, message="fok_not_filled")
        # BUY: making=USDC spent, taking=shares. SELL: reversed.
        if kwargs["side"] == "BUY":
            return SimpleNamespace(
                ok=True, making_amount=Decimal("10"), taking_amount=Decimal("20")
            )
        return SimpleNamespace(ok=True, making_amount=Decimal("20"), taking_amount=Decimal("9.8"))

    async def get_builder_fee_rates(self, code: str):
        return SimpleNamespace(taker=Decimal("0.01"), maker=Decimal("0"))

    async def get_balance_allowance(self, *, asset_type: str):
        return SimpleNamespace(balance=90_000_000)  # $90 in base units


class FakeWallets:
    def __init__(self, client: FakeClobClient) -> None:
        self._client = client

    @asynccontextmanager
    async def secure_client(self, telegram_user_id: int):
        yield self._client


def make_service(settings, session_factory) -> tuple[TradingService, FakeClobClient]:
    client = FakeClobClient()
    service = TradingService(settings, session_factory, FakeWallets(client))
    return service, client


async def buy(service, update_id: int = 1000, amount: str = "10"):
    return await service.execute_market_order(
        telegram_user_id=701,
        telegram_update_id=update_id,
        token_id=FAKE_TOKEN,
        market_title="Will it rain?",
        side="BUY",
        amount=Decimal(amount),
    )


async def test_market_order_attaches_builder_code_and_worst_price_guard(settings, session_factory):
    service, client = make_service(settings, session_factory)
    result = await buy(service)

    assert result.status == "filled"
    placed = client.placed[0]
    assert placed["builder_code"] == settings.builder_code
    # estimate 0.50 with default 2% slippage -> worst price 0.51, rounded up to cents
    assert placed["max_price"] == Decimal("0.51")
    assert placed["order_type"] == "FOK"


async def test_sell_uses_min_price_guard_and_builder_code(settings, session_factory):
    service, client = make_service(settings, session_factory)
    result = await service.execute_market_order(
        telegram_user_id=701,
        telegram_update_id=1001,
        token_id=FAKE_TOKEN,
        market_title="Will it rain?",
        side="SELL",
        amount=Decimal("20"),
    )

    assert result.status == "filled"
    placed = client.placed[0]
    assert placed["builder_code"] == settings.builder_code
    assert placed["min_price"] == Decimal("0.49")  # 0.50 - 2%
    assert placed["order_type"] == "FOK"


async def test_duplicate_update_id_does_not_create_second_order(settings, session_factory):
    """A Telegram webhook retry re-delivers the same update_id: no double trade."""
    service, client = make_service(settings, session_factory)

    first = await buy(service, update_id=2000)
    retry = await buy(service, update_id=2000)  # same update delivered again

    assert first.status == "filled"
    assert retry.status == "duplicate"
    assert len(client.placed) == 1  # the CLOB saw exactly one order
    async with session_factory() as session:
        orders = (await session.scalars(select(Order))).all()
    assert len(orders) == 1


async def test_fill_reports_price_fee_and_new_balance(settings, session_factory):
    service, _ = make_service(settings, session_factory)
    result = await buy(service)

    assert result.fill_price == Decimal("0.5000")  # $10 for 20 shares
    assert result.fill_shares == Decimal("20")
    assert result.fee == Decimal("0.10")  # 1% of $10
    assert result.new_balance == Decimal("90")


async def test_paused_trading_blocks_orders_before_clob(settings, session_factory):
    service, client = make_service(settings, session_factory)
    await service.set_paused(True, by_user_id=1)

    result = await buy(service, update_id=3000)

    assert result.status == "paused"
    assert client.placed == []

    await service.set_paused(False, by_user_id=1)
    resumed = await buy(service, update_id=3001)
    assert resumed.status == "filled"


async def test_rejected_order_is_recorded_and_reported(settings, session_factory):
    service, client = make_service(settings, session_factory)
    client.reject_next = True

    result = await buy(service, update_id=4000)

    assert result.status == "rejected"
    assert "fok_not_filled" in result.error
    async with session_factory() as session:
        order = await session.scalar(select(Order))
    assert order is not None and order.status == "rejected"
