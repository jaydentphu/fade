"""Stage 16: post-audit hardening — client-custody UX degradation, callback
byte safety, and Mini App script integrity pins. (initData freshness lives
in tests/test_miniapp_custody.py with the rest of that surface.)
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import discover, onboarding, positions, wallet
from fade.miniapp import MINIAPP_HTML
from fade.trading.markets import MarketCard, truncate_utf8
from fade.trading.service import PositionView

USER_ID = 960
CLIENT_ADDRESS = "0xC11e0000000000000000000000000000000000aa"


def flagged(settings):
    return replace(settings, miniapp_custody=True, miniapp_url="https://fade.example/miniapp")


def client_wallets_mock() -> MagicMock:
    """A client-custody wallet as the WalletService reports it: get_balance
    raising is the crash the handlers must never reach anymore."""
    from fade.wallets import CLIENT_CUSTODY_MESSAGE, ClientCustodyError

    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=CLIENT_ADDRESS)
    wallets.get_custody = AsyncMock(return_value="client")
    wallets.get_balance = AsyncMock(side_effect=ClientCustodyError(CLIENT_CUSTODY_MESSAGE))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": CLIENT_ADDRESS})
    return wallets


def dm_update() -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.effective_user.username = "clienty"
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    progress.delete = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.message.reply_photo = AsyncMock()  # the F-2 deposit QR
    update.callback_query = None
    return update


# -- (2) client-custody UX: no crashes, honest copy ---------------------------------


async def test_wallet_degrades_honestly_for_client_custody(settings, session_factory):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[])
    context.application.bot_data = {
        "settings": flagged(settings),
        "sessions": session_factory,
        "wallets": client_wallets_mock(),
        "trading": trading,
    }
    update = dm_update()

    await wallet.wallet(update, context)  # must not raise

    text = update.message.reply_text.return_value.edit_text.call_args.args[0]
    assert wallet.CLIENT_BALANCE_LINE in text  # honest, not fake, not crashed
    assert "key lives on your device" in text  # what this mode can/can't do
    assert "/security" in text
    context.application.bot_data["wallets"].get_balance.assert_not_awaited()


async def test_start_overview_degrades_honestly_for_client_custody(settings, session_factory):
    from datetime import UTC, datetime

    from fade.db.models import User

    async with session_factory() as session:
        session.add(
            User(
                telegram_user_id=USER_ID,
                attestation_accepted_at=datetime.now(UTC),
                attestation_version="test",
            )
        )
        await session.commit()

    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.args = []
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[])
    context.application.bot_data = {
        "settings": flagged(settings),
        "sessions": session_factory,
        "wallets": client_wallets_mock(),
        "trading": trading,
    }
    update = dm_update()

    await onboarding.start(update, context)  # must not raise

    text = update.message.reply_text.call_args.args[0]
    assert onboarding.CLIENT_BALANCE_DISPLAY in text
    assert "/security" in text
    context.application.bot_data["wallets"].get_balance.assert_not_awaited()


async def test_positions_works_for_client_custody(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    trading = MagicMock()
    trading.list_positions = AsyncMock(
        return_value=[
            PositionView(
                token_id="111",
                title="Will it rain?",
                outcome="Yes",
                shares=Decimal("10"),
                avg_price=Decimal("0.5"),
                cur_price=Decimal("0.7"),
                cash_pnl=None,
                percent_pnl=None,
            )
        ]
    )
    context.application.bot_data = {
        "settings": flagged(settings),
        "sessions": session_factory,
        "wallets": client_wallets_mock(),
        "trading": trading,
    }
    update = dm_update()

    await positions.positions(update, context)  # public API path: must just work

    card_text = update.message.reply_text.call_args_list[-1].args[0]
    assert "Will it rain?" in card_text


# -- (3) callback-data byte safety ----------------------------------------------------


def test_truncate_utf8_respects_byte_budget_without_splitting_characters():
    long_cyrillic = "Политика" * 20  # 2 bytes per letter in UTF-8
    cut = truncate_utf8(long_cyrillic, 60)
    assert len(cut.encode("utf-8")) <= 60
    cut.encode("utf-8").decode("utf-8")  # no broken surrogate/partial bytes
    assert truncate_utf8("Sports", 60) == "Sports"  # short names untouched


async def test_long_non_ascii_category_stays_within_telegram_limit_and_round_trips():
    long_category = "Международная политика и экономика мира"  # >64 bytes in UTF-8
    assert len(long_category.encode("utf-8")) > 64

    card = MarketCard(
        market_id="1",
        question="Will it happen?",
        slug="will-it",
        yes_token="111",
        no_token="222",
        yes_price=Decimal("0.5"),
        no_price=Decimal("0.5"),
        end_date=None,
        open=True,
        category=long_category,
    )
    context = MagicMock()
    trading = MagicMock()
    trading.list_trending = AsyncMock(return_value=[card])
    trading.list_by_category = AsyncMock(return_value=[card])
    context.application.bot_data = {"trading": trading}

    update = MagicMock(spec=Update)
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    await discover.trending(update, context)

    markup = progress.edit_text.call_args.kwargs["reply_markup"]
    category_data = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("c:")
    ][0]
    assert len(category_data.encode("utf-8")) <= 64  # Telegram's hard limit

    # The (truncated) tap still works end to end through the handler.
    tap = MagicMock(spec=Update)
    tap.callback_query = MagicMock()
    tap.callback_query.data = category_data
    tap.callback_query.answer = AsyncMock()
    tap.callback_query.edit_message_text = AsyncMock()
    await discover.category_callback(tap, context)

    trading.list_by_category.assert_awaited_once()
    markup = tap.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data.startswith("v:")


# -- (4) Mini App script integrity ------------------------------------------------------


def test_both_miniapp_script_tags_carry_integrity_pins():
    script_tags = [
        chunk.split(">")[0] for chunk in MINIAPP_HTML.split("<script ")[1:] if "src=" in chunk
    ]
    assert len(script_tags) == 2  # telegram-web-app.js + ethers
    for tag in script_tags:
        assert 'integrity="sha384-' in tag, tag
        assert 'crossorigin="anonymous"' in tag, tag
