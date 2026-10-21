"""Flex card redesign: losses, open positions, hold time, referral QR, and
glyph safety. Same discipline as tests/social/test_flex_card.py — the data
pipeline is pinned exactly; the renderer is checked for a valid image (plus
one pixel probe proving the QR tile is drawn only when there's a link).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from PIL import Image

from fade.bot.handlers.resolutions import ResolvedPosition
from fade.db.models import Order, User
from fade.social import flex_card as fc
from fade.trading.service import PositionView

LINK = "https://t.me/fadetest_bot?start=ref42"

WIN = ResolvedPosition(
    token_id="111",
    title="Will the Fed cut rates in September?",
    shares=Decimal("40"),
    avg_price=Decimal("0.61"),
    final_price=Decimal("1"),
    outcome="Yes",
)
LOSS = ResolvedPosition(
    token_id="222",
    title="Will it rain tomorrow?",
    shares=Decimal("20"),
    avg_price=Decimal("0.55"),
    final_price=Decimal("0"),
    outcome="No",
)
OPEN_UP = PositionView(
    token_id="333",
    title="Lakers win the 2026 NBA Championship?",
    outcome="No",
    shares=Decimal("120"),
    avg_price=Decimal("0.31"),
    cur_price=Decimal("0.54"),
    cash_pnl=Decimal("27.60"),
    percent_pnl=74.19,
)


def as_image(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    img.load()
    return img


# -- data pipeline -----------------------------------------------------------------


def test_resolved_loss_formats_as_red_got_faded():
    data = fc.build_flex_card_data(LOSS, pnl=Decimal("-11.00"))

    assert data.won is False
    assert data.status_label == fc.STATUS_LOSS
    assert data.pnl_dollar_label == "-$11.00"
    assert data.pnl_percent_label == "-100.0%"
    assert data.invested_label == "$11.00"
    assert data.exit_price_label == "$0.00"
    assert data.exit_caption == "Settled"


def test_resolved_win_keeps_stage8_formats_and_adds_new_fields():
    data = fc.build_flex_card_data(WIN, pnl=Decimal("15.60"), referral_link=LINK,
                                   held=timedelta(days=3, hours=4))

    # The original six-field contract, unchanged.
    assert (data.pnl_dollar_label, data.pnl_percent_label) == ("+$15.60", "+63.9%")
    assert (data.entry_price_label, data.exit_price_label) == ("$0.61", "$1.00")
    # The redesign's additions.
    assert data.won is True and data.status_label == fc.STATUS_WIN
    assert data.invested_label == "$24.40"
    assert data.held_label == "3d 4h"
    assert data.referral_link == LINK


def test_open_position_prefers_the_apis_own_pnl_figures():
    data = fc.build_open_flex_card_data(OPEN_UP, referral_link=LINK)

    assert data.status_label == fc.STATUS_LIVE
    assert data.exit_caption == "Now"
    assert data.exit_price_label == "$0.54"  # current price, not a settlement
    assert data.pnl_dollar_label == "+$27.60"
    assert data.pnl_percent_label == "+74.2%"
    assert data.invested_label == "$37.20"  # 0.31 * 120
    assert data.won is True


def test_open_position_computes_pnl_when_the_api_omits_it():
    view = PositionView(
        token_id="444", title="Some market", outcome="Yes", shares=Decimal("10"),
        avg_price=Decimal("0.50"), cur_price=Decimal("0.40"),
        cash_pnl=None, percent_pnl=None,
    )

    data = fc.build_open_flex_card_data(view)

    assert data.pnl_dollar_label == "-$1.00"  # (0.40 - 0.50) * 10
    assert data.pnl_percent_label == "-20.0%"
    assert data.won is False


def test_large_amounts_get_thousands_separators():
    big = PositionView(
        token_id="555", title="Big one", outcome="Yes", shares=Decimal("25000"),
        avg_price=Decimal("0.38"), cur_price=Decimal("0.83"),
        cash_pnl=Decimal("11250.00"), percent_pnl=118.42,
    )

    data = fc.build_open_flex_card_data(big)

    assert data.invested_label == "$9,500.00"
    assert data.pnl_dollar_label == "+$11,250.00"
    assert fc._signed_money(Decimal("-1234567.891")) == "-$1,234,567.89"


def test_hold_time_formats_like_trojan():
    assert fc.format_held(timedelta(seconds=45)) == "45s"
    assert fc.format_held(timedelta(minutes=12, seconds=24)) == "12m 24s"
    assert fc.format_held(timedelta(hours=5, minutes=3, seconds=9)) == "5h 3m"
    assert fc.format_held(timedelta(days=3, hours=4, minutes=59)) == "3d 4h"
    assert fc.format_held(timedelta(seconds=-5)) == "0s"  # clock skew never goes negative


# -- glyph safety (the Stage 8 tofu-box regression) -----------------------------------


def test_every_fixed_string_the_card_draws_has_glyphs():
    fixed = [
        fc.STATUS_WIN, fc.STATUS_LOSS, fc.STATUS_LIVE, fc.CTA_LINE_1, fc.CTA_LINE_2,
        fc.CTA_NO_LINK, "FADE", "INVESTED", "PROFIT", "LOSS", "CURRENT GAIN",
        "CURRENT LOSS", "Entry Settled Now", "YES NO", "0123456789$+-.%", "−",
        "·", "…", "3d 4h 12m 24s",
    ]
    missing = {ch for text in fixed for ch in text if not fc.has_glyph(ch)}
    assert not missing, f"would render as tofu boxes: {sorted(missing)}"


def test_untrusted_titles_lose_only_undrawable_characters():
    assert fc.drawable("Will BTC hit $150k? \U0001F680\U0001F680") == "Will BTC hit $150k?"
    assert fc.drawable("Who wins 中国 open?") == "Who wins open?"
    assert fc.drawable("Café naïve résumé") == "Café naïve résumé"  # Latin-1 survives


# -- renderer ---------------------------------------------------------------------------


def test_win_loss_and_live_cards_render_as_valid_landscape_pngs():
    for data in (
        fc.build_flex_card_data(WIN, Decimal("15.60"), referral_link=LINK),
        fc.build_flex_card_data(LOSS, Decimal("-11.00"), referral_link=LINK),
        fc.build_open_flex_card_data(OPEN_UP, referral_link=LINK, held=timedelta(minutes=12)),
    ):
        img = as_image(fc.render_flex_card_image(data))
        assert img.format == "PNG"
        assert img.size == (fc.WIDTH, fc.HEIGHT)


def test_qr_tile_is_drawn_only_when_there_is_a_referral_link():
    tile = fc._qr_tile(LINK)
    right = fc._PANEL[2] - fc._PAD
    footer_top = fc._PANEL[3] - fc._PAD - fc._FOOTER_H
    tile_top = footer_top + (fc._FOOTER_H - tile.height) // 2
    probe = (right - tile.width // 2, tile_top + 2)  # inside the QR's quiet zone

    with_link = as_image(fc.generate_flex_card(WIN, Decimal("15.60"), referral_link=LINK))
    without = as_image(fc.generate_flex_card(WIN, Decimal("15.60")))

    assert min(with_link.convert("RGB").getpixel(probe)) > 200  # light QR tile
    assert max(without.convert("RGB").getpixel(probe)) < 80  # dark glass panel


def test_a_long_emoji_title_still_renders():
    nasty = ResolvedPosition(
        token_id="9", title="\U0001F525" * 10 + " Will " + "x" * 200 + " happen?",
        shares=Decimal("1"), avg_price=Decimal("0.5"), final_price=Decimal("1"),
    )
    assert as_image(fc.generate_flex_card(nasty, Decimal("0.50"))).size == (fc.WIDTH, fc.HEIGHT)


def test_font_is_vendored_with_its_license():
    assert fc.FONT_PATH.is_file()
    assert "SIL Open Font License" in (fc.FONT_PATH.parent / "OFL.txt").read_text()


# -- hold time from the orders table ------------------------------------------------------


async def test_held_duration_counts_from_the_first_filled_buy(session_factory):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    first = now.replace(tzinfo=None) - timedelta(hours=5)

    def order(n, *, token="111", user=7, side="BUY", status="filled", hours_ago):
        return Order(
            telegram_user_id=user, telegram_update_id=70_000 + n,
            idempotency_key=f"held-{n}", token_id=token, market_title="m",
            side=side, amount=Decimal("10"), status=status,
            created_at=now.replace(tzinfo=None) - timedelta(hours=hours_ago),
        )

    async with session_factory() as session:
        session.add(User(telegram_user_id=7))
        session.add(User(telegram_user_id=8))
        session.add(order(1, hours_ago=5))  # the first fill: counts
        session.add(order(2, hours_ago=2))  # a later add-on buy
        session.add(order(3, hours_ago=9, status="rejected"))  # never filled
        session.add(order(4, hours_ago=9, side="SELL"))  # not an entry
        session.add(order(5, hours_ago=9, token="999"))  # other market
        session.add(order(6, hours_ago=9, user=8))  # other user
        await session.commit()

    held = await fc.held_duration(session_factory, 7, "111", now=now)
    assert held == now - first.replace(tzinfo=UTC)

    assert await fc.held_duration(session_factory, 7, "never-traded", now=now) is None
