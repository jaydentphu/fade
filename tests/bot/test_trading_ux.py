"""Stage 17: trading UX completion — deposit QR (F-2), /slippage, /history,
partial sells. No live network anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import history, positions, slippage, wallet
from fade.bot.handlers.basic import COMMANDS
from fade.db.models import Order, User
from fade.trading.service import OrderResult, PositionView

USER_ID = 970
OTHER_ID = 971
DEPOSIT_ADDR = "0xDe9051700000000000000000000000000000BBbb"

VIEW = PositionView(
    token_id="111",
    title="Will it rain tomorrow?",
    outcome="Yes",
    shares=Decimal("16.4"),
    avg_price=Decimal("0.61"),
    cur_price=Decimal("0.70"),
    cash_pnl=None,
    percent_pnl=None,
    owner_id=USER_ID,
)

FILLED_SELL = OrderResult(
    status="filled",
    side="SELL",
    fill_price=Decimal("0.70"),
    fill_shares=Decimal("8.2"),
    fill_usdc=Decimal("5.74"),
    fee=Decimal("0.06"),
    new_balance=Decimal("100"),
)


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(
        return_value="0xFadE0000000000000000000000000000000000aa"
    )
    wallets.get_custody = AsyncMock(return_value="server")
    wallets.get_balance = AsyncMock(return_value=Decimal("12.5"))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": DEPOSIT_ADDR})
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[])
    trading.execute_market_order = AsyncMock(return_value=FILLED_SELL)
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
        "trading": trading,
    }
    return context


def dm_update(user_id: int = USER_ID, chat_type: str = "private") -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = 10_000
    update.effective_chat.type = chat_type
    update.effective_user.id = user_id
    update.effective_user.username = "trader"
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    progress.delete = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.message.reply_photo = AsyncMock()
    update.callback_query = None
    return update


def callback_update(data: str, user_id: int = USER_ID, update_id: int = 10_100) -> MagicMock:
    update = MagicMock(spec=Update)
    update.update_id = update_id
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.effective_user.username = "trader"
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


# -- (1) deposit QR (PRD F-2 as written) --------------------------------------------


def test_qr_renderer_returns_valid_nonempty_image():
    image_bytes = wallet.render_deposit_qr(DEPOSIT_ADDR)

    assert len(image_bytes) > 0
    img = Image.open(BytesIO(image_bytes))
    img.verify()
    assert img.format == "PNG"


def test_qr_payload_is_exactly_the_deposit_address():
    # Pipeline discipline (same as flex cards): the string handed to the
    # encoder is the address verbatim — no scheme, no decoration.
    assert wallet.build_qr_payload(DEPOSIT_ADDR) == DEPOSIT_ADDR


async def test_wallet_sends_qr_photo_in_dm_and_group_path_is_unchanged(
    settings, session_factory, onboard
):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    update = dm_update()

    await wallet.wallet(update, context)

    update.message.reply_photo.assert_awaited_once()
    kwargs = update.message.reply_photo.call_args.kwargs
    assert len(kwargs["photo"]) > 0
    assert kwargs["caption"] == wallet.QR_CAPTION
    # The text view still renders alongside it.
    text = update.message.reply_text.return_value.edit_text.call_args.args[0]
    assert "12.5" in text

    # Group path: redirect only, no photo, no details — byte-identical rule.
    group = dm_update(chat_type="supergroup")
    await wallet.wallet(group, context)
    group.message.reply_photo.assert_not_awaited()
    assert group.message.reply_text.call_args.args[0] == wallet.GROUP_REDIRECT_TEXT


# -- (2) /slippage -------------------------------------------------------------------


async def test_slippage_shows_current_value_and_presets(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    update = dm_update()

    await slippage.slippage(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "2% (200 bps)" in text  # the default, in plain language
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    offered = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert offered == [f"sl:{bps}" for bps in slippage.SLIPPAGE_PRESETS_BPS]
    assert "$" not in text and "0x" not in text  # no balances, no addresses


async def test_slippage_preset_persists_and_orders_use_it(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)

    tap = callback_update("sl:50")
    await slippage.set_callback(tap, context)

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
    assert user.slippage_bps == 50
    assert "0.5% (50 bps)" in tap.callback_query.edit_message_text.call_args.args[0]

    # Subsequent orders read the new value through the real TradingService.
    from fade.trading.service import TradingService

    service = TradingService(settings, session_factory, MagicMock())
    assert await service._slippage_bps(USER_ID) == 50


async def test_slippage_bounds_hold_even_for_forged_callbacks(
    settings, session_factory, onboard
):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)

    # Only the presets are offered (floor 0.5%, ceiling 5%)…
    assert min(slippage.SLIPPAGE_PRESETS_BPS) == 50
    assert max(slippage.SLIPPAGE_PRESETS_BPS) == 500

    # …and a forged out-of-bounds callback is refused server-side.
    forged = callback_update("sl:10000")
    await slippage.set_callback(forged, context)
    forged.callback_query.answer.assert_awaited_once_with(
        slippage.INVALID_ALERT, show_alert=True
    )
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
    assert user.slippage_bps == 200  # untouched


# -- (3) /history --------------------------------------------------------------------


def order_row(uid: int, n: int, side: str, status: str, title: str) -> Order:
    return Order(
        telegram_user_id=uid,
        telegram_update_id=20_000 + n,
        idempotency_key=f"hist-{uid}-{n}",
        token_id="111",
        market_title=title,
        side=side,
        amount=Decimal("25"),
        status=status,
        fill_price=Decimal("0.61") if status == "filled" else None,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


async def test_history_renders_own_orders_newest_first(settings, session_factory, onboard):
    await onboard(USER_ID)
    await onboard(OTHER_ID)
    async with session_factory() as session:
        session.add(order_row(USER_ID, 1, "BUY", "filled", "Will it rain tomorrow?"))
        session.add(order_row(USER_ID, 2, "BUY", "rejected", "Will the Fed cut rates?"))
        session.add(order_row(OTHER_ID, 3, "BUY", "filled", "SOMEONE ELSES MARKET"))
        await session.commit()

    context = make_context(settings, session_factory)
    update = dm_update()
    await history.history(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert text.index("Fed cut rates") < text.index("rain tomorrow")  # newest first
    assert "rejected" in text  # non-fills shown honestly
    assert "@ 0.61" in text  # fill price where filled
    assert "SOMEONE ELSES MARKET" not in text  # never another user's orders

    # Group: standard redirect, no order data.
    group = dm_update(chat_type="supergroup")
    await history.history(group, context)
    assert group.message.reply_text.call_args.args[0] == history.GROUP_REDIRECT_TEXT


# -- (4) partial sells ----------------------------------------------------------------


async def seeded_sell_key(context):
    return await positions._cache_position(context, VIEW)


async def test_partial_50_sells_exactly_half_rounded_down(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = await seeded_sell_key(context)

    pick = callback_update(f"psp:{key}:50")
    await positions.sell_partial_callback(pick, context)
    confirm_text = pick.callback_query.edit_message_text.call_args.args[0]
    assert "8.2" in confirm_text and "50%" in confirm_text

    confirm = callback_update(f"psc:{key}:50", update_id=10_200)
    await positions.sell_confirm_callback(confirm, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["side"] == "SELL"
    # 16.4 * 50% = 8.2 exactly; precision rule: quantized to 6dp, ROUND_DOWN.
    assert call.kwargs["amount"] == Decimal("8.200000")
    assert positions.sell_amount(Decimal("0.000001"), 50) == Decimal("0")  # never oversells


async def test_full_sell_path_matches_previous_behavior(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = await seeded_sell_key(context)

    confirm = callback_update(f"psc:{key}", update_id=10_300)  # bare psc: as always
    await positions.sell_confirm_callback(confirm, context)

    call = context.application.bot_data["trading"].execute_market_order.call_args
    assert call.kwargs["amount"] == Decimal("16.4")  # the exact held amount


async def test_partial_sell_buttons_are_owner_locked(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = await seeded_sell_key(context)

    intruder = callback_update(f"psp:{key}:50", user_id=999)
    await positions.sell_partial_callback(intruder, context)
    intruder.callback_query.answer.assert_awaited_once_with(
        positions.NOT_YOUR_POSITION_ALERT, show_alert=True
    )

    intruder2 = callback_update(f"psc:{key}:50", user_id=999, update_id=10_400)
    await positions.sell_confirm_callback(intruder2, context)
    context.application.bot_data["trading"].execute_market_order.assert_not_awaited()


# -- (5) registry ---------------------------------------------------------------------


def test_registry_gains_slippage_and_history():
    names = [name for name, _ in COMMANDS]
    assert "slippage" in names
    assert "history" in names
