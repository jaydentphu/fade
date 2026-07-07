"""Shared fixtures. All keys/tokens here are obviously fake; no test hits a network."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fade.config import Settings
from fade.db.models import Base


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="0000000000:FAKE-TEST-TOKEN",
        admin_user_id=1,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'main.db').as_posix()}",
        signing_database_url=f"sqlite+aiosqlite:///{(tmp_path / 'signing.db').as_posix()}",
        fade_signing_key=Fernet.generate_key().decode(),
        relayer_api_key="00000000-0000-0000-0000-000000000000",
        relayer_api_key_address="0x0000000000000000000000000000000000000000",
        builder_code="0x" + "00" * 32,
        polygon_rpc_url="http://127.0.0.1:0/never-called",
    )


@pytest.fixture
async def session_factory(settings):
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class SharedFakeClobClient:
    """Fake CLOB client for Phase 3 hardening tests. No network, ever."""

    def __init__(self) -> None:
        self.placed: list[dict] = []

    async def estimate_market_price(self, **kwargs):
        from decimal import Decimal

        return Decimal("0.50")

    async def place_market_order(self, **kwargs):
        from decimal import Decimal
        from types import SimpleNamespace

        self.placed.append(kwargs)
        if kwargs["side"] == "BUY":
            return SimpleNamespace(
                ok=True, making_amount=Decimal("10"), taking_amount=Decimal("20")
            )
        return SimpleNamespace(ok=True, making_amount=Decimal("20"), taking_amount=Decimal("9.8"))

    async def get_builder_fee_rates(self, code: str):
        from decimal import Decimal
        from types import SimpleNamespace

        return SimpleNamespace(taker=Decimal("0.01"), maker=Decimal("0"))

    async def get_balance_allowance(self, *, asset_type: str):
        from types import SimpleNamespace

        return SimpleNamespace(balance=90_000_000)


@pytest.fixture
def real_trading(settings, session_factory):
    """A REAL TradingService over a fake CLOB client (for handler-level audits)."""
    from contextlib import asynccontextmanager

    from fade.trading.service import TradingService

    clob = SharedFakeClobClient()

    class FakeWallets:
        @asynccontextmanager
        async def secure_client(self, telegram_user_id: int):
            yield clob

    return TradingService(settings, session_factory, FakeWallets()), clob


@pytest.fixture
def onboard(session_factory):
    """Callable fixture: insert a user who has accepted the attestation gate."""
    from datetime import UTC, datetime

    from fade.db.models import User

    async def _onboard(telegram_user_id: int) -> None:
        async with session_factory() as session:
            session.add(
                User(
                    telegram_user_id=telegram_user_id,
                    attestation_accepted_at=datetime.now(UTC),
                    attestation_version="test",
                )
            )
            await session.commit()

    return _onboard
