"""Order execution (PRD F-10, F-11, F-13) with idempotency (PRD §6).

Every order:
- is FOK with a worst-price limit derived from the user's slippage setting,
- carries the builder code from settings,
- is recorded BEFORE hitting the CLOB under a unique telegram_update_id, so a
  Telegram webhook retry (same update_id) can never execute twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from polymarket import AsyncPublicClient
from polymarket.errors import UnexpectedResponseError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fade.config import Settings
from fade.db.models import Order, TradingPauseFlag, User
from fade.trading.markets import MarketCard, MarketLookupError, market_to_card
from fade.wallets import PUSD_DECIMALS, WalletService

CENT = Decimal("0.01")


@dataclass(frozen=True)
class OrderResult:
    status: str  # filled | rejected | duplicate | paused | failed
    side: str = ""
    fill_price: Decimal | None = None
    fill_shares: Decimal | None = None
    fill_usdc: Decimal | None = None
    fee: Decimal | None = None
    new_balance: Decimal | None = None
    error: str = ""


@dataclass(frozen=True)
class PositionView:
    token_id: str
    title: str
    outcome: str
    shares: Decimal
    avg_price: Decimal | None
    cur_price: Decimal | None
    cash_pnl: Decimal | None
    percent_pnl: float | None


class TradingService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        wallets: WalletService,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._wallets = wallets

    # -- market data ---------------------------------------------------------

    async def get_market_card(self, url: str) -> MarketCard:
        async with AsyncPublicClient() as client:
            try:
                market = await client.get_market(url=url)
            except UnexpectedResponseError as error:
                raise MarketLookupError(
                    "Only binary YES/NO markets are supported for now."
                ) from error
        return market_to_card(market)

    async def list_trending(self, limit: int = 5) -> list[MarketCard]:
        cards: list[MarketCard] = []
        async with AsyncPublicClient() as client:
            page = await client.list_markets(
                closed=False, order="volumeNum", ascending=False
            ).first_page()
        for market in page.items:
            try:
                card = market_to_card(market)
            except MarketLookupError:
                continue
            if card.open:
                cards.append(card)
            if len(cards) >= limit:
                break
        return cards

    async def search_markets(self, query: str, limit: int = 5) -> list[MarketCard]:
        cards: list[MarketCard] = []
        async with AsyncPublicClient() as client:
            page = await client.search(q=query).first_page()
            for result in page.items:
                for event in getattr(result, "events", ()) or ():
                    for market in getattr(event, "markets", ()) or ():
                        slug = getattr(market, "slug", None)
                        if not slug:
                            continue
                        try:
                            full = await client.get_market(slug=slug)
                            card = market_to_card(full)
                        except (UnexpectedResponseError, MarketLookupError):
                            continue
                        if card.open:
                            cards.append(card)
                        if len(cards) >= limit:
                            return cards
        return cards

    # -- kill switch ---------------------------------------------------------

    async def is_paused(self) -> bool:
        async with self._sessions() as session:
            flag = await session.get(TradingPauseFlag, 1)
        return bool(flag and flag.paused)

    async def set_paused(self, paused: bool, by_user_id: int) -> None:
        async with self._sessions() as session:
            flag = await session.get(TradingPauseFlag, 1)
            if flag is None:
                flag = TradingPauseFlag(id=1)
                session.add(flag)
            flag.paused = paused
            flag.paused_by = by_user_id
            flag.paused_at = datetime.now(UTC)
            await session.commit()

    # -- orders --------------------------------------------------------------

    async def execute_market_order(
        self,
        *,
        telegram_user_id: int,
        telegram_update_id: int,
        token_id: str,
        market_title: str,
        side: str,
        amount: Decimal,
    ) -> OrderResult:
        """Place a FOK market order. `amount` is USDC for BUY, shares for SELL."""
        if await self.is_paused():
            return OrderResult(status="paused", side=side)

        # Idempotency barrier: record the attempt before touching the CLOB.
        order = Order(
            telegram_user_id=telegram_user_id,
            telegram_update_id=telegram_update_id,
            idempotency_key=uuid.uuid4().hex,
            token_id=token_id,
            market_title=market_title[:200],
            side=side,
            amount=amount,
        )
        async with self._sessions() as session:
            session.add(order)
            try:
                await session.commit()
            except IntegrityError:
                return OrderResult(status="duplicate", side=side)
            order_id = order.id

        slippage_bps = await self._slippage_bps(telegram_user_id)
        try:
            result = await self._place(telegram_user_id, token_id, side, amount, slippage_bps)
        except Exception as error:  # never leave a pending row lying
            await self._finalize(order_id, status="failed", error=str(error)[:200])
            return OrderResult(status="failed", side=side, error=str(error)[:200])

        await self._finalize(
            order_id,
            status=result.status,
            clob_order_id=getattr(result, "clob_order_id", None),
            fill_price=result.fill_price,
            fill_shares=result.fill_shares,
            fill_usdc=result.fill_usdc,
            fee=result.fee,
            error=result.error,
        )
        return result

    async def _place(
        self,
        telegram_user_id: int,
        token_id: str,
        side: str,
        amount: Decimal,
        slippage_bps: int,
    ) -> OrderResult:
        slip = Decimal(slippage_bps) / Decimal(10_000)
        async with self._wallets.secure_client(telegram_user_id) as client:
            if side == "BUY":
                estimate = await client.estimate_market_price(
                    token_id=token_id, side="BUY", amount=amount, order_type="FOK"
                )
                guard = min(
                    (estimate * (1 + slip)).quantize(CENT, rounding=ROUND_UP), Decimal("0.99")
                )
                response = await client.place_market_order(
                    token_id=token_id,
                    side="BUY",
                    amount=amount,
                    max_price=guard,
                    order_type="FOK",
                    builder_code=self._settings.builder_code,
                )
            else:
                estimate = await client.estimate_market_price(
                    token_id=token_id, side="SELL", shares=amount, order_type="FOK"
                )
                guard = max((estimate * (1 - slip)).quantize(CENT, rounding=ROUND_DOWN), CENT)
                response = await client.place_market_order(
                    token_id=token_id,
                    side="SELL",
                    shares=amount,
                    min_price=guard,
                    order_type="FOK",
                    builder_code=self._settings.builder_code,
                )

            if not response.ok:
                return OrderResult(status="rejected", side=side, error=response.message[:200])

            if side == "BUY":
                usdc, shares = response.making_amount, response.taking_amount
            else:
                usdc, shares = response.taking_amount, response.making_amount
            price = (usdc / shares).quantize(Decimal("0.0001")) if shares else Decimal(0)
            fee = await self._builder_fee(client, usdc)
            balance = await client.get_balance_allowance(asset_type="COLLATERAL")

        return OrderResult(
            status="filled",
            side=side,
            fill_price=price,
            fill_shares=shares,
            fill_usdc=usdc,
            fee=fee,
            new_balance=Decimal(balance.balance) / PUSD_DECIMALS,
        )

    async def _builder_fee(self, client, fill_usdc: Decimal) -> Decimal:
        try:
            rates = await client.get_builder_fee_rates(self._settings.builder_code)
            return (fill_usdc * Decimal(rates.taker)).quantize(CENT)
        except Exception:
            return Decimal("0")

    async def _slippage_bps(self, telegram_user_id: int) -> int:
        async with self._sessions() as session:
            user = await session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
        return user.slippage_bps if user else 200

    async def _finalize(self, order_id: int, *, status: str, **fields) -> None:
        async with self._sessions() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return
            order.status = status
            for name, value in fields.items():
                if value is not None and hasattr(order, name):
                    setattr(order, name, value)
            await session.commit()

    # -- positions -----------------------------------------------------------

    async def list_positions(self, wallet_address: str) -> list[PositionView]:
        async with AsyncPublicClient() as client:
            page = await client.list_positions(user=wallet_address).first_page()
        views = []
        for pos in page.items:
            if pos.token_id is None or not pos.size:
                continue
            views.append(
                PositionView(
                    token_id=str(pos.token_id),
                    title=pos.title or "(unknown market)",
                    outcome=pos.outcome or "?",
                    shares=pos.size,
                    avg_price=pos.avg_price,
                    cur_price=pos.cur_price,
                    cash_pnl=pos.cash_pnl,
                    percent_pnl=pos.percent_pnl,
                )
            )
        return views
