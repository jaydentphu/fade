"""Public interface of the wallets package.

Everything other modules are allowed to call lives here. Raw key material never
crosses this boundary: callers get addresses, balances, or a ready-to-use
authenticated client object scoped to one action — never a key string.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import TYPE_CHECKING

from polymarket import AsyncSecureClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fade.config import Settings
from fade.db.models import Wallet
from fade.wallets.bridge import fetch_deposit_addresses
from fade.wallets.relayer import build_relayer_api_key
from fade.wallets.signing import SigningStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PUSD_DECIMALS = Decimal(10) ** 6


class WalletService:
    def __init__(
        self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._signing = SigningStore(settings.signing_database_url, settings.fade_signing_key)
        self._relayer_key = build_relayer_api_key(settings)

    async def create_wallet(self, telegram_user_id: int) -> str:
        """Create (or return) the user's deposit wallet; auto-deploys via the Relayer.

        Idempotent: an existing wallet address is returned as-is.
        """
        async with self._sessions() as session:
            existing = await session.scalar(
                select(Wallet).where(Wallet.telegram_user_id == telegram_user_id)
            )
            if existing is not None:
                return existing.deposit_wallet_address

        private_key = await self._signing.create_signer(telegram_user_id)
        client = await AsyncSecureClient.create(private_key=private_key, api_key=self._relayer_key)
        try:
            address = str(client.wallet)
        finally:
            await client.close()

        async with self._sessions() as session:
            session.add(Wallet(telegram_user_id=telegram_user_id, deposit_wallet_address=address))
            await session.commit()
        return address

    async def get_wallet_address(self, telegram_user_id: int) -> str | None:
        async with self._sessions() as session:
            wallet = await session.scalar(
                select(Wallet).where(Wallet.telegram_user_id == telegram_user_id)
            )
        return wallet.deposit_wallet_address if wallet else None

    async def get_balance(self, telegram_user_id: int) -> Decimal:
        """The user's pUSD balance (dollars)."""
        async with self.secure_client(telegram_user_id) as client:
            balance = await client.get_balance_allowance(asset_type="COLLATERAL")
        return Decimal(balance.balance) / PUSD_DECIMALS

    async def get_deposit_addresses(self, telegram_user_id: int) -> dict[str, str]:
        address = await self.get_wallet_address(telegram_user_id)
        if address is None:
            raise KeyError(f"User {telegram_user_id} has no wallet")
        return await fetch_deposit_addresses(address, self._settings.builder_code)

    @asynccontextmanager
    async def secure_client(self, telegram_user_id: int) -> AsyncIterator[AsyncSecureClient]:
        """Authenticated client for one action. The key never leaves this scope."""
        private_key = await self._signing.get_private_key(telegram_user_id)
        client = await AsyncSecureClient.create(private_key=private_key, api_key=self._relayer_key)
        del private_key
        try:
            yield client
        finally:
            await client.close()

    async def close(self) -> None:
        await self._signing.close()


__all__ = ["PUSD_DECIMALS", "WalletService"]
