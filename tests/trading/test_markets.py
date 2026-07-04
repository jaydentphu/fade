"""Market model helpers: URL extraction and card conversion."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from fade.trading.markets import MarketLookupError, extract_polymarket_url, market_to_card


def make_sdk_market(
    active: bool = True,
    closed: bool = False,
    yes_token: str | None = "111",
    no_token: str | None = "222",
):
    return SimpleNamespace(
        id="558943",
        question="Will it rain?",
        slug="will-it-rain",
        state=SimpleNamespace(active=active, closed=closed, end_date=None),
        outcomes=SimpleNamespace(
            yes=SimpleNamespace(label="Yes", token_id=yes_token, price=Decimal("0.60")),
            no=SimpleNamespace(label="No", token_id=no_token, price=Decimal("0.40")),
        ),
    )


def test_extract_polymarket_url_finds_url_in_text():
    text = "check this out https://polymarket.com/event/will-it-rain lol"
    assert extract_polymarket_url(text) == "https://polymarket.com/event/will-it-rain"


def test_extract_polymarket_url_none_for_other_text():
    assert extract_polymarket_url("no links here") is None
    assert extract_polymarket_url("https://example.com/foo") is None


def test_market_to_card_maps_fields():
    card = market_to_card(make_sdk_market())
    assert card.yes_token == "111"
    assert card.no_token == "222"
    assert card.yes_price == Decimal("0.60")
    assert card.open is True


def test_market_to_card_flags_closed_market():
    card = market_to_card(make_sdk_market(active=False, closed=True))
    assert card.open is False


def test_market_to_card_rejects_untradeable_market():
    with pytest.raises(MarketLookupError):
        market_to_card(make_sdk_market(yes_token=None))
