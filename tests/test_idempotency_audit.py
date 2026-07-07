"""Idempotency audit (PRD §6, CLAUDE.md): replayed webhook updates, double
button taps, and retried submissions each produce exactly ONE order.

All three scenarios run against the REAL TradingService (fake CLOB), so the
production idempotency barrier — the unique telegram_update_id row inserted
before the CLOB call — is what's being proven.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import clan, trade
from fade.db.models import Order
from fade.trading.markets import MarketCard

USER_ID = 960
CHAT_ID = -100960

MARKET = MarketCard(
    market_id="1",
    question="Will it rain tomorrow?",
    slug="rain",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.60"),
    no_price=Decimal("0.40"),
    end_date=None,
    open=True,
)


def make_context(settings, session_factory, trading) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
    }
    return context


def callback_update(data: str, update_id: int, chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = chat_type
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = USER_ID
    update.effective_user.username = "tapper"
    update.effective_user.first_name = "Tapper"
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


async def count_orders(session_factory) -> int:
    async with session_factory() as session:
        return len((await session.scalars(select(Order))).all())


async def test_replayed_webhook_update_executes_exactly_one_order(
    settings, session_factory, onboard, real_trading
):
    """A Telegram webhook retry re-delivers the exact same update object."""
    trading, clob = real_trading
    await onboard(USER_ID)
    context = make_context(settings, session_factory, trading)
    key = trade.cache_card(context, MARKET)

    original = callback_update(f"tc:{key}:y:10", update_id=9600)
    replay = callback_update(f"tc:{key}:y:10", update_id=9600)  # same update_id

    await trade.confirm_callback(original, context)
    await trade.confirm_callback(replay, context)

    assert len(clob.placed) == 1
    assert await count_orders(session_factory) == 1


async def test_double_tap_on_same_callback_executes_exactly_one_order(
    settings, session_factory, onboard, real_trading
):
    """Two frantic taps on the same confirm button = same callback delivery."""
    trading, clob = real_trading
    await onboard(USER_ID)
    context = make_context(settings, session_factory, trading)
    key = trade.cache_card(context, MARKET)

    tap = callback_update(f"tc:{key}:y:25", update_id=9700)
    await trade.confirm_callback(tap, context)
    await trade.confirm_callback(tap, context)  # second tap, same update

    assert len(clob.placed) == 1
    assert await count_orders(session_factory) == 1


async def test_clan_confirm_double_tap_executes_exactly_one_order(
    settings, session_factory, onboard, real_trading
):
    trading, clob = real_trading
    await onboard(USER_ID)
    context = make_context(settings, session_factory, trading)
    card = clan.FadeCard(
        chat_id=CHAT_ID,
        orig_user_id=999,
        orig_username="priya",
        question=MARKET.question,
        side_label="YES",
        yes_token="111",
        no_token="222",
        message_id=7,
    )
    key = clan.cache_fade_card(context, card)

    tap = callback_update(f"gc:{key}:fd:{USER_ID}:10", update_id=9800, chat_type="supergroup")
    await clan.confirm_callback(tap, context)
    await clan.confirm_callback(tap, context)

    assert len(clob.placed) == 1
    assert await count_orders(session_factory) == 1


async def test_retried_submission_after_result_executes_exactly_one_order(
    settings, session_factory, onboard, real_trading
):
    """A caller retrying the service call itself (e.g. after a timeout on the
    response, not the execution) cannot double-submit."""
    trading, clob = real_trading
    await onboard(USER_ID)

    first = await trading.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=9900,
        token_id="111",
        market_title=MARKET.question,
        side="BUY",
        amount=Decimal("10"),
    )
    retry = await trading.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=9900,
        token_id="111",
        market_title=MARKET.question,
        side="BUY",
        amount=Decimal("10"),
    )

    assert first.status == "filled"
    assert retry.status == "duplicate"
    assert len(clob.placed) == 1
    assert await count_orders(session_factory) == 1
