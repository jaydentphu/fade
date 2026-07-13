"""Stage 14: social + discovery polish.

Challenge robustness (text_mention opponents, challenger withdraw), category
browsing (F-9 as written), and full pagination for positions/resolutions.
No live network anywhere.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import challenge as ch
from fade.bot.handlers import discover
from fade.bot.handlers.resolutions import fetch_resolved_positions
from fade.db.models import Challenge
from fade.trading.markets import MarketCard
from fade.trading.service import MAX_POSITION_PAGES, OrderResult, TradingService

CHAT_ID = -100555
PRIYA, MARCUS, LURKER = 901, 902, 999
URL = "https://polymarket.com/event/fed-cuts"

MARKET = MarketCard(
    market_id="1",
    question="Will the Fed cut rates in September?",
    slug="fed-cuts",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.61"),
    no_price=Decimal("0.39"),
    end_date="2026-09-30",
    open=True,
)

FILLED = OrderResult(
    status="filled",
    side="BUY",
    fill_price=Decimal("0.61"),
    fill_shares=Decimal("40.9"),
    fill_usdc=Decimal("25"),
    fee=Decimal("0.25"),
    new_balance=Decimal("75"),
)


def make_context(settings, session_factory):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    trading = MagicMock()
    trading.execute_market_order = AsyncMock(return_value=FILLED)
    trading.get_market_card = AsyncMock(return_value=MARKET)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
    }
    return context


def group_message(user_id: int, username: str | None, entities=None) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = 9000
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = "Someone"
    update.message.reply_text = AsyncMock()
    update.message.entities = entities or []
    return update


def group_callback(data: str, user_id: int, username: str | None, update_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = "Someone"
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


# -- (1a) text_mention opponents (no username) ------------------------------------


async def test_no_username_opponent_can_be_challenged_and_accept(
    settings, session_factory, onboard
):
    await onboard(PRIYA)
    await onboard(MARCUS)
    context = make_context(settings, session_factory)

    # Marcus has NO username: he arrives as a text_mention entity with his id.
    mention = SimpleNamespace(
        type="text_mention", user=SimpleNamespace(id=MARCUS, first_name="Marcus")
    )
    cmd = group_message(PRIYA, "priya", entities=[mention])
    context.args = [URL]  # the mention text isn't in args; the URL is
    await ch.challenge(cmd, context)

    markup = cmd.message.reply_text.call_args.kwargs["reply_markup"]
    key = markup.inline_keyboard[0][0].callback_data.split(":")[1]
    pick = group_callback(f"hp:{key}:{PRIYA}:y:25", PRIYA, "priya", 9100)
    await ch.pick_callback(pick, context)

    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row.opponent_id == MARCUS  # real id captured at propose time
    assert row.status == "pending"

    # A third user (also without a username) is still locked out — by id.
    intruder = group_callback(f"ha:{row.id}", LURKER, None, 9200)
    await ch.accept_callback(intruder, context)
    intruder.callback_query.answer.assert_awaited_once_with(
        ch.NOT_YOUR_CHALLENGE_ALERT, show_alert=True
    )

    # Marcus accepts with no username: the id lock lets him through.
    accept = group_callback(f"ha:{row.id}", MARCUS, None, 9300)
    await ch.accept_callback(accept, context)

    calls = context.application.bot_data["trading"].execute_market_order.call_args_list
    assert len(calls) == 2  # both legs executed
    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    assert row.status == "accepted"
    assert row.opponent_id == MARCUS


# -- (1b) challenger withdraw of a pending challenge --------------------------------


async def propose_username_challenge(context, session_factory):
    cmd = group_message(PRIYA, "priya")
    context.args = ["@marcus", URL]
    await ch.challenge(cmd, context)
    markup = cmd.message.reply_text.call_args.kwargs["reply_markup"]
    key = markup.inline_keyboard[0][0].callback_data.split(":")[1]
    pick = group_callback(f"hp:{key}:{PRIYA}:y:25", PRIYA, "priya", 9100)
    await ch.pick_callback(pick, context)
    async with session_factory() as session:
        row = await session.scalar(select(Challenge))
    return row.id, pick


async def test_challenger_can_withdraw_pending_challenge(settings, session_factory, onboard):
    await onboard(PRIYA)
    context = make_context(settings, session_factory)
    challenge_id, pick = await propose_username_challenge(context, session_factory)

    # The accept prompt carries the challenger's Withdraw button.
    markup = pick.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    all_data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"hc:{challenge_id}" in all_data

    withdraw = group_callback(f"hc:{challenge_id}", PRIYA, "priya", 9400)
    await ch.withdraw_callback(withdraw, context)

    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
    assert row.status == "cancelled"
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()
    assert "No money moved" in withdraw.callback_query.edit_message_text.call_args.args[0]


async def test_third_user_cannot_withdraw_someone_elses_challenge(
    settings, session_factory, onboard
):
    await onboard(PRIYA)
    await onboard(LURKER)
    context = make_context(settings, session_factory)
    challenge_id, _ = await propose_username_challenge(context, session_factory)

    intruder = group_callback(f"hc:{challenge_id}", LURKER, "lurker", 9500)
    await ch.withdraw_callback(intruder, context)

    intruder.callback_query.answer.assert_awaited_once_with(
        ch.NOT_YOUR_WITHDRAW_ALERT, show_alert=True
    )
    async with session_factory() as session:
        row = await session.get(Challenge, challenge_id)
    assert row.status == "pending"  # untouched


# -- (2) category browsing (PRD F-9) -------------------------------------------------


def card_in(category: str, question: str, market_id: str) -> MarketCard:
    return MarketCard(
        market_id=market_id,
        question=question,
        slug=question.lower().replace(" ", "-")[:20],
        yes_token="111",
        no_token="222",
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        end_date=None,
        open=True,
        category=category,
    )


async def test_trending_offers_category_buttons_and_tap_lists_that_category():
    context = MagicMock()
    trading = MagicMock()
    trending_cards = [
        card_in("Politics", "Will the Fed cut rates?", "1"),
        card_in("Sports", "Will the Lakers win?", "2"),
    ]
    trading.list_trending = AsyncMock(return_value=trending_cards)
    trading.list_by_category = AsyncMock(
        return_value=[card_in("Sports", "Will the Lakers win?", "2")]
    )
    context.application.bot_data = {"trading": trading}

    update = MagicMock(spec=Update)
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    await discover.trending(update, context)

    markup = progress.edit_text.call_args.kwargs["reply_markup"]
    all_buttons = [b for row in markup.inline_keyboard for b in row]
    category_buttons = [b for b in all_buttons if b.callback_data.startswith("c:")]
    assert {b.callback_data for b in category_buttons} == {"c:Politics", "c:Sports"}

    # Tap "Sports": rendered through the SAME card path as /search results.
    tap = MagicMock(spec=Update)
    tap.callback_query = MagicMock()
    tap.callback_query.data = "c:Sports"
    tap.callback_query.answer = AsyncMock()
    tap.callback_query.edit_message_text = AsyncMock()
    await discover.category_callback(tap, context)

    trading.list_by_category.assert_awaited_once_with("Sports")
    text = tap.callback_query.edit_message_text.call_args.args[0]
    assert "Sports" in text
    markup = tap.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    result_buttons = [b for row in markup.inline_keyboard for b in row]
    assert all(b.callback_data.startswith("v:") for b in result_buttons)  # discovery cards
    assert "Will the Lakers win?" in result_buttons[0].text


# -- (3) pagination -------------------------------------------------------------------


def position_item(token_id: str, redeemable: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        token_id=token_id,
        title=f"Market {token_id}",
        outcome="Yes",
        size=Decimal("10"),
        avg_price=Decimal("0.5"),
        cur_price=Decimal("0.7"),
        cash_pnl=None,
        percent_pnl=None,
        redeemable=redeemable,
    )


class FakePaginator:
    def __init__(self, pages: list[list[SimpleNamespace]]) -> None:
        self._pages = pages

    async def first_page(self):
        return SimpleNamespace(items=self._pages[0])

    def __aiter__(self):
        async def gen():
            for items in self._pages:
                yield SimpleNamespace(items=items)

        return gen()


class FakePublicClient:
    def __init__(self, pages: list[list[SimpleNamespace]]) -> None:
        self._pages = pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def list_positions(self, user: str):
        return FakePaginator(self._pages)


async def test_list_positions_includes_items_from_page_two(settings, session_factory):
    pages = [[position_item("page1-token")], [position_item("page2-token")]]
    service = TradingService(
        settings,
        session_factory,
        MagicMock(),
        public_client_factory=lambda: FakePublicClient(pages),
    )

    views = await service.list_positions("0xFadE00000000000000000000000000000000aaaa")

    assert [v.token_id for v in views] == ["page1-token", "page2-token"]


async def test_list_positions_is_bounded_against_pathological_pagination(
    settings, session_factory
):
    pages = [[position_item(f"t{i}")] for i in range(MAX_POSITION_PAGES + 5)]
    service = TradingService(
        settings,
        session_factory,
        MagicMock(),
        public_client_factory=lambda: FakePublicClient(pages),
    )

    views = await service.list_positions("0xFadE00000000000000000000000000000000aaaa")

    assert len(views) == MAX_POSITION_PAGES  # one item per page, capped


async def test_fetch_resolved_positions_includes_items_from_page_two():
    pages = [
        [position_item("page1-token", redeemable=True)],
        [position_item("page2-token", redeemable=True)],
    ]

    resolved = await fetch_resolved_positions(
        "0xFadE00000000000000000000000000000000aaaa",
        client_factory=lambda: FakePublicClient(pages),
    )

    assert [r.token_id for r in resolved] == ["page1-token", "page2-token"]
