"""Resolution lifecycle (PRD F-14): roast or crown when a market settles.

Same restart-safe discipline as the deposit scanner: detection state persists
in the database (the Resolution row's unique (user, token) constraint is the
idempotency barrier), so a second poll — or a poll after a restart — never
double-notifies.

Brand voice applies HERE by design: F-14 is the one money-adjacent moment the
PRD explicitly wants personality ("roast or crown them"). Deposit/withdraw/
confirmation screens stay plain; this one gets to talk.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.ext import ContextTypes

from fade.bot.handlers.common import sanitize
from fade.db.models import Resolution, Wallet
from fade.errors import ErrorReporter, report_safely

logger = logging.getLogger(__name__)

CROWN_TEXT = (
    "👑 Called it.\n"
    '"{title}" resolved your way: +${pnl}.\n'
    "{shares} shares settled at $1. The record remembers who was right."
)
ROAST_TEXT = (
    '💀 "{title}" resolved against you: -${pnl}.\n'
    "{shares} shares settled at zero. Somewhere, someone who faded you is "
    "smiling. Study the tape, come back meaner."
)


@dataclass(frozen=True)
class ResolvedPosition:
    """One settled position, as reported by the data source."""

    token_id: str
    title: str
    shares: Decimal
    avg_price: Decimal
    final_price: Decimal  # 1 for the winning side, 0 for the losing side
    outcome: str = ""  # "Yes" / "No" label, when the data source reports one


class ResolutionScanner:
    """Polls each user's positions for settled markets and DMs the verdict.

    `fetch` is injectable (an async callable: wallet address -> list of
    ResolvedPosition) so tests never touch a live network — same pattern as
    the deposit scanner's Rpc.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fetch: Callable[[str], Any],
        reporter: ErrorReporter | None = None,
    ) -> None:
        self._sessions = session_factory
        self._fetch = fetch
        self._reporter = reporter

    async def scan_once(self, bot: Any) -> int:
        """Detect and notify newly resolved positions. Returns DMs sent."""
        async with self._sessions() as session:
            wallets = (await session.scalars(select(Wallet))).all()
        notified = 0
        for wallet in wallets:
            try:
                resolved = await self._fetch(wallet.deposit_wallet_address)
            except Exception:
                logger.exception("resolution fetch failed for %s", wallet.deposit_wallet_address)
                continue
            for position in resolved:
                if await self._record_and_notify(bot, wallet.telegram_user_id, position):
                    notified += 1
        return notified

    async def _record_and_notify(
        self, bot: Any, telegram_user_id: int, position: ResolvedPosition
    ) -> bool:
        pnl = ((position.final_price - position.avg_price) * position.shares).quantize(
            Decimal("0.01")
        )
        won = position.final_price >= Decimal("0.5")
        # Idempotency barrier FIRST: the unique (user, token) insert must win
        # before any DM goes out, so a crash after insert costs a notification
        # at worst — never a duplicate.
        async with self._sessions() as session:
            session.add(
                Resolution(
                    telegram_user_id=telegram_user_id,
                    token_id=position.token_id,
                    market_title=position.title[:200],
                    shares=position.shares,
                    avg_price=position.avg_price,
                    final_price=position.final_price,
                    pnl=pnl,
                    won=won,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                return False  # already recorded (earlier poll or pre-restart)

        template = CROWN_TEXT if won else ROAST_TEXT
        # One user's failed DM (blocked bot, Telegram error) must not abort the
        # scan for everyone else. The Resolution row already exists, so nothing
        # is retried: at-most-once notification, loop keeps going (Stage 11).
        try:
            await bot.send_message(
                chat_id=telegram_user_id,
                text=template.format(
                    title=sanitize(position.title),
                    pnl=f"{abs(pnl):.2f}",
                    shares=position.shares,
                ),
            )
        except Exception as error:
            logger.exception("resolution DM failed for user %s", telegram_user_id)
            await report_safely(self._reporter, error, user_id=telegram_user_id)
            return False
        if won:
            await self._send_flex_card(bot, telegram_user_id, position, pnl)
        return True

    async def _send_flex_card(
        self, bot: Any, telegram_user_id: int, position: ResolvedPosition, pnl: Decimal
    ) -> None:
        from fade.social.flex_card import generate_flex_card

        try:
            image = generate_flex_card(position, pnl)
        except Exception as error:
            logger.exception("flex card generation failed for %s", telegram_user_id)
            await report_safely(self._reporter, error, user_id=telegram_user_id)
            return
        # Growth loop (D-14): the flex card is the artifact that leaves the
        # group chat — its caption carries the winner's referral link so a
        # shared screenshot's owner can answer "what app is that" in one tap.
        from fade.bot.handlers.invite import referral_link

        caption = (
            "Called it. Share the receipt.\n"
            f"Ride with me: {referral_link(getattr(bot, 'username', 'fade'), telegram_user_id)}"
        )
        try:
            await bot.send_photo(
                chat_id=telegram_user_id,
                photo=image,
                caption=caption,
            )
        except Exception as error:
            # The crown DM already landed; losing the flex card is cosmetic.
            logger.exception("flex card send failed for user %s", telegram_user_id)
            await report_safely(self._reporter, error, user_id=telegram_user_id)


# Same bound rationale as TradingService.MAX_POSITION_PAGES: fixes silent
# first-page truncation for heavy traders while capping how long one wallet
# can occupy the 5-minute scanner (10 pages ≈ 1000 positions).
MAX_RESOLUTION_PAGES = 10


async def fetch_resolved_positions(address: str, client_factory=None) -> list[ResolvedPosition]:
    """Production fetcher: redeemable positions from the Polymarket data API,
    across ALL pages (bounded). `client_factory` is injectable for tests."""
    if client_factory is None:
        from polymarket import AsyncPublicClient

        client_factory = AsyncPublicClient

    resolved: list[ResolvedPosition] = []
    async with client_factory() as client:
        pages = 0
        async for page in client.list_positions(user=address):
            for pos in page.items:
                if not getattr(pos, "redeemable", False):
                    continue
                if pos.token_id is None or not pos.size or pos.avg_price is None:
                    continue
                final = Decimal("1") if (pos.cur_price or 0) >= Decimal("0.5") else Decimal("0")
                resolved.append(
                    ResolvedPosition(
                        token_id=str(pos.token_id),
                        title=pos.title or "(unknown market)",
                        shares=pos.size,
                        avg_price=pos.avg_price,
                        final_price=final,
                        outcome=pos.outcome or "",
                    )
                )
            pages += 1
            if pages >= MAX_RESOLUTION_PAGES:
                break
    return resolved


async def scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    scanner: ResolutionScanner = context.application.bot_data["resolution_scanner"]
    await scanner.scan_once(context.bot)
    # Late import: challenge handlers import this module's ResolvedPosition.
    from fade.bot.handlers.challenge import settle_and_expire

    # F-24 settlement rides this job: the Resolution rows just recorded are
    # what decides accepted challenges; stale proposals expire on the same tick.
    await settle_and_expire(
        context.application.bot_data["sessions"],
        context.bot,
        reporter=context.application.bot_data.get("error_reporter"),
    )
