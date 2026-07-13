"""Market lookup and view models (PRD F-8, F-9). Binary markets only (D-4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

POLYMARKET_URL_RE = re.compile(r"https?://(?:www\.)?polymarket\.com/\S+", re.IGNORECASE)


class MarketLookupError(RuntimeError):
    """Market missing, closed, or not a binary market."""


@dataclass(frozen=True)
class MarketCard:
    market_id: str
    question: str
    slug: str
    yes_token: str
    no_token: str
    yes_price: Decimal | None
    no_price: Decimal | None
    end_date: str | None
    open: bool
    # Polymarket's own category label (F-9 "browsable by category"); None when
    # the market data doesn't carry one. Default keeps cached cards revivable.
    category: str | None = None


def extract_polymarket_url(text: str) -> str | None:
    match = POLYMARKET_URL_RE.search(text or "")
    return match.group(0) if match else None


def market_to_card(market) -> MarketCard:
    """Convert an SDK Market model to our card, enforcing tradeability."""
    yes, no = market.outcomes.yes, market.outcomes.no
    if yes.token_id is None or no.token_id is None:
        raise MarketLookupError("Market has no tradeable tokens.")
    is_open = bool(market.state.active) and not bool(market.state.closed)
    end = market.state.end_date.date().isoformat() if market.state.end_date else None
    return MarketCard(
        market_id=str(market.id),
        question=market.question or "(untitled market)",
        slug=market.slug or "",
        yes_token=str(yes.token_id),
        no_token=str(no.token_id),
        yes_price=yes.price,
        no_price=no.price,
        end_date=end,
        open=is_open,
        category=getattr(market, "category", None),
    )
