"""Flex cards in the bot: the /positions Flex button (open positions) and the
resolution scanner's win/loss cards. Rendering is captured, not decoded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from PIL import Image
from telegram import Update

from fade.bot.handlers import positions
from fade.bot.handlers.resolutions import (
    FLEX_LOSS_CAPTION,
    FLEX_WIN_CAPTION,
    ResolutionScanner,
    ResolvedPosition,
)
from fade.db.models import Order, Wallet
from fade.social import flex_card
from fade.trading.service import PositionView

USER_ID = 990
WALLET_ADDR = "0xFadE0000000000000000000000000000000000cc"
LINK = f"https://t.me/fadetest_bot?start=ref{USER_ID}"

VIEW = PositionView(
    token_id="111",
    title="Will it rain tomorrow?",
    outcome="Yes",
    shares=Decimal("16.4"),
    avg_price=Decimal("0.61"),
    cur_price=Decimal("0.70"),
    cash_pnl=Decimal("1.48"),
    percent_pnl=14.8,
)


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    wallets = MagicMock()
    wallets.get_wallet_address = AsyncMock(return_value=WALLET_ADDR)
    trading = MagicMock()
    trading.list_positions = AsyncMock(return_value=[VIEW])
    reporter = MagicMock()
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
        "trading": trading,
        "error_reporter": reporter,
    }
    return context


def positions_update() -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = USER_ID
    update.effective_user.username = "flexer"
    progress = MagicMock()
    progress.edit_text = AsyncMock()
    progress.delete = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=progress)
    update.callback_query = None
    return update


def flex_tap(key: str, user_id: int = USER_ID) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.callback_query = MagicMock()
    update.callback_query.data = f"pf:{key}"
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_photo = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


async def open_positions_and_get_key(context) -> str:
    update = positions_update()
    await positions.positions(update, context)
    row = update.message.reply_text.call_args_list[-1].kwargs["reply_markup"].inline_keyboard[0]
    return row[1].callback_data.split(":")[1]


# -- /positions Flex button ---------------------------------------------------------


async def test_positions_card_offers_flex_next_to_sell(settings, session_factory, onboard):
    await onboard(USER_ID)
    update = positions_update()

    await positions.positions(update, make_context(settings, session_factory))

    row = update.message.reply_text.call_args_list[-1].kwargs["reply_markup"].inline_keyboard[0]
    assert [b.text for b in row] == ["Sell", "✨ Flex"]
    assert row[0].callback_data.startswith("ps:")  # Sell is untouched, still first
    assert row[1].callback_data.startswith("pf:")


async def test_owner_flex_sends_live_card_with_referral_and_hold_time(
    settings, session_factory, onboard, monkeypatch
):
    await onboard(USER_ID)
    async with session_factory() as session:
        session.add(
            Order(
                telegram_user_id=USER_ID, telegram_update_id=88_001, idempotency_key="flex-1",
                token_id="111", market_title="Will it rain tomorrow?", side="BUY",
                amount=Decimal("10"), status="filled",
                created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
            )
        )
        await session.commit()

    captured = {}
    real = flex_card.generate_open_flex_card

    def spy(view, **kwargs):
        captured.update(kwargs, view=view)
        return real(view, **kwargs)

    monkeypatch.setattr(flex_card, "generate_open_flex_card", spy)
    context = make_context(settings, session_factory)
    key = await open_positions_and_get_key(context)

    tap = flex_tap(key)
    await positions.flex_callback(tap, context)

    tap.callback_query.answer.assert_awaited_once_with(positions.FLEX_RENDERING_TOAST)
    kwargs = tap.callback_query.message.reply_photo.call_args.kwargs
    assert Image.open(BytesIO(kwargs["photo"])).size == (flex_card.WIDTH, flex_card.HEIGHT)
    assert LINK in kwargs["caption"]
    assert "0x" not in kwargs["caption"]  # no wallet data rides along
    # The data pipeline got the right inputs: this position, its link, and
    # roughly two hours held (from the seeded first fill).
    assert captured["view"].token_id == "111"
    assert captured["referral_link"] == LINK
    assert timedelta(hours=1, minutes=59) < captured["held"] < timedelta(hours=2, minutes=1)


async def test_someone_elses_position_cannot_be_flexed(settings, session_factory, onboard):
    await onboard(USER_ID)
    context = make_context(settings, session_factory)
    key = await open_positions_and_get_key(context)

    intruder = flex_tap(key, user_id=777)
    await positions.flex_callback(intruder, context)

    intruder.callback_query.answer.assert_awaited_once_with(
        positions.NOT_YOUR_POSITION_ALERT, show_alert=True
    )
    intruder.callback_query.message.reply_photo.assert_not_awaited()


async def test_expired_card_flex_is_refused_politely(settings, session_factory, onboard):
    await onboard(USER_ID)
    tap = flex_tap("nonexistent")

    await positions.flex_callback(tap, make_context(settings, session_factory))

    tap.callback_query.answer.assert_awaited_once_with(
        positions.CARD_EXPIRED_ALERT, show_alert=True
    )
    tap.callback_query.message.reply_photo.assert_not_awaited()


async def test_render_failure_is_reported_and_the_user_is_told(
    settings, session_factory, onboard, monkeypatch
):
    await onboard(USER_ID)

    def boom(view, **kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(flex_card, "generate_open_flex_card", boom)
    context = make_context(settings, session_factory)
    key = await open_positions_and_get_key(context)

    tap = flex_tap(key)
    await positions.flex_callback(tap, context)  # must not raise

    tap.callback_query.message.reply_text.assert_awaited_once_with(positions.FLEX_FAILED_ALERT)
    context.application.bot_data["error_reporter"].report.assert_called_once()


# -- resolution cards ----------------------------------------------------------------------


async def seed_wallet(session_factory, onboard) -> None:
    await onboard(USER_ID)
    async with session_factory() as session:
        session.add(Wallet(telegram_user_id=USER_ID, deposit_wallet_address=WALLET_ADDR))
        await session.commit()


def resolved(token_id: str, final: str) -> ResolvedPosition:
    return ResolvedPosition(
        token_id=token_id, title="Will it happen?", shares=Decimal("10"),
        avg_price=Decimal("0.40"), final_price=Decimal(final), outcome="Yes",
    )


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.username = "fadetest_bot"
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


async def test_resolution_cards_carry_link_and_variant_caption(
    settings, session_factory, onboard, monkeypatch
):
    await seed_wallet(session_factory, onboard)
    captured = []
    real = flex_card.generate_flex_card

    def spy(position, pnl, **kwargs):
        captured.append((pnl, kwargs))
        return real(position, pnl, **kwargs)

    monkeypatch.setattr(flex_card, "generate_flex_card", spy)

    # Distinct tokens, so the idempotency barrier treats them as two settlements.
    cases = ((resolved("555", "1"), FLEX_WIN_CAPTION), (resolved("556", "0"), FLEX_LOSS_CAPTION))
    for position, caption_start in cases:
        async def fetch(address, position=position):
            return [position]

        bot = make_bot()
        await ResolutionScanner(session_factory, fetch=fetch).scan_once(bot)

        caption = bot.send_photo.call_args.kwargs["caption"]
        assert caption.startswith(caption_start)
        assert LINK in caption

    (win_pnl, win_kwargs), (loss_pnl, loss_kwargs) = captured
    assert win_pnl == Decimal("6.00")  # (1 - 0.40) * 10
    assert loss_pnl == Decimal("-4.00")  # (0 - 0.40) * 10
    assert win_kwargs["referral_link"] == loss_kwargs["referral_link"] == LINK


async def test_unknown_bot_username_means_no_link_rather_than_a_wrong_one(
    settings, session_factory, onboard
):
    await seed_wallet(session_factory, onboard)

    async def fetch(address):
        return [resolved("557", "1")]

    bot = make_bot()
    # PTB raises when a Bot was never initialized; don't guess a username.
    type(bot).username = PropertyMock(side_effect=RuntimeError("not initialized"))

    await ResolutionScanner(session_factory, fetch=fetch).scan_once(bot)

    caption = bot.send_photo.call_args.kwargs["caption"]
    assert caption == FLEX_WIN_CAPTION  # no "Ride with me" line pointing at a guessed bot
    assert "t.me" not in caption
