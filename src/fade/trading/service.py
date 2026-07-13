"""Order execution (PRD F-10, F-11, F-13) with idempotency (PRD §6).

Every order:
- is FOK with a worst-price limit derived from the user's slippage setting,
- carries the builder code from settings,
- is recorded BEFORE hitting the CLOB under a unique telegram_update_id, so a
  Telegram webhook retry (same update_id) can never execute twice.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from polymarket import AsyncPublicClient
from polymarket.errors import UnexpectedResponseError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fade.config import Settings
from fade.db.models import Order, TradingPauseFlag, User
from fade.trading.markets import MarketCard, MarketLookupError, market_to_card
from fade.wallets import PUSD_DECIMALS, WalletService

CENT = Decimal("0.01")

# Pagination bound (Stage 14): the data API pages positions; iterating ALL
# pages fixes silent truncation for heavy traders, and the bound stops a
# pathological account (or an API cursor loop) from spinning a scan forever.
# 10 pages ≈ 1000 positions — far beyond any real wallet here.
MAX_POSITION_PAGES = 10

logger = logging.getLogger(__name__)

VOLUME_CAP_TEXT = (
    "Daily trade limit reached: ${cap} of buys per 24 hours. "
    "You can still sell positions and withdraw. The limit resets on its own."
)

# Anomaly alert (PRD §7.7): flag a user whose last-24h volume blows past their
# own recent baseline. Absolute floor stops new-user noise. See D-12.
ANOMALY_MULTIPLIER = Decimal("5")
ANOMALY_FLOOR_USD = Decimal("200")
ANOMALY_ALERT_TEXT = (
    "⚠️ Volume anomaly: user {user_id} traded ${recent} in the last 24h "
    "(baseline ${baseline}/day over the prior week). Worth a look: /pause "
    "stops trading if something is wrong."
)


@dataclass(frozen=True)
class OrderResult:
    status: str  # filled | rejected | duplicate | paused | capped | failed
    side: str = ""
    fill_price: Decimal | None = None
    fill_shares: Decimal | None = None
    fill_usdc: Decimal | None = None
    fee: Decimal | None = None  # builder fee
    # Polymarket's platform fee, estimated from the market's fee params
    # (F-13/D-10); None = could not estimate (renders as unavailable, not $0).
    platform_fee: Decimal | None = None
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
    # Telegram user the card was issued to; 0 = unstamped (legacy cached cards).
    # The /positions handler stamps it so sell taps can be owner-locked.
    owner_id: int = 0


class TradingService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        wallets: WalletService,
        now: Callable[[], datetime] | None = None,
        alert: Callable[[str], Awaitable[None]] | None = None,
        public_client_factory: Callable[[], AsyncPublicClient] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._wallets = wallets
        self._now = now or (lambda: datetime.now(UTC))
        self._alert = alert  # async sink for admin anomaly alerts (optional)
        self._alerted_users: set[int] = set()  # currently-flagged incidents
        # Injectable so pagination is testable without a live network.
        self._public = public_client_factory or AsyncPublicClient

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

    async def list_by_category(self, category: str, limit: int = 5) -> list[MarketCard]:
        """Top open markets in one Polymarket category (F-9 category browsing).
        Same volume-ordered fetch as list_trending, filtered by category —
        no parallel data path."""
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
            if card.open and (card.category or "").lower() == category.lower():
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

        # Daily volume ceiling (PRD §7.7, D-12): BUYS only — selling out of a
        # position must always remain possible, like withdrawals. Checked
        # before the CLOB and before the idempotency row (nothing happened).
        cap = self._settings.daily_volume_cap_usd
        if side == "BUY" and cap > 0:
            recent = await self._volume_last_24h(telegram_user_id)
            if recent + amount > cap:
                return OrderResult(
                    status="capped", side=side, error=VOLUME_CAP_TEXT.format(cap=cap)
                )

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
        if result.status == "filled":
            try:
                await self._check_anomaly(telegram_user_id)
            except Exception:  # an alerting failure must never break trading
                logger.exception("anomaly check failed for user %s", telegram_user_id)
        return result

    # -- safety rails (PRD §7.7, D-12) ----------------------------------------

    async def _volume_last_24h(self, telegram_user_id: int) -> Decimal:
        since = (self._now() - timedelta(hours=24)).astimezone(UTC).replace(tzinfo=None)
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(Order.fill_usdc), 0)).where(
                    Order.telegram_user_id == telegram_user_id,
                    Order.status == "filled",
                    Order.created_at >= since,
                )
            )
        return Decimal(str(total))

    async def _check_anomaly(self, telegram_user_id: int) -> None:
        """DM the admin once per incident when a user's 24h volume blows past
        their own trailing baseline (5x prior-week daily average, $200 floor)."""
        if self._alert is None:
            return
        recent = await self._volume_last_24h(telegram_user_id)
        now_naive = self._now().astimezone(UTC).replace(tzinfo=None)
        week_ago = now_naive - timedelta(days=8)
        day_ago = now_naive - timedelta(hours=24)
        async with self._sessions() as session:
            prior = await session.scalar(
                select(func.coalesce(func.sum(Order.fill_usdc), 0)).where(
                    Order.telegram_user_id == telegram_user_id,
                    Order.status == "filled",
                    Order.created_at >= week_ago,
                    Order.created_at < day_ago,
                )
            )
        baseline = Decimal(str(prior)) / Decimal(7)
        threshold = max(baseline * ANOMALY_MULTIPLIER, ANOMALY_FLOOR_USD)
        if recent > threshold:
            if telegram_user_id not in self._alerted_users:
                self._alerted_users.add(telegram_user_id)
                await self._alert(
                    ANOMALY_ALERT_TEXT.format(
                        user_id=telegram_user_id,
                        recent=f"{recent:.2f}",
                        baseline=f"{baseline:.2f}",
                    )
                )
        else:
            # Incident over: re-arm so a future spike alerts again.
            self._alerted_users.discard(telegram_user_id)

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
            platform_fee = await self._platform_fee(client, token_id, price, shares)
            balance = await client.get_balance_allowance(asset_type="COLLATERAL")

        return OrderResult(
            status="filled",
            side=side,
            fill_price=price,
            fill_shares=shares,
            fill_usdc=usdc,
            fee=fee,
            platform_fee=platform_fee,
            new_balance=Decimal(balance.balance) / PUSD_DECIMALS,
        )

    async def _builder_fee(self, client, fill_usdc: Decimal) -> Decimal:
        try:
            rates = await client.get_builder_fee_rates(self._settings.builder_code)
            return (fill_usdc * Decimal(rates.taker)).quantize(CENT)
        except Exception:
            return Decimal("0")

    async def _platform_fee(
        self, client, token_id: str, price: Decimal, shares: Decimal
    ) -> Decimal | None:
        """Estimated Polymarket platform fee for a fill (PRD F-13, D-10).

        The fill response carries no fee figures, so this is computed from the
        market's fee params — rate × (p(1-p))^exponent per share, the exact
        formula the SDK uses to size orders (docs/SPIKE-NOTES.md §4). None
        means "couldn't estimate": the confirmation renders that honestly
        instead of showing a false $0.
        """
        if price <= 0 or shares is None:
            return None
        try:
            info = await self._platform_fee_info(client, token_id)
            if info is None:
                return None
            effective = info.rate * ((price * (Decimal(1) - price)) ** info.exponent)
            return (shares * effective).quantize(CENT)
        except Exception:
            logger.warning("platform fee estimate failed for token %s", token_id, exc_info=True)
            return None

    async def _platform_fee_info(self, client, token_id: str):
        getter = getattr(client, "get_platform_fee_info", None)
        if getter is not None:
            return await getter(token_id=token_id)
        # The beta SDK computes platform fees internally but has no public
        # accessor yet; reach its documented endpoint through the internal
        # actions, degrading to None ("estimate unavailable") if that seam
        # moves in a future SDK release.
        ctx = getattr(client, "_ctx", None)
        if ctx is None:
            return None
        from polymarket._internal.actions.orders.market_data import (
            fetch_platform_fee_info,
            resolve_condition_by_token,
        )

        condition_id = await resolve_condition_by_token(ctx, token_id=token_id)
        return await fetch_platform_fee_info(ctx, condition_id=condition_id)

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
        views: list[PositionView] = []
        async with self._public() as client:
            pages = 0
            async for page in client.list_positions(user=wallet_address):
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
                pages += 1
                if pages >= MAX_POSITION_PAGES:
                    break
        return views
