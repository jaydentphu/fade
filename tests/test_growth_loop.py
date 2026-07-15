"""Stage 18: growth loop — referral attribution (PRD J1, D-14), /invite,
and shareable entry points. Attribution + links only; no rewards mechanics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update

from fade.bot.handlers import invite, onboarding
from fade.bot.handlers.basic import COMMANDS
from fade.bot.handlers.resolutions import ResolutionScanner, ResolvedPosition
from fade.db.models import User, Wallet
from fade.social.record import compute_record

REFERRER = 980
NEWBIE = 981
FAKE_ADDRESS = "0xFadE0000000000000000000000000000000000aa"


def make_context(settings, session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.user_data = {}
    context.args = []
    wallets = MagicMock()
    wallets.create_wallet = AsyncMock(return_value=FAKE_ADDRESS)
    wallets.get_wallet_address = AsyncMock(return_value=None)
    wallets.get_custody = AsyncMock(return_value="server")
    wallets.get_balance = AsyncMock(return_value=Decimal("0"))
    wallets.get_deposit_addresses = AsyncMock(return_value={"evm": "0xDep05170000"})
    context.application.bot_data = {
        "settings": settings,
        "sessions": session_factory,
        "wallets": wallets,
    }
    return context


def dm_update(user_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.effective_user.username = f"user{user_id}"
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def attest_tap(user_id: int) -> MagicMock:
    update = dm_update(user_id)
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def referred_by_of(session_factory, user_id: int) -> int | None:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == user_id))
    return user.referred_by if user else None


# -- (1) attribution rules ---------------------------------------------------------


async def test_new_user_via_ref_link_is_attributed_after_attestation(
    settings, session_factory, onboard
):
    await onboard(REFERRER)
    context = make_context(settings, session_factory)
    context.args = [f"ref{REFERRER}"]

    await onboarding.start(dm_update(NEWBIE), context)  # gate shown, referrer stashed
    await onboarding.attest_callback(attest_tap(NEWBIE), context)

    assert await referred_by_of(session_factory, NEWBIE) == REFERRER
    context.application.bot_data["wallets"].create_wallet.assert_awaited_once_with(NEWBIE)


async def test_existing_user_is_never_reattributed(settings, session_factory, onboard):
    await onboard(REFERRER)
    await onboard(NEWBIE)  # already a user, referred by nobody
    context = make_context(settings, session_factory)
    context.application.bot_data["wallets"].get_wallet_address = AsyncMock(
        return_value=FAKE_ADDRESS
    )
    context.args = [f"ref{REFERRER}"]

    await onboarding.start(dm_update(NEWBIE), context)  # lands on the overview

    assert await referred_by_of(session_factory, NEWBIE) is None
    assert "referrer_id" not in context.user_data


async def test_self_referral_noops(settings, session_factory):
    context = make_context(settings, session_factory)
    context.args = [f"ref{NEWBIE}"]

    await onboarding.start(dm_update(NEWBIE), context)
    await onboarding.attest_callback(attest_tap(NEWBIE), context)

    assert await referred_by_of(session_factory, NEWBIE) is None


async def test_nonexistent_referrer_is_ignored(settings, session_factory):
    context = make_context(settings, session_factory)
    context.args = ["ref999999999"]  # nobody

    await onboarding.start(dm_update(NEWBIE), context)
    await onboarding.attest_callback(attest_tap(NEWBIE), context)

    assert await referred_by_of(session_factory, NEWBIE) is None  # user created, unattributed
    async with session_factory() as session:
        assert (await session.scalar(select(User))) is not None


async def test_stage13_routing_payloads_keep_working(settings, session_factory, onboard):
    from fade.bot.handlers.withdraw import ASK_AMOUNT_TEXT

    await onboard(NEWBIE)
    context = make_context(settings, session_factory)
    context.application.bot_data["wallets"].get_wallet_address = AsyncMock(
        return_value=FAKE_ADDRESS
    )
    context.args = ["withdraw"]
    update = dm_update(NEWBIE)

    await onboarding.start(update, context)

    assert update.message.reply_text.call_args.args[0] == ASK_AMOUNT_TEXT


# -- (2) /invite ---------------------------------------------------------------------


async def test_invite_dm_contains_link_and_correct_count(settings, session_factory, onboard):
    await onboard(REFERRER)
    async with session_factory() as session:
        # Two attested recruits, one who started but never attested: count = 2.
        session.add(
            User(
                telegram_user_id=1001,
                referred_by=REFERRER,
                attestation_accepted_at=datetime.now(UTC),
            )
        )
        session.add(
            User(
                telegram_user_id=1002,
                referred_by=REFERRER,
                attestation_accepted_at=datetime.now(UTC),
            )
        )
        session.add(User(telegram_user_id=1003, referred_by=REFERRER))  # never attested
        await session.commit()

    context = make_context(settings, session_factory)
    update = dm_update(REFERRER)
    await invite.invite(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert f"https://t.me/fadetest_bot?start=ref{REFERRER}" in text
    assert "Recruits so far: 2" in text  # the drive-by doesn't count (D-14)


# -- (3) flex cards carry the entry point ----------------------------------------------


async def test_flex_caption_contains_referral_link_and_no_wallet_data(
    settings, session_factory, onboard
):
    await onboard(REFERRER)
    async with session_factory() as session:
        session.add(Wallet(telegram_user_id=REFERRER, deposit_wallet_address=FAKE_ADDRESS))
        await session.commit()

    async def fetch(address: str):
        return [
            ResolvedPosition(
                token_id="777",
                title="Will it happen?",
                shares=Decimal("10"),
                avg_price=Decimal("0.5"),
                final_price=Decimal("1"),
            )
        ]

    scanner = ResolutionScanner(session_factory, fetch=fetch)
    bot = MagicMock()
    bot.username = "fadetest_bot"
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()

    await scanner.scan_once(bot)

    caption = bot.send_photo.call_args.kwargs["caption"]
    assert f"https://t.me/fadetest_bot?start=ref{REFERRER}" in caption
    assert "0x" not in caption  # no wallet data rides along


# -- (4) /record surfaces recruits ------------------------------------------------------


async def test_record_shows_recruit_count(settings, session_factory, onboard):
    await onboard(REFERRER)
    async with session_factory() as session:
        session.add(
            User(
                telegram_user_id=1001,
                referred_by=REFERRER,
                attestation_accepted_at=datetime.now(UTC),
            )
        )
        await session.commit()

    record = await compute_record(session_factory, REFERRER)

    assert record.recruits == 1


# -- (5) registry ------------------------------------------------------------------------


def test_registry_gains_invite():
    assert "invite" in [name for name, _ in COMMANDS]
