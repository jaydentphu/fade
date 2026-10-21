"""The copy/fade loop end to end (PRD F-17, F-18, F-19) plus group-safety checks.

Simulates: Priya trades in DM -> card broadcast to her clan -> Marcus taps
Fade/Copy -> size picker -> confirm -> execution -> rivalry reply.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import clan, trade
from fade.db.models import User
from fade.social.broadcast import BroadcastQueue
from fade.trading.markets import MarketCard
from fade.trading.service import OrderResult

CHAT_ID = -100888
PRIYA, MARCUS = 901, 902

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
    """Context with a REAL broadcast queue draining into a captured list."""
    sent: list[dict] = []

    async def send(**kwargs):
        sent.append(kwargs)
        return type("Msg", (), {"message_id": len(sent)})()

    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    trading = MagicMock()
    trading.execute_market_order = AsyncMock(return_value=FILLED)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
        "broadcasts": BroadcastQueue(send=send),
    }
    return context, sent


def dm_callback(data: str, user_id: int, username: str, update_id: int = 8000) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = username.title()
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


def group_callback(data: str, user_id: int, username: str, update_id: int = 8100) -> MagicMock:
    update = dm_callback(data, user_id, username, update_id)
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = CHAT_ID
    return update


async def seed_clan_with_members(context, session_factory, onboard) -> None:
    await onboard(PRIYA)
    await onboard(MARCUS)
    sessions = session_factory
    async with sessions() as session:
        from fade.db.models import GroupChat, GroupMember

        session.add(GroupChat(chat_id=CHAT_ID, title="Degens", created_by=PRIYA))
        session.add(GroupMember(chat_id=CHAT_ID, telegram_user_id=PRIYA))
        session.add(GroupMember(chat_id=CHAT_ID, telegram_user_id=MARCUS))
        await session.commit()


async def priya_trades(context, update_id: int = 8000) -> None:
    """Priya confirms a YES buy in DM, which triggers the broadcast."""
    key = await trade.cache_card(context, MARKET)
    update = dm_callback(f"tc:{key}:y:25", PRIYA, "priya", update_id)
    await trade.confirm_callback(update, context)


async def broadcast_card(context, sent) -> tuple[str, str]:
    """Drain the queue; return (card key, card text) of the broadcast card."""
    await context.application.bot_data["broadcasts"].drain_once()
    card_msg = sent[-1]
    markup = card_msg["reply_markup"]
    key = markup.inline_keyboard[0][0].callback_data.split(":")[1]
    return key, card_msg["text"]


async def test_member_trade_broadcasts_card_with_fade_and_copy_buttons(
    settings, session_factory, onboard
):
    """PRD F-17/F-18: a member's trade posts a card with ⚔️ Fade and 📋 Copy."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)

    await priya_trades(context)
    await context.application.bot_data["broadcasts"].drain_once()

    assert len(sent) == 1
    card = sent[0]
    assert card["chat_id"] == CHAT_ID
    assert "priya" in card["text"]
    assert "bought YES" in card["text"]
    assert "$25" in card["text"]
    assert "Fed cut rates" in card["text"]
    assert "0.61" in card["text"]  # entry price
    labels = [b.text for row in card["reply_markup"].inline_keyboard for b in row]
    assert labels == ["⚔️ Fade", "📋 Copy"]


