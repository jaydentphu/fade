"""Signing store: the ONLY code in the codebase that touches key material.

Rules (CLAUDE.md, PRD §7):
- Keys are encrypted at rest (Fernet) in a dedicated SQLite store that is a
  physically separate file from the main application database.
- No function here may log, print, or embed key material in an exception.
- Nothing outside the fade.wallets package may import this module.
"""

from __future__ import annotations

from eth_account import Account
from sqlalchemy import BigInteger, DateTime, LargeBinary, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from cryptography.fernet import Fernet
except ImportError as _err:  # pragma: no cover
    raise RuntimeError("cryptography is required for the signing store") from _err


class _SigningBase(DeclarativeBase):
    pass


class SignerKey(_SigningBase):
    __tablename__ = "signer_keys"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SigningStore:
    """Encrypted-at-rest per-user signer storage.

    The same interface can later be backed by managed infra (e.g. Turnkey)
    without changing callers.
    """

    def __init__(self, database_url: str, fernet_key: str) -> None:
        if database_url.startswith("sqlite") and ":memory:" not in database_url:
            pass  # separate file enforced by configuration; asserted in tests
        self._engine = create_async_engine(database_url)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        self._fernet = Fernet(fernet_key.encode())
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(_SigningBase.metadata.create_all)
        self._initialized = True

    async def create_signer(self, telegram_user_id: int) -> str:
        """Create (or return the existing) signer for a user.

        Returns the private key hex string for immediate, transient use by the
        wallets package only. Callers inside fade.wallets must not persist or
        log it; callers outside fade.wallets must never see it.
        """
        await self._ensure_schema()
        existing = await self._load(telegram_user_id)
        if existing is not None:
            return existing
        account = Account.create()
        private_key = account.key.to_0x_hex()
        encrypted = self._fernet.encrypt(private_key.encode())
        async with self._sessions() as session:
            session.add(SignerKey(telegram_user_id=telegram_user_id, encrypted_key=encrypted))
            await session.commit()
        return private_key

    async def get_private_key(self, telegram_user_id: int) -> str:
        await self._ensure_schema()
        key = await self._load(telegram_user_id)
        if key is None:
            raise KeyError(f"No signer for user {telegram_user_id}")
        return key

    async def _load(self, telegram_user_id: int) -> str | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(SignerKey).where(SignerKey.telegram_user_id == telegram_user_id)
            )
        if row is None:
            return None
        return self._fernet.decrypt(row.encrypted_key).decode()

    async def close(self) -> None:
        await self._engine.dispose()
