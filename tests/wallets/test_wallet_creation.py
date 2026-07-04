"""WalletService.create_wallet: mocked SDK client, no key leaves the boundary."""

from __future__ import annotations

from sqlalchemy import select

import fade.wallets as wallets_pkg
from fade.db.models import Wallet
from fade.wallets import WalletService

FAKE_ADDRESS = "0xFadE0000000000000000000000000000000000aa"


class FakeSecureClient:
    created_with: list[dict] = []

    def __init__(self) -> None:
        self.wallet = FAKE_ADDRESS

    @classmethod
    async def create(cls, **kwargs):
        cls.created_with.append(kwargs)
        return cls()

    async def close(self) -> None:
        pass


async def test_create_wallet_persists_address_and_is_idempotent(
    settings, session_factory, monkeypatch
):
    FakeSecureClient.created_with = []
    monkeypatch.setattr(wallets_pkg, "AsyncSecureClient", FakeSecureClient)
    service = WalletService(settings, session_factory)

    address = await service.create_wallet(201)
    assert address == FAKE_ADDRESS
    async with session_factory() as session:
        row = await session.scalar(select(Wallet).where(Wallet.telegram_user_id == 201))
    assert row is not None and row.deposit_wallet_address == FAKE_ADDRESS

    # Second call returns the stored address without re-deploying.
    again = await service.create_wallet(201)
    assert again == FAKE_ADDRESS
    assert len(FakeSecureClient.created_with) == 1
    await service.close()


async def test_raw_key_never_reaches_main_database(
    settings, session_factory, monkeypatch, tmp_path
):
    FakeSecureClient.created_with = []
    monkeypatch.setattr(wallets_pkg, "AsyncSecureClient", FakeSecureClient)
    service = WalletService(settings, session_factory)
    await service.create_wallet(202)
    await service.close()

    private_key = FakeSecureClient.created_with[0]["private_key"]
    main_db_bytes = (tmp_path / "main.db").read_bytes()
    assert private_key.encode() not in main_db_bytes
    assert private_key.removeprefix("0x").encode() not in main_db_bytes


async def test_wallet_creation_passes_relayer_key(settings, session_factory, monkeypatch):
    FakeSecureClient.created_with = []
    monkeypatch.setattr(wallets_pkg, "AsyncSecureClient", FakeSecureClient)
    service = WalletService(settings, session_factory)
    await service.create_wallet(203)
    assert FakeSecureClient.created_with[0]["api_key"] is not None
    await service.close()
