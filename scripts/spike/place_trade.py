"""Spike task 3: place one small FOK market order with builder attribution, then sell it back.

THIS SPENDS REAL MONEY. Keep --amount small ($1-2).

Usage:
    uv run python scripts/spike/place_trade.py <polymarket-url> [--side yes|no]
        [--amount 1.0] [--slippage 2.0] [--keep] [--skip-confirm]

Required environment (shell env or .env — .env is gitignored):
    POLYMARKET_PRIVATE_KEY   your exported Polymarket signing key (never logged, never stored)
    FADE_BUILDER_CODE        the Fade builder code (public bytes32)
Optional:
    POLYMARKET_WALLET        funds wallet to act for; defaults to the signer's deposit wallet

The builder code is attached to both orders; the SDK serializes it into the signed
order's `builder` field, which is how Polymarket attributes volume and fees to Fade.
Output is plain and precise: this is a money screen.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from dotenv import load_dotenv
from polymarket import PublicClient, SecureClient
from polymarket.errors import UnexpectedResponseError
from polymarket.models.clob import AcceptedOrder, RejectedOrder

CENT = Decimal("0.01")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Polymarket market URL")
    parser.add_argument("--side", choices=["yes", "no"], default="yes")
    parser.add_argument("--amount", type=Decimal, default=Decimal("1.0"), help="USDC to spend")
    parser.add_argument(
        "--slippage", type=Decimal, default=Decimal("2.0"), help="max slippage in percent"
    )
    parser.add_argument("--keep", action="store_true", help="do not sell the position back")
    parser.add_argument("--skip-confirm", action="store_true")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"Error: {name} is not set.")
        raise SystemExit(1)
    return value


def show_fill(label: str, order: AcceptedOrder | RejectedOrder, side: str) -> AcceptedOrder:
    if isinstance(order, RejectedOrder):
        print(f"{label} rejected ({order.code}): {order.message}")
        raise SystemExit(3)
    if side == "BUY":
        usdc, shares = order.making_amount, order.taking_amount
    else:
        usdc, shares = order.taking_amount, order.making_amount
    avg = (usdc / shares).quantize(Decimal("0.0001")) if shares else Decimal(0)
    print(f"{label} {order.status}: {shares} shares at avg {avg} for {usdc} USDC")
    print(f"  order_id: {order.order_id}")
    for tx in order.transactions_hashes:
        print(f"  tx: https://polygonscan.com/tx/{tx}")
    return order


def main() -> None:
    load_dotenv()
    args = parse_args()
    if args.amount > Decimal("5"):
        print("Refusing to spend more than $5 in a spike script. Lower --amount.")
        raise SystemExit(1)

    private_key = require_env("POLYMARKET_PRIVATE_KEY")
    builder_code = require_env("FADE_BUILDER_CODE")
    wallet = os.environ.get("POLYMARKET_WALLET") or None
    slip = args.slippage / Decimal(100)

    with PublicClient() as public:
        try:
            market = public.get_market(url=args.url)
        except UnexpectedResponseError as error:
            print(f"Not a binary market: {error}")
            raise SystemExit(2) from error
    outcome = market.outcomes.yes if args.side == "yes" else market.outcomes.no
    if outcome.token_id is None:
        print("Market has no token id for that outcome; is it tradeable?")
        raise SystemExit(2)
    print(f"Market: {market.question}")
    print(f"Buying {outcome.label} with {args.amount} USDC (max slippage {args.slippage}%)")

    if not args.skip_confirm:
        answer = input("This places a real order with real funds. Type 'go' to proceed: ")
        if answer.strip().lower() != "go":
            print("Aborted. No order was placed.")
            raise SystemExit(0)

    print("Authenticating with the CLOB (derives API credentials; key stays in memory)...")
    with SecureClient.create(private_key=private_key, wallet=wallet) as client:
        rates = client.get_builder_fee_rates(builder_code)
        print(f"Builder fee rates for this code: taker={rates.taker} maker={rates.maker}")

        est_buy = client.estimate_market_price(
            token_id=outcome.token_id, side="BUY", amount=args.amount, order_type="FOK"
        )
        max_price = min((est_buy * (1 + slip)).quantize(CENT, rounding=ROUND_UP), Decimal("0.99"))
        print(f"Estimated buy price: {est_buy}; worst-price limit: {max_price}")

        buy = show_fill(
            "BUY",
            client.place_market_order(
                token_id=outcome.token_id,
                side="BUY",
                amount=args.amount,
                max_price=max_price,
                order_type="FOK",
                builder_code=builder_code,
            ),
            "BUY",
        )

        if args.keep:
            print("Keeping the position (--keep). Done.")
            return

        shares = buy.taking_amount
        est_sell = client.estimate_market_price(
            token_id=outcome.token_id, side="SELL", shares=shares, order_type="FOK"
        )
        min_price = max((est_sell * (1 - slip)).quantize(CENT, rounding=ROUND_DOWN), CENT)
        print(f"Selling {shares} shares back. Estimated price: {est_sell}; floor: {min_price}")

        show_fill(
            "SELL",
            client.place_market_order(
                token_id=outcome.token_id,
                side="SELL",
                shares=shares,
                min_price=min_price,
                order_type="FOK",
                builder_code=builder_code,
            ),
            "SELL",
        )

    print(
        "Round trip complete. Verify attribution at polymarket.com/settings?tab=builder "
        "(volume credits can lag a few minutes)."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Check open orders before re-running.")
        sys.exit(130)
