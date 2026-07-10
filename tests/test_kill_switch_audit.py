"""Kill-switch audit (PRD F-25, §7.3): with trading paused, EVERY order path
returns a paused message, and withdrawals still complete.

Uses a REAL TradingService (fake CLOB underneath) so the pause check exercised
is the production one, not a mock.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import clan, positions, trade, withdraw
from fade.bot.handlers.trade import PAUSED_TEXT
from fade.trading.markets import MarketCard
from fade.trading.service import PositionView

USER_ID = 950
CHAT_ID = -100950

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

VIEW = PositionView(
    token_id="111",
    title="Will it rain tomorrow?",
    outcome="Yes",
    shares=Decimal("10"),
    avg_price=Decimal("0.5"),
    cur_price=Decimal("0.6"),
    cash_pnl=Decimal("1"),
    percent_pnl=10.0,
)


def make_context(settings, session_factory, trading) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(
        return_value="0xFadE0000000000000000000000000000000000aa"
    )
    wallets.withdraw = AsyncMock(return_value="0x" + "ee" * 32)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "trading": trading,
        "wallets": wallets,
    }
    return context


def callback_update(data: str, update_id: int, chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = chat_type
    update.effective_chat.id = CHAT_ID
    update.effective_user.id = USER_ID
    update.effective_user.username = "pausee"
    update.effective_user.first_name = "Pausee"
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


def message_update(text: str = "") -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


async def test_paused_blocks_every_order_path_but_withdrawals_complete(
    settings, session_factory, onboard, real_trading
):
    """PRD F-25: pause -> DM buys, position sells, and clan fade/copy all show a
    paused message; a withdrawal still goes through end to end."""
    trading, clob = real_trading
    await onboard(USER_ID)
    await trading.set_paused(True, by_user_id=1)
    context = make_context(settings, session_factory, trading)

    # Path 1: DM buy confirm.
    key = await trade.cache_card(context, MARKET)
    buy = callback_update(f"tc:{key}:y:10", update_id=9100)
    await trade.confirm_callback(buy, context)
    assert buy.callback_query.edit_message_text.call_args.args[0] == PAUSED_TEXT

    # Path 2: position sell confirm.
    pos_key = await positions._cache_position(context, VIEW)
    sell = callback_update(f"psc:{pos_key}", update_id=9200)
    await positions.sell_confirm_callback(sell, context)
    assert sell.callback_query.edit_message_text.call_args.args[0] == PAUSED_TEXT

    # Path 3: clan fade/copy confirm.
    fade_card = clan.FadeCard(
        chat_id=CHAT_ID,
        orig_user_id=999,
        orig_username="priya",
        question=MARKET.question,
        side_label="YES",
        yes_token="111",
        no_token="222",
        message_id=5,
    )
    clan_key = await clan.cache_fade_card(context, fade_card)
    group = callback_update(
        f"gc:{clan_key}:fd:{USER_ID}:10", update_id=9300, chat_type="supergroup"
    )
    await clan.confirm_callback(group, context)
    group_text = group.callback_query.edit_message_text.call_args.args[0]
    assert "paused" in group_text.lower()

    # No order of any kind reached the CLOB.
    assert clob.placed == []

    # Withdrawals still complete while paused (F-25).
    await withdraw.withdraw(message_update(), context)
    await withdraw.handle_withdraw_text(message_update("5"), context)
    dest = "0xAbCd000000000000000000000000000000001234"
    await withdraw.handle_withdraw_text(message_update(dest), context)
    confirm = callback_update("wd:yes", update_id=9400)
    await withdraw.confirm_callback(confirm, context)

    context.application.bot_data["wallets"].withdraw.assert_awaited_once_with(
        USER_ID, dest, Decimal("5")
    )
    assert "Withdrawal submitted" in confirm.callback_query.edit_message_text.call_args.args[0]


async def test_resume_restores_every_order_path(settings, session_factory, onboard, real_trading):
    trading, clob = real_trading
    await onboard(USER_ID)
    await trading.set_paused(True, by_user_id=1)
    await trading.set_paused(False, by_user_id=1)
    context = make_context(settings, session_factory, trading)

    key = await trade.cache_card(context, MARKET)
    buy = callback_update(f"tc:{key}:y:10", update_id=9500)
    await trade.confirm_callback(buy, context)

    assert len(clob.placed) == 1
    assert "Filled." in buy.callback_query.edit_message_text.call_args.args[0]
