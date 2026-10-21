"""Trade cards inside the bot: buy broadcasts go out as photos (the Fade/Copy
loop still works on them), market cards are photos whose caption gets
edited through the trade flow, and every render failure degrades to text."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
from telegram import Update

from fade.bot.handlers import clan, trade
from fade.bot.handlers.common import edit_card
from fade.db.models import Clan, ClanMember
from fade.social import trade_card
from fade.social.broadcast import BroadcastQueue, PendingMessage, render_trade_card
from fade.trading.markets import MarketCard
from fade.trading.service import OrderResult

CHAT_ID = -100900
PRIYA, MARCUS = 921, 922

MARKET = MarketCard(
    market_id="1",
    question="Will the Fed cut rates in September?",
    slug="fed-cuts",
    yes_token="111",
    no_token="222",
    yes_price=Decimal("0.61"),
    no_price=Decimal("0.39"),
    end_date="2026-09-20",
    open=True,
)
FILLED = OrderResult(
    status="filled", side="BUY", fill_price=Decimal("0.61"), fill_shares=Decimal("40.9"),
    fill_usdc=Decimal("25"), fee=Decimal("0.25"), new_balance=Decimal("75"),
)


def photo_queue():
    """A queue that can send photos, capturing what it sends."""
    texts, photos = [], []

    async def send(**kwargs):
        texts.append(kwargs)
        return MagicMock(message_id=1000 + len(texts) + len(photos))

    async def send_photo(**kwargs):
        photos.append(kwargs)
        return MagicMock(message_id=1000 + len(texts) + len(photos))

    return BroadcastQueue(send=send, send_photo=send_photo), texts, photos


def make_context(settings, session_factory, queue):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    trading = MagicMock()
    trading.execute_market_order = AsyncMock(return_value=FILLED)
    trading.get_market_card = AsyncMock(return_value=MARKET)
    context.application.bot_data = {
        "settings": settings, "sessions": session_factory, "trading": trading,
        "broadcasts": queue,
    }
    return context


async def seed_clan(session_factory, onboard) -> None:
    await onboard(PRIYA)
    await onboard(MARCUS)
    async with session_factory() as session:
        session.add(Clan(chat_id=CHAT_ID, title="Degens", created_by=PRIYA))
        session.add(ClanMember(chat_id=CHAT_ID, telegram_user_id=PRIYA))
        session.add(ClanMember(chat_id=CHAT_ID, telegram_user_id=MARCUS))
        await session.commit()


def callback(data: str, user_id: int, username: str, update_id: int, photo=()) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = username.title()
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_caption = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.callback_query.message.photo = photo
    return update


# -- the queue -----------------------------------------------------------------------------


async def test_queue_sends_photo_messages_as_photos_with_caption():
    queue, texts, photos = photo_queue()
    seen = []
    queue.enqueue(
        PendingMessage(chat_id=CHAT_ID, text="caption", reply_markup="kb", photo=b"jpeg"),
        on_sent=seen.append,
    )

    await queue.drain_once()

    assert texts == []
    assert photos[0] == {"chat_id": CHAT_ID, "photo": b"jpeg", "caption": "caption",
                         "reply_markup": "kb", "reply_to_message_id": None}
    assert seen[0].message_id  # on_sent still gets the sent message (F-19 threading)


async def test_queue_without_photo_support_degrades_to_text():
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)
        return MagicMock()

    queue = BroadcastQueue(send=send)  # no send_photo
    queue.enqueue(PendingMessage(chat_id=CHAT_ID, text="caption", photo=b"jpeg"))

    await queue.drain_once()

    assert sent[0]["text"] == "caption"


# -- broadcast_fill ------------------------------------------------------------------------


async def test_buy_broadcast_is_a_card_photo_with_the_text_as_caption(
    settings, session_factory, onboard
):
    await seed_clan(session_factory, onboard)
    queue, texts, photos = photo_queue()
    context = make_context(settings, session_factory, queue)

    await clan.broadcast_fill(
        context, telegram_user_id=PRIYA, username="priya", question=MARKET.question,
        side_label="YES", yes_token="111", no_token="222",
        amount_usdc=Decimal("25"), price=Decimal("0.61"),
    )
    await queue.drain_once()

    assert texts == []
    sent = photos[0]
    assert Image.open(BytesIO(sent["photo"])).format == "JPEG"
    assert sent["caption"] == render_trade_card(
        who="priya", question=MARKET.question, side_label="YES",
        amount_usdc=Decimal("25"), price=Decimal("0.61"), hide_amounts=False,
    )
    labels = [b.text for row in sent["reply_markup"].inline_keyboard for b in row]
    assert labels == ["⚔️ Fade", "📋 Copy"]


async def test_hidden_amounts_reach_the_image_too(
    settings, session_factory, onboard, monkeypatch
):
    await seed_clan(session_factory, onboard)
    from sqlalchemy import select

    from fade.db.models import User

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == PRIYA))
        user.hide_amounts = True
        await session.commit()

    captured = {}
    real = trade_card.generate_broadcast_card

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(trade_card, "generate_broadcast_card", spy)
    queue, _, _ = photo_queue()
    await clan.broadcast_fill(
        make_context(settings, session_factory, queue), telegram_user_id=PRIYA,
        username="priya", question=MARKET.question, side_label="YES", yes_token="111",
        no_token="222", amount_usdc=Decimal("25"), price=Decimal("0.61"),
    )

    assert captured["hide_amounts"] is True  # F-17 privacy applies to pixels as well


async def test_sells_stay_text_and_render_failures_fall_back_to_text(
    settings, session_factory, onboard, monkeypatch
):
    await seed_clan(session_factory, onboard)
    queue, texts, photos = photo_queue()
    context = make_context(settings, session_factory, queue)

    await clan.broadcast_fill(
        context, telegram_user_id=PRIYA, username="priya", question=MARKET.question,
        side_label="YES", yes_token="", no_token="", amount_usdc=Decimal("15"),
        price=Decimal("0.70"), is_sell=True,
    )

    def boom(**kwargs):
        raise RuntimeError("renderer down")

    monkeypatch.setattr(trade_card, "generate_broadcast_card", boom)
    await clan.broadcast_fill(
        context, telegram_user_id=PRIYA, username="priya", question=MARKET.question,
        side_label="YES", yes_token="111", no_token="222", amount_usdc=Decimal("25"),
        price=Decimal("0.61"),
    )
    await queue.drain_once()

    assert photos == []  # neither went out as an image...
    assert len(texts) == 2  # ...but both broadcasts still reached the clan
    assert "sold" in texts[0]["text"]
    assert texts[1]["reply_markup"] is not None  # the buy kept its Fade/Copy buttons


async def test_fade_loop_works_end_to_end_on_a_photo_card(settings, session_factory, onboard):
    """Trade -> photo card -> Fade -> size -> confirm -> threaded reply."""
    await seed_clan(session_factory, onboard)
    queue, texts, photos = photo_queue()
    context = make_context(settings, session_factory, queue)

    await clan.broadcast_fill(
        context, telegram_user_id=PRIYA, username="priya", question=MARKET.question,
        side_label="YES", yes_token="111", no_token="222",
        amount_usdc=Decimal("25"), price=Decimal("0.61"),
    )
    await queue.drain_once()
    card_message_id = 1001  # first message the fake sender returned
    key = photos[0]["reply_markup"].inline_keyboard[0][0].callback_data.split(":")[1]

    tap = callback(f"fd:{key}", MARCUS, "marcus", 9100)
    await clan.fade_copy_callback(tap, context)
    confirm = callback(f"gc:{key}:fd:{MARCUS}:25", MARCUS, "marcus", 9200)
    await clan.confirm_callback(confirm, context)
    await queue.drain_once()

    order = context.application.bot_data["trading"].execute_market_order.call_args.kwargs
    assert order["token_id"] == "222"  # faded YES -> bought NO
    reply = texts[-1]
    assert reply["reply_to_message_id"] == card_message_id  # threads under the photo
    assert "marcus" in reply["text"] and "priya" in reply["text"]


# -- market card in DMs --------------------------------------------------------------------


async def test_edit_card_edits_captions_on_photos_and_text_otherwise():
    photo_query = MagicMock()
    photo_query.message.photo = (MagicMock(),)  # Telegram: tuple of PhotoSize
    photo_query.edit_message_caption = AsyncMock()
    await edit_card(photo_query, "confirm?", reply_markup="kb")
    photo_query.edit_message_caption.assert_awaited_once_with(caption="confirm?", reply_markup="kb")

    text_query = MagicMock()
    text_query.message.photo = ()
    text_query.edit_message_text = AsyncMock()
    await edit_card(text_query, "confirm?")
    text_query.edit_message_text.assert_awaited_once_with("confirm?", reply_markup=None)


async def test_trade_flow_on_a_photo_card_edits_the_caption(settings, session_factory, onboard):
    await onboard(PRIYA)
    queue, _, _ = photo_queue()
    context = make_context(settings, session_factory, queue)
    key = await trade.cache_card(context, MARKET)

    size = callback(f"t:{key}:y:25", PRIYA, "priya", 9300, photo=(MagicMock(),))
    await trade.size_callback(size, context)
    size.callback_query.edit_message_text.assert_not_awaited()
    assert "Confirm order" in size.callback_query.edit_message_caption.call_args.kwargs["caption"]

    confirm = callback(f"tc:{key}:y:25", PRIYA, "priya", 9400, photo=(MagicMock(),))
    await trade.confirm_callback(confirm, context)
    assert "Filled." in confirm.callback_query.edit_message_caption.call_args.kwargs["caption"]


async def test_market_card_render_failure_falls_back_to_the_text_card(
    settings, session_factory, onboard, monkeypatch
):
    await onboard(PRIYA)

    def boom(card):
        raise RuntimeError("renderer down")

    monkeypatch.setattr(trade_card, "generate_market_card", boom)
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = PRIYA
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    queue, _, _ = photo_queue()

    await trade.show_market_card(update, make_context(settings, session_factory, queue), "url")

    update.message.reply_photo.assert_not_awaited()
    text = update.message.reply_text.call_args.args[0]
    assert text == trade.render_card_text(MARKET)
    assert update.message.reply_text.call_args.kwargs["reply_markup"] is not None
