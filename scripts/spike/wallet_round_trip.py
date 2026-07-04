"""Spike milestone 3: deposit/withdraw round-trip on a test deposit wallet.

Polymarket V2 wallets hold pUSD (Polymarket's collateral token), not raw USDC.
Deposits: get a personal bridge address, send USDC (any supported chain) to it,
pUSD arrives in the deposit wallet. Withdrawals: request a withdrawal bridge
address for a destination chain/token/recipient, then transfer pUSD to it.

Usage:
    uv run python scripts/spike/wallet_round_trip.py info
    uv run python scripts/spike/wallet_round_trip.py deposit-address
    uv run python scripts/spike/wallet_round_trip.py withdraw --amount 1.0 --to 0xRecipient
        [--chain 137] [--token 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359] [--skip-confirm]

Required environment (shell env for the key; .env for the rest):
    POLYMARKET_PRIVATE_KEY   signing key for the deposit wallet (never logged, never stored)
    RELAYER_API_KEY          gasless relayer credentials (see .env)
    RELAYER_API_KEY_ADDRESS
Optional:
    FADE_BUILDER_CODE        attached to bridge requests as X-Builder-Code
    POLYMARKET_WALLET        wallet to act for; defaults to the signer's deposit wallet

Output is plain and precise: this is a money screen.
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal

import httpx
from dotenv import load_dotenv
from polymarket import SecureClient
from polymarket.auth import RelayerApiKey
from polymarket.environments import PRODUCTION

BRIDGE_URL = "https://bridge.polymarket.com"
USDC_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
PUSD_DECIMALS = Decimal(10) ** 6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="show deposit wallet address and pUSD balance")
    sub.add_parser("deposit-address", help="get personal bridge addresses for deposits")
    withdraw = sub.add_parser("withdraw", help="withdraw pUSD as USDC to an external address")
    withdraw.add_argument("--amount", type=Decimal, required=True, help="pUSD amount to withdraw")
    withdraw.add_argument("--to", required=True, help="recipient address on destination chain")
    withdraw.add_argument("--chain", default="137", help="destination chain id (default Polygon)")
    withdraw.add_argument("--token", default=USDC_POLYGON, help="destination token address")
    withdraw.add_argument("--skip-confirm", action="store_true")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"Error: {name} is not set.")
        raise SystemExit(1)
    return value


def make_client() -> SecureClient:
    private_key = require_env("POLYMARKET_PRIVATE_KEY")
    api_key = RelayerApiKey(
        key=require_env("RELAYER_API_KEY"),
        address=require_env("RELAYER_API_KEY_ADDRESS"),
    )
    wallet = os.environ.get("POLYMARKET_WALLET") or None
    print("Authenticating (key stays in memory)...")
    return SecureClient.create(private_key=private_key, wallet=wallet, api_key=api_key)


def bridge_post(path: str, payload: dict[str, str]) -> dict:
    headers = {}
    builder_code = os.environ.get("FADE_BUILDER_CODE")
    if builder_code:
        headers["X-Builder-Code"] = builder_code
    response = httpx.post(f"{BRIDGE_URL}{path}", json=payload, headers=headers, timeout=30)
    if response.status_code not in (200, 201):
        print(f"Bridge API error {response.status_code}: {response.text}")
        raise SystemExit(3)
    return response.json()


def show_info(client: SecureClient) -> None:
    balance = client.get_balance_allowance(asset_type="COLLATERAL")
    print(f"Deposit wallet: {client.wallet}")
    print(f"Wallet type:    {client.wallet_type}")
    print(f"pUSD balance:   {Decimal(balance.balance) / PUSD_DECIMALS}")


def show_deposit_address(client: SecureClient) -> None:
    data = bridge_post("/deposit", {"address": str(client.wallet)})
    addresses = data.get("address", {})
    print(f"Deposit wallet: {client.wallet}")
    print("Send USDC to YOUR personal bridge address; it arrives as pUSD:")
    for network, addr in addresses.items():
        print(f"  {network}: {addr}")
    if note := data.get("note"):
        print(f"Note: {note}")


def withdraw(client: SecureClient, args: argparse.Namespace) -> None:
    base_units = int(args.amount * PUSD_DECIMALS)
    balance = client.get_balance_allowance(asset_type="COLLATERAL")
    if base_units > balance.balance:
        print(
            f"Insufficient balance: have {Decimal(balance.balance) / PUSD_DECIMALS} pUSD, "
            f"asked to withdraw {args.amount}."
        )
        raise SystemExit(1)

    data = bridge_post(
        "/withdraw",
        {
            "address": str(client.wallet),
            "toChainId": args.chain,
            "toTokenAddress": args.token,
            "recipientAddr": args.to,
        },
    )
    bridge_addr = data.get("address", {}).get("evm")
    if not bridge_addr:
        print(f"Bridge API did not return an EVM address: {data}")
        raise SystemExit(3)

    print(f"Withdrawing {args.amount} pUSD -> {args.token} on chain {args.chain}")
    print(f"Recipient: {args.to}")
    print(f"Via bridge address: {bridge_addr}")
    if not args.skip_confirm:
        answer = input("This moves real funds. Type 'go' to proceed: ")
        if answer.strip().lower() != "go":
            print("Aborted. No funds moved.")
            raise SystemExit(0)

    handle = client.transfer_erc20(
        token_address=PRODUCTION.collateral_token,
        recipient_address=bridge_addr,
        amount=base_units,
        metadata=f"Withdraw {args.amount} pUSD to {args.to}",
    )
    print("Submitted (gasless). Waiting for confirmation...")
    outcome = handle.wait()
    print(f"Confirmed: https://polygonscan.com/tx/{outcome.transaction_hash}")
    print("The bridge delivers the destination token to the recipient; timing varies.")
    balance = client.get_balance_allowance(asset_type="COLLATERAL")
    print(f"New pUSD balance: {Decimal(balance.balance) / PUSD_DECIMALS}")


def main() -> None:
    load_dotenv()
    args = parse_args()
    with make_client() as client:
        if args.command == "info":
            show_info(client)
        elif args.command == "deposit-address":
            show_deposit_address(client)
        elif args.command == "withdraw":
            withdraw(client, args)


if __name__ == "__main__":
    main()
