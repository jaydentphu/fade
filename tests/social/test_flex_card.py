"""Flex card (PRD F-23) data pipeline and image rendering.

Pixel-level output isn't practically unit-testable, so the data pipeline
(build_flex_card_data) is tested for exact-value correctness, and the
renderer is only tested for producing a valid, non-empty, decodable image.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from PIL import Image

from fade.bot.handlers.resolutions import ResolvedPosition
from fade.social.flex_card import (
    FlexCardData,
    build_flex_card_data,
    generate_flex_card,
    render_flex_card_image,
)

WIN = ResolvedPosition(
    token_id="111",
    title="Will the Fed cut rates in September?",
    shares=Decimal("40"),
    avg_price=Decimal("0.61"),
    final_price=Decimal("1"),
    outcome="Yes",
)


def test_build_flex_card_data_matches_input_exactly():
    data = build_flex_card_data(WIN, pnl=Decimal("15.60"))

    assert data.title == "Will the Fed cut rates in September?"
    assert data.side_label == "YES"
    assert data.entry_price_label == "$0.61"
    assert data.exit_price_label == "$1.00"
    assert data.pnl_dollar_label == "+$15.60"
    # (15.60 / (0.61 * 40)) * 100 = 63.9...%
    assert data.pnl_percent_label == "+63.9%"


def test_build_flex_card_data_sanitizes_untrusted_title():
    dirty = ResolvedPosition(
        token_id="222",
        title="`evil` [markdown](inject)\nnewline",
        shares=Decimal("10"),
        avg_price=Decimal("0.50"),
        final_price=Decimal("1"),
    )
    data = build_flex_card_data(dirty, pnl=Decimal("5.00"))

    assert "`" not in data.title
    assert "[" not in data.title and "]" not in data.title
    assert "\n" not in data.title


def test_build_flex_card_data_defaults_side_label_when_outcome_missing():
    no_outcome = ResolvedPosition(
        token_id="333",
        title="Some market",
        shares=Decimal("10"),
        avg_price=Decimal("0.50"),
        final_price=Decimal("1"),
    )
    data = build_flex_card_data(no_outcome, pnl=Decimal("5.00"))

    assert data.side_label == ""


def test_render_flex_card_image_returns_valid_nonempty_image():
    data = FlexCardData(
        title="Will the Fed cut rates in September?",
        side_label="YES",
        entry_price_label="$0.61",
        exit_price_label="$1.00",
        pnl_dollar_label="+$15.60",
        pnl_percent_label="+63.9%",
    )
    image_bytes = render_flex_card_image(data)

    assert len(image_bytes) > 0
    img = Image.open(BytesIO(image_bytes))
    img.verify()
    assert img.format == "PNG"


def test_generate_flex_card_full_pipeline_returns_valid_image():
    image_bytes = generate_flex_card(WIN, pnl=Decimal("15.60"))

    img = Image.open(BytesIO(image_bytes))
    img.verify()
    assert img.format == "PNG"
    assert len(image_bytes) > 0
