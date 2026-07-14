"""Stage 13: UX completion of shipped features.

/wallet position value (F-2), fee itemization (F-13), /help + command menu,
/start deep-link routing, and username freshness. No live network anywhere.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.app import _post_init
from fade.bot.handlers import basic, onboarding, trade, wallet
from fade.bot.handlers.basic import COMMANDS, COMMANDS_TEXT
from fade.social.record import compute_record
from fade.trading.service import OrderResult, PositionView

USER_ID = 811

VIEWS = [
    PositionView(
        token_id="111",
        title="Will it rain tomorrow?",
        outcome="Yes",
        shares=Decimal("10"),
        avg_price=Decimal("0.50"),
        cur_price=Decimal("0.70"),  # value 7.00
        cash_pnl=None,
        percent_pnl=None,
    ),
    PositionView(
        token_id="222",
        title="Will the Fed cut rates?",
        outcome="No",
        shares=Decimal("20"),
        avg_price=Decimal("0.40"),
        cur_price=Decimal("0.25"),  # value 5.00
        cash_pnl=None,
        percent_pnl=None,
    ),
]


# -- (1) /wallet open-position value (PRD F-2) -----------------------------------


def wallet_context(list_positions) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(
        return_value="0xFadE00000000000000000000000000000000aaaa"
    )
    wallets.get_custody = AsyncMock(return_value="server")  # classic mode
    wallets.get_balance = AsyncMock(return_value=Decimal("12.5"))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": "0xDep05170000"})
    trading = MagicMock()
    trading.list_positions = list_positions
    context.application.bot_data = {"wallets": wallets, "trading": trading}
    return context


def dm_update() -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    return update


async def test_wallet_shows_open_position_value():
    update = dm_update()
    context = wallet_context(AsyncMock(return_value=VIEWS))

    await wallet.wallet(update, context)

    text = update.message.reply_text.return_value.edit_text.call_args.args[0]
    assert "12.5" in text  # balance unchanged
    assert "Open positions: $12.00" in text  # 10*0.70 + 20*0.25


async def test_wallet_positions_fetch_failure_still_shows_balance():
    update = dm_update()
    context = wallet_context(AsyncMock(side_effect=RuntimeError("data api down")))

    await wallet.wallet(update, context)  # must not crash

    text = update.message.reply_text.return_value.edit_text.call_args.args[0]
    assert "12.5" in text  # balance still renders
    assert wallet.POSITIONS_VALUE_UNAVAILABLE in text


# -- (2) fee itemization (PRD F-13) -----------------------------------------------


async def test_fill_confirmation_itemizes_both_fees_and_balance_unchanged():
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    result = OrderResult(
        status="filled",
        side="BUY",
        fill_price=Decimal("0.61"),
        fill_shares=Decimal("40.9"),
        fill_usdc=Decimal("25"),
        fee=Decimal("0.25"),
        platform_fee=Decimal("0.19"),
        new_balance=Decimal("75"),
    )

    await trade.render_result(query, result, "Bought YES", "Will it rain?")

    text = query.edit_message_text.call_args.args[0]
    assert "Fee (builder): $0.25" in text
    assert "Fee (Polymarket, estimated): $0.19" in text
    assert "New balance: $75" in text  # unchanged: it always reflected the real fill


async def test_fill_confirmation_is_honest_when_platform_fee_unknown():
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    result = OrderResult(
        status="filled",
        side="BUY",
        fill_price=Decimal("0.61"),
        fill_shares=Decimal("40.9"),
        fill_usdc=Decimal("25"),
        fee=Decimal("0.25"),
        platform_fee=None,  # estimate unavailable
        new_balance=Decimal("75"),
    )

    await trade.render_result(query, result, "Bought YES", "Will it rain?")

    text = query.edit_message_text.call_args.args[0]
    assert trade.PLATFORM_FEE_UNAVAILABLE in text
    assert "$None" not in text and "$0.00" not in text  # never a fake number


async def test_platform_fee_computed_from_market_fee_params(
    settings, session_factory, onboard, real_trading
):
    """End to end through the REAL TradingService over the fake CLOB client."""
    await onboard(USER_ID)
    service, clob = real_trading
    result = await service.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=90_001,
        token_id="111",
        market_title="Will it rain?",
        side="BUY",
        amount=Decimal("10"),
    )

    assert result.status == "filled"
    # Fake fill: 10 USDC -> 20 shares (price 0.50). Fee params: rate 0.02,
    # exponent 1 -> effective 0.02 * (0.5*0.5) = 0.005/share -> 20 * 0.005 = 0.10.
    assert result.platform_fee == Decimal("0.10")
    assert result.fee == Decimal("0.10")  # builder fee: 1% of 10 USDC, unchanged


# -- (3) /help + the Telegram command menu ----------------------------------------


async def test_help_contains_every_registered_command():
    update = MagicMock(spec=Update)
    update.message.reply_text = AsyncMock()

    await basic.help_command(update, MagicMock())

    text = update.message.reply_text.call_args.args[0]
    for name, _ in COMMANDS:
        assert f"/{name}" in text, f"/{name} missing from /help"
    # The names the goal calls out specifically:
    for required in ("record", "challenge", "security", "privacy", "leaderboard"):
        assert f"/{required}" in text
    # Group-safety: static list, no addresses or balance figures in it.
    assert "0x" not in text
    assert "$" not in text


async def test_startup_registers_the_same_commands_with_telegram():
    application = MagicMock()
    application.bot.set_my_commands = AsyncMock()

    await _post_init(application)

    registered = application.bot.set_my_commands.call_args.args[0]
    assert [c.command for c in registered] == [name for name, _ in COMMANDS]


async def test_onboarding_overview_uses_the_shared_command_list():
    assert onboarding.COMMANDS_TEXT is COMMANDS_TEXT  # one constant, no drift


# -- (4) /start deep-link payload routing ------------------------------------------


def start_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(
        return_value="0xFadE00000000000000000000000000000000aaaa"
    )
    wallets.get_custody = AsyncMock(return_value="server")  # classic mode
    wallets.get_balance = AsyncMock(return_value=Decimal("50"))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": "0xDep05170000"})
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[])
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
        "trading": trading,
    }
    return context


def start_update() -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.effective_user.username = "someuser"
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    progress.delete = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    return update


async def test_start_payload_withdraw_enters_withdraw_flow(settings, session_factory, onboard):
    from fade.bot.handlers.withdraw import ASK_AMOUNT_TEXT

    await onboard(USER_ID)
    context = start_context(settings, session_factory)
    context.args = ["withdraw"]
    update = start_update()

    await onboarding.start(update, context)

    assert update.message.reply_text.call_args.args[0] == ASK_AMOUNT_TEXT
    assert context.user_data["withdraw"] == {"step": "amount"}


async def test_start_payload_positions_shows_positions(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = start_context(settings, session_factory)
    context.args = ["positions"]
    update = start_update()

    await onboarding.start(update, context)

    context.application.bot_data["trading"].list_positions.assert_awaited_once()


async def test_start_unknown_payload_falls_back_to_overview(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = start_context(settings, session_factory)
    context.args = ["something-unknown"]
    update = start_update()

    await onboarding.start(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "Balance" in text  # the normal overview


async def test_start_payload_never_bypasses_attestation_gate(settings, session_factory):
    # NOT onboarded: any payload still lands on the attestation gate first.
    context = start_context(settings, session_factory)
    context.args = ["withdraw"]
    update = start_update()

    await onboarding.start(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert text == onboarding.ATTESTATION_TEXT
    assert "withdraw" not in context.user_data  # no flow started


# -- (5) username freshness ---------------------------------------------------------


async def test_renamed_user_shows_new_name_after_next_interaction(
    settings, session_factory, onboard
):
    from sqlalchemy import select

    from fade.db.models import User

    await onboard(USER_ID)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
        user.username = "oldname"
        await session.commit()

    # Their next interaction arrives under a new Telegram username.
    context = start_context(settings, session_factory)
    update = dm_update()
    update.effective_user.username = "newname"
    from fade.bot.handlers import positions as positions_handlers

    await positions_handlers.positions(update, context)

    record = await compute_record(session_factory, USER_ID)
    assert record.username == "newname"  # /record and leaderboards now agree