async def test_privacy_toggle_hides_amounts_but_keeps_side_and_market(
    settings, session_factory, onboard
):
    """PRD F-17: hidden amounts still show side + market."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == PRIYA))
        user.hide_amounts = True
        await session.commit()

    await priya_trades(context)
    await context.application.bot_data["broadcasts"].drain_once()

    text = sent[0]["text"]
    assert "$25" not in text  # amount hidden
    assert "$" not in text.split("Entry")[0]  # no dollar figure in the action line
    assert "bought YES" in text  # side still shown
    assert "Fed cut rates" in text  # market still shown


async def test_fade_opens_size_picker_then_executes_opposite_side(
    settings, session_factory, onboard
):
    """PRD F-18: Fade -> size picker -> confirm -> OPPOSITE side of same market."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, _ = await broadcast_card(context, sent)

    # Marcus taps ⚔️ Fade: a size picker appears addressed to him.
    tap = group_callback(f"fd:{key}", MARCUS, "marcus")
    await clan.fade_copy_callback(tap, context)
    picker_call = tap.callback_query.message.reply_text.call_args
    assert "marcus" in picker_call.args[0] and "fade" in picker_call.args[0]
    picker_markup = picker_call.kwargs["reply_markup"]
    size_data = picker_markup.inline_keyboard[0][1].callback_data  # $25
    assert size_data == f"sf:{key}:{MARCUS}:25"

    # He picks $25: the confirm step appears.
    pick = group_callback(size_data, MARCUS, "marcus", update_id=8200)
    await clan.size_callback(pick, context)
    confirm_markup = pick.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    confirm_data = confirm_markup.inline_keyboard[0][0].callback_data

    # He confirms: the order executes on the OPPOSITE token (Priya bought YES).
    confirm = group_callback(confirm_data, MARCUS, "marcus", update_id=8300)
    await clan.confirm_callback(confirm, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["token_id"] == MARKET.no_token  # opposite of YES
    assert call.kwargs["side"] == "BUY"
    assert call.kwargs["amount"] == Decimal("25")
    assert call.kwargs["telegram_user_id"] == MARCUS
    assert call.kwargs["telegram_update_id"] == 8300


async def test_copy_executes_same_side(settings, session_factory, onboard):
    """PRD F-18: Copy buys the SAME side the original trader bought."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, _ = await broadcast_card(context, sent)

    tap = group_callback(f"cp:{key}", MARCUS, "marcus")
    await clan.fade_copy_callback(tap, context)
    picker_markup = tap.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
    size_data = picker_markup.inline_keyboard[0][0].callback_data  # $10
    assert size_data.startswith("sc:")

    pick = group_callback(size_data, MARCUS, "marcus", update_id=8400)
    await clan.size_callback(pick, context)
    confirm_data = (
        pick.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data
    )

    confirm = group_callback(confirm_data, MARCUS, "marcus", update_id=8500)
    await clan.confirm_callback(confirm, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["token_id"] == MARKET.yes_token  # same side as Priya
    assert call.kwargs["amount"] == Decimal("10")


async def test_fade_execution_posts_reply_naming_both_users(settings, session_factory, onboard):
    """PRD F-19: the execution posts a reply to the original card naming both."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, _ = await broadcast_card(context, sent)
    card_message_id = 1  # first message sent by the fake sender

    tap = group_callback(f"fd:{key}", MARCUS, "marcus")
    await clan.fade_copy_callback(tap, context)
    confirm = group_callback(f"gc:{key}:fd:{MARCUS}:25", MARCUS, "marcus", update_id=8600)
    await clan.confirm_callback(confirm, context)

    # The picker message is edited into the rivalry line...
    edited = confirm.callback_query.edit_message_text.call_args.args[0]
    assert "marcus" in edited and "priya" in edited and "faded" in edited

    # ...and the same line is queued as a reply to the original broadcast card.
    await context.application.bot_data["broadcasts"].drain_once()
    reply = sent[-1]
    assert reply["reply_to_message_id"] == card_message_id
    assert "marcus" in reply["text"] and "priya" in reply["text"]
    assert "$25" in reply["text"]


async def test_sell_broadcasts_without_fade_copy_buttons(settings, session_factory, onboard):
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)

    await clan.broadcast_fill(
        context,
        telegram_user_id=PRIYA,
        username="priya",
        question=MARKET.question,
        side_label="YES",
        yes_token="",
        no_token="",
        amount_usdc=Decimal("15"),
        price=Decimal("0.70"),
        is_sell=True,
    )
    await context.application.bot_data["broadcasts"].drain_once()

    assert len(sent) == 1
    assert "sold" in sent[0]["text"]
    assert sent[0]["reply_markup"] is None


async def test_trade_without_clan_membership_broadcasts_nothing(settings, session_factory, onboard):
    context, sent = make_context(settings, session_factory)
    await onboard(PRIYA)  # onboarded but in no clan

    await priya_trades(context)
    await context.application.bot_data["broadcasts"].drain_once()

    assert sent == []


async def test_non_onboarded_tapper_is_prompted_to_create_account(
    settings, session_factory, onboard
):
    """D-5: no account -> told to /start; no picker, no order."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, _ = await broadcast_card(context, sent)

    stranger = group_callback(f"fd:{key}", 999, "lurker")
    await clan.fade_copy_callback(stranger, context)

    stranger.callback_query.answer.assert_awaited_once_with(
        clan.NEED_ACCOUNT_ALERT, show_alert=True
    )
    stranger.callback_query.message.reply_text.assert_not_awaited()


async def test_wrong_user_cannot_use_someone_elses_picker(settings, session_factory, onboard):
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, _ = await broadcast_card(context, sent)

    hijack = group_callback(f"sf:{key}:{MARCUS}:25", PRIYA, "priya")
    await clan.size_callback(hijack, context)
    hijack.callback_query.answer.assert_awaited_once_with(
        clan.NOT_YOUR_PICKER_ALERT, show_alert=True
    )

    # Reset the mock (it recorded Priya's own DM trade during setup) so the
    # assertion isolates the hijack attempt.
    context.application.bot_data["trading"].execute_market_order.reset_mock()
    hijack2 = group_callback(f"gc:{key}:fd:{MARCUS}:25", PRIYA, "priya")
    await clan.confirm_callback(hijack2, context)
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()


async def test_group_messages_never_contain_wallet_or_balance_info(
    settings, session_factory, onboard
):
    """Negative test (PRD F-7): nothing group-rendered leaks balances/addresses."""
    context, sent = make_context(settings, session_factory)
    await seed_clan_with_members(context, session_factory, onboard)
    await priya_trades(context)
    key, card_text = await broadcast_card(context, sent)

    group_texts = [card_text]

    tap = group_callback(f"fd:{key}", MARCUS, "marcus")
    await clan.fade_copy_callback(tap, context)
    group_texts.append(tap.callback_query.message.reply_text.call_args.args[0])

    pick = group_callback(f"sf:{key}:{MARCUS}:25", MARCUS, "marcus", update_id=8700)
    await clan.size_callback(pick, context)
    group_texts.append(pick.callback_query.edit_message_text.call_args.args[0])

    confirm = group_callback(f"gc:{key}:fd:{MARCUS}:25", MARCUS, "marcus", update_id=8800)
    await clan.confirm_callback(confirm, context)
    group_texts.append(confirm.callback_query.edit_message_text.call_args.args[0])

    await context.application.bot_data["broadcasts"].drain_once()
    group_texts.extend(m["text"] for m in sent)

    for text in group_texts:
        lowered = text.lower()
        assert "balance" not in lowered, text
        assert "0x" not in lowered, text  # no addresses of any kind
        assert "wallet" not in lowered, text
