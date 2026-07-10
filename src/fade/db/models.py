"""Main application database models.

Security rule (CLAUDE.md): no private keys, mnemonics, or signing credentials in
this database, ever. Key material lives only in the wallets signing store.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # PRD F-17 privacy toggle: hide dollar amounts in group broadcasts.
    hide_amounts: Mapped[bool] = mapped_column(Boolean, default=False)
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


class Order(Base):
    """One order attempt. The unique telegram_update_id is the idempotency
    barrier: a webhook retry re-delivers the same update_id, the insert
    conflicts, and the trade is not executed twice (PRD §6)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), index=True
    )
    telegram_update_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    token_id: Mapped[str] = mapped_column(String(80))
    market_title: Mapped[str] = mapped_column(String(200))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6))  # USDC for BUY, shares for SELL
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | filled | rejected | failed
    clob_order_id: Mapped[str | None] = mapped_column(String(80))
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    fill_shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    fill_usdc: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WithdrawalAddress(Base):
    """Destination addresses a user has explicitly confirmed (PRD F-4)."""

    __tablename__ = "withdrawal_addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), index=True
    )
    address: Mapped[str] = mapped_column(String(42))
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Clan(Base):
    """A group chat registered via /setup (PRD F-16)."""

    __tablename__ = "clans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClanMember(Base):
    """Membership observed from group activity (/setup, /join, fade/copy taps)."""

    __tablename__ = "clan_members"

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("clans.chat_id"), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Resolution(Base):
    """A user's position settled by market resolution (PRD F-14, D-6).

    One row per (user, token): the unique constraint IS the idempotency
    barrier — a second poll of the same resolution conflicts and never
    re-notifies, surviving restarts (same discipline as deposits.tx_hash).
    Feeds the leaderboard's resolution-realized PnL (the other half of D-6).
    """

    __tablename__ = "resolutions"
    __table_args__ = (UniqueConstraint("telegram_user_id", "token_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id"), index=True
    )
    token_id: Mapped[str] = mapped_column(String(80))
    market_title: Mapped[str] = mapped_column(String(200))
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    avg_price: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    final_price: Mapped[Decimal] = mapped_column(Numeric(10, 6))  # 1 won, 0 lost
    pnl: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    won: Mapped[bool] = mapped_column(Boolean)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UiCard(Base):
    """Durable store for in-flight interactive cards (market/position/fade).

    Survives process restarts so a redeploy doesn't strand someone mid-trade.
    Rows past the TTL are treated as expired ("card expired" UX, not a crash).
    Payload is JSON of the card dataclass; no key material, ever.
    """

    __tablename__ = "ui_cards"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # market | position | fade
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
