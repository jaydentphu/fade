"""Spike task 2: fetch a market by Polymarket URL, print token IDs and prices.

Usage:
    uv run python scripts/spike/fetch_market.py https://polymarket.com/event/<slug>

Uses only public endpoints — no credentials required.
"""

from __future__ import annotations

import sys

from polymarket import PublicClient
from polymarket.errors import UnexpectedResponseError


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    url = sys.argv[1]

    with PublicClient() as client:
        try:
            market = client.get_market(url=url)
        except UnexpectedResponseError as error:
            print(f"Not a binary market (multi-outcome events are out of scope for v1): {error}")
            raise SystemExit(2) from error

    if market.state.closed:
        status = "closed"
    elif market.state.active:
        status = "open"
    else:
        status = "inactive"

    print(f"Question:     {market.question}")
    print(f"Slug:         {market.slug}")
    print(f"Market ID:    {market.id}")
    print(f"Condition ID: {market.condition_id}")
    print(f"Status:       {status}")
    if market.state.end_date is not None:
        print(f"Ends:         {market.state.end_date.isoformat()}")
    for outcome in (market.outcomes.yes, market.outcomes.no):
        price = f"{outcome.price}" if outcome.price is not None else "?"
        print(f"  {outcome.label:>4}: price={price}  token_id={outcome.token_id}")


if __name__ == "__main__":
    main()
