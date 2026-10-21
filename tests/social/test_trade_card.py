"""Trade cards (market + broadcast): data pipeline pinned exactly, renderers
checked for valid images, and glyph coverage for every fixed label."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from PIL import Image

from fade.social import flex_card
from fade.social import trade_card as tc
from fade.trading.markets import MarketCard

FED = MarketCard(
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


def as_image(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    img.load()
    return img


# -- market card data ------------------------------------------------------------------


def test_market_card_data_matches_the_market():
    data = tc.build_market_card_data(FED)

    assert data.title == "Will the Fed cut rates in September?"
    assert data.resolves_label == "RESOLVES SEP 20, 2026"
    assert (data.yes_label, data.no_label) == ("61¢", "39¢")
    assert data.odds_label == "61%"
    assert data.yes_share == 0.61


def test_unpriced_market_without_end_date_degrades_cleanly():
    bare = MarketCard("2", "New market?", "new", "1", "2", None, None, None, True)

    data = tc.build_market_card_data(bare)

    assert data.resolves_label == "OPEN MARKET"
    assert (data.yes_label, data.no_label) == ("—", "—")
    assert data.odds_label == "" and data.yes_share is None


def test_garbage_end_date_reads_as_open_market():
    assert tc.resolves_label("not-a-date") == "OPEN MARKET"
    assert tc.resolves_label("2027-01-05") == "RESOLVES JAN 5, 2027"


# -- broadcast card data ---------------------------------------------------------------


def test_broadcast_card_data_puts_the_trader_against_the_open_slot():
    data = tc.build_broadcast_card_data(
        who="priya", question=FED.question, side_label="YES",
        amount_usdc=Decimal("50"), price=Decimal("0.61"), hide_amounts=False,
    )

    assert (data.trader, data.initials) == ("priya", "PR")
    assert (data.side, data.price_label) == ("YES", "61¢")
    assert (data.fade_side, data.fade_price_label) == ("NO", "39¢")  # implied: 1 - 0.61
    assert data.amount_label == "$50"


def test_broadcast_card_respects_the_privacy_toggle():
    data = tc.build_broadcast_card_data(
        who="priya", question=FED.question, side_label="YES",
        amount_usdc=Decimal("50"), price=Decimal("0.61"), hide_amounts=True,
    )
    assert data.amount_label == ""  # F-17: side + market still shown, size never


def test_broadcast_card_handles_no_side_cents_and_odd_names():
    data = tc.build_broadcast_card_data(
        who="\U0001F525[x]", question="Q?", side_label="no",
        amount_usdc=Decimal("24.99"), price=Decimal("0.58"), hide_amounts=False,
    )

    assert (data.side, data.fade_side) == ("NO", "YES")
    assert data.amount_label == "$24.99"
    assert data.fade_price_label == "42¢"
    assert "[" not in data.trader  # sanitized
    assert data.initials == "X"  # emoji can't be drawn; falls to the next letter


# -- glyphs and rendering ----------------------------------------------------------------


def test_every_fixed_trade_card_string_has_glyphs():
    fixed = [
        tc.STATUS_MARKET, tc.STATUS_NEW_POSITION, *tc.MARKET_CTA, *tc.BROADCAST_CTA,
        tc.OPEN_SLOT, tc.UNPRICED_LABEL, "VS", "chance", "of YES", "RESOLVES OPEN MARKET",
        *tc._MONTHS,
        "YES NO 0123456789¢%$,.·—?",
    ]
    missing = {ch for text in fixed for ch in text if not flex_card.has_glyph(ch)}
    assert not missing, f"would render as tofu boxes: {sorted(missing)}"


def test_market_and_broadcast_cards_render_as_landscape_jpegs():
    renders = [
        tc.generate_market_card(FED),
        tc.generate_market_card(
            MarketCard("2", "Unpriced?", "u", "1", "2", None, None, None, True)
        ),
        tc.generate_broadcast_card(
            who="priya", question=FED.question, side_label="YES",
            amount_usdc=Decimal("50"), price=Decimal("0.61"), hide_amounts=False,
        ),
        tc.generate_broadcast_card(
            who="marcus", question="Will it rain?", side_label="NO",
            amount_usdc=None, price=None, hide_amounts=True,
        ),
    ]
    for data in renders:
        img = as_image(data)
        assert img.format == "JPEG"
        assert img.size == (flex_card.WIDTH, flex_card.HEIGHT)
