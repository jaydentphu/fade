"""Main application database models.

Security rule (CLAUDE.md): no private keys, mnemonics, or signing credentials in
this database, ever. Key material lives only in the wallets signing store.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    attestation_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attestation_version: Mapped[str | None] = mapped_column(String(32))
    slippage_bps: Mapped[int] = mapped_column(Integer, default=200)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    wallet: Mapped[Wallet | None] = relationship(back_populates="user")

    @property
    def onboarded(self) -> bool:
        return self.attestation_accepted_at is not None


class Wallet(Base):
    __tablename__ = "wallets"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), primary_key=True
    )
    # Public on-chain address only. Never key material.
    deposit_wallet_address: Mapped[str] = mapped_column(String(42), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="wallet")


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), index=True
    )
    tx_hash: Mapped[str] = mapped_column(String(66), unique=True)
    token: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    block_number: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChainScanState(Base):
    """Singleton watermark for the deposit scanner (row id is always 1)."""

    __tablename__ = "chain_scan_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_scanned_block: Mapped[int] = mapped_column(BigInteger)


class TradingPauseFlag(Base):
    """Singleton kill-switch flag (row id is always 1). Wired up in slice 3."""

    __tablename__ = "trading_pause"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_by: Mapped[int | None] = mapped_column(BigInteger)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
