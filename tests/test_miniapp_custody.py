"""Mini App client-side key custody (PRD §7.8, Stage 15 engineering slice).

initData verification is pure crypto (fake bot token, no network). The fake
key below is obviously fake, per project testing rules — and notably it never
appears in any request body, only in the assertion that it DOESN'T.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import export, onboarding
from fade.db.models import User, Wallet
from fade.miniapp import MINIAPP_HTML, handle_register, validate_init_data
from fade.wallets import (
    CLIENT_CUSTODY_MESSAGE,
    ClientCustodyError,
    WalletService,
)

BOT_TOKEN = "0000000000:FAKE-TEST-TOKEN"  # matches the settings fixture
USER_ID = 950
CLIENT_ADDRESS = "0xC11e0000000000000000000000000000000000aa"
FAKE_KEY = "0x" + "ab" * 32  # obviously fake; must never leave the "browser"


def signed_init_data(user_id: int, bot_token: str = BOT_TOKEN) -> str:
    """Build initData signed exactly the way Telegram signs it."""
    fields = {
        "auth_date": "1780000000",
        "query_id": "AAtest",
        "user": json.dumps({"id": user_id, "first_name": "Tester"}),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# -- (1) initData verification: pure crypto ---------------------------------------


def test_valid_init_data_is_accepted():
    fields = validate_init_data(signed_init_data(USER_ID), BOT_TOKEN)
    assert fields is not None
    assert json.loads(fields["user"])["id"] == USER_ID


def test_tampered_init_data_is_rejected():
    data = signed_init_data(USER_ID)
    tampered = data.replace(str(USER_ID), str(USER_ID + 1))  # swap the user id
    assert validate_init_data(tampered, BOT_TOKEN) is None
    assert validate_init_data(data, "wrong-token") is None
    assert validate_init_data("", BOT_TOKEN) is None
    assert validate_init_data("no-hash-at-all=1", BOT_TOKEN) is None


# -- (1) the register endpoint ------------------------------------------------------


async def onboarded_user(session_factory, user_id: int = USER_ID) -> None:
    from datetime import UTC, datetime

    async with session_factory() as session:
        session.add(
            User(
                telegram_user_id=user_id,
                attestation_accepted_at=datetime.now(UTC),
                attestation_version="test",
            )
        )
        await session.commit()


async def test_register_rejects_tampered_init_data_before_any_state_change(
    settings, session_factory
):
    await onboarded_user(session_factory)
    wallets = WalletService(settings, session_factory)
    body = {
        "init_data": signed_init_data(USER_ID).replace(str(USER_ID), "999"),
        "address": CLIENT_ADDRESS,
    }

    response = await handle_register(session_factory, wallets, BOT_TOKEN, body)

    assert response.status_code == 401
    async with session_factory() as session:
        assert (await session.scalar(select(Wallet))) is None  # nothing was written


async def test_register_creates_client_wallet_with_no_server_key(settings, session_factory):
    await onboarded_user(session_factory)
    wallets = WalletService(settings, session_factory)
    body = {"init_data": signed_init_data(USER_ID), "address": CLIENT_ADDRESS}

    response = await handle_register(session_factory, wallets, BOT_TOKEN, body)

    assert response.status_code == 200
    async with session_factory() as session:
        row = await session.scalar(select(Wallet))
    assert row.telegram_user_id == USER_ID  # bound to the AUTHENTICATED id
    assert row.deposit_wallet_address == CLIENT_ADDRESS
    assert row.custody == "client"
    # The server holds NO key for this wallet: export refuses at the service
    # boundary (it never even reaches the signing store).
    with pytest.raises(ClientCustodyError):
        await wallets.export_key(USER_ID)


async def test_register_requires_attestation(settings, session_factory):
    # Valid signature, but the user never passed the gate.
    wallets = WalletService(settings, session_factory)
    body = {"init_data": signed_init_data(USER_ID), "address": CLIENT_ADDRESS}

    response = await handle_register(session_factory, wallets, BOT_TOKEN, body)

    assert response.status_code == 403
    async with session_factory() as session:
        assert (await session.scalar(select(Wallet))) is None


async def test_register_rejects_bad_address(settings, session_factory):
    await onboarded_user(session_factory)
    wallets = WalletService(settings, session_factory)
    body = {"init_data": signed_init_data(USER_ID), "address": "not-an-address"}

    response = await handle_register(session_factory, wallets, BOT_TOKEN, body)

    assert response.status_code == 400


def test_miniapp_page_generates_key_client_side_and_never_posts_it():
    # The page's register call sends ONLY initData + the public address.
    assert "wallet.address" in MINIAPP_HTML
    assert "init_data: tg.initData" in MINIAPP_HTML
    assert "privateKey" not in MINIAPP_HTML.split("fetch(")[1].split(")")[0]
    assert FAKE_KEY not in MINIAPP_HTML  # and obviously no key constants


def test_webhook_app_serves_the_miniapp_routes(settings):
    from fade.webhook import create_webhook_app

    app = create_webhook_app(settings)
    paths = {route.path for route in app.routes}
    assert "/miniapp" in paths
    assert "/miniapp/register" in paths


# -- (2) the feature flag ------------------------------------------------------------


def attest_update() -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.effective_user.username = "tester"
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    return update


def attest_context(settings, session_factory, has_wallet: bool = False) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.create_wallet = AsyncMock(return_value="0xFadE0000000000000000000000000000000000aa")
    wallets.get_wallet_address = AsyncMock(
        return_value="0xFadE0000000000000000000000000000000000aa" if has_wallet else None
    )
    wallets.get_balance = AsyncMock(return_value=Decimal("0"))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": "0xDep05170000"})
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
    }
    return context


async def test_flag_off_keeps_server_side_wallet_creation_unchanged(settings, session_factory):
    assert settings.miniapp_custody is False  # the fixture IS the default
    context = attest_context(settings, session_factory)

    await onboarding.attest_callback(attest_update(), context)

    context.application.bot_data["wallets"].create_wallet.assert_awaited_once_with(USER_ID)


async def test_flag_on_hands_out_miniapp_button_instead_of_creating_a_signer(
    settings, session_factory
):
    flagged = replace(
        settings, miniapp_custody=True, miniapp_url="https://fade.example/miniapp"
    )
    context = attest_context(flagged, session_factory)
    update = attest_update()

    await onboarding.attest_callback(update, context)

    context.application.bot_data["wallets"].create_wallet.assert_not_awaited()  # no signer
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert text == onboarding.MINIAPP_CUSTODY_TEXT
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://fade.example/miniapp"


# -- (3) capability honesty ------------------------------------------------------------


async def client_wallet_service(settings, session_factory) -> WalletService:
    await onboarded_user(session_factory)
    wallets = WalletService(settings, session_factory)
    await wallets.register_client_wallet(USER_ID, CLIENT_ADDRESS)
    return wallets


async def test_export_explains_the_server_holds_no_key(settings, session_factory):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=CLIENT_ADDRESS)
    wallets.get_custody = AsyncMock(return_value="client")
    wallets.export_key = AsyncMock()
    context.application.bot_data = {"settings": settings, "wallets": wallets}
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.message.reply_text = AsyncMock()

    await export.export(update, context)

    assert update.message.reply_text.call_args.args[0] == export.NO_SERVER_KEY_TEXT
    wallets.export_key.assert_not_awaited()  # nothing to export, nothing touched


async def test_withdrawals_and_trading_refuse_client_custody_plainly(
    settings, session_factory
):
    """Slice decision: server-signed actions return a clear not-supported
    message for client wallets (the server CANNOT sign without the key)."""
    wallets = await client_wallet_service(settings, session_factory)

    with pytest.raises(ClientCustodyError, match="not supported"):
        await wallets.withdraw(USER_ID, "0x" + "11" * 20, Decimal("5"))

    with pytest.raises(ClientCustodyError, match="key lives on your device"):
        async with wallets.secure_client(USER_ID):
            pass  # never reached

    # Through the real TradingService: the refusal surfaces as a failed order
    # carrying the plain message — before any CLOB contact (no client exists).
    from fade.trading.service import TradingService

    trading = TradingService(settings, session_factory, wallets)
    result = await trading.execute_market_order(
        telegram_user_id=USER_ID,
        telegram_update_id=95_001,
        token_id="111",
        market_title="Will it rain?",
        side="BUY",
        amount=Decimal("10"),
    )
    assert result.status == "failed"
    assert CLIENT_CUSTODY_MESSAGE[:40] in result.error
