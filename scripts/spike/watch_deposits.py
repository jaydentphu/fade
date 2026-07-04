"""Spike task 4: watch a Polygon address for incoming USDC transfers.

Usage:
    uv run python scripts/spike/watch_deposits.py 0xYourAddress [--interval 30]
        [--confirmations 12] [--lookback 2000]

Environment (shell env or .env):
    POLYGON_RPC_URL   defaults to https://1rpc.io/matic

Watches both collateral tokens: USDC.e (bridged — what Polymarket uses) and native
USDC. A transfer is reported as PENDING when first seen and CONFIRMED once it has
the requested number of confirmations (D-8: 12). Plain JSON-RPC over httpx; no
third-party indexer dependency.
"""

from __future__ import annotations

import argparse
import os
import time
from decimal import Decimal
from typing import Any

import httpx
from dotenv import load_dotenv

TOKENS = {
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
}
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDC_DECIMALS = Decimal(10) ** 6
MAX_LOG_RANGE = 50  # keyless public RPCs cap eth_getLogs ranges hard (1rpc.io: 50 blocks)


class Rpc:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client = httpx.Client(timeout=30)
        self._id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        response = self._client.post(
            self._url,
            json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(f"RPC error from {method}: {payload['error']}")
        return payload["result"]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def transfers_to(self, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
        padded = "0x" + address.lower().removeprefix("0x").rjust(64, "0")
        logs: list[dict[str, Any]] = []
        start = from_block
        while start <= to_block:
            end = min(start + MAX_LOG_RANGE - 1, to_block)
            logs.extend(
                self.call(
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "address": list(TOKENS),
                            "topics": [TRANSFER_TOPIC, None, padded],
                        }
                    ],
                )
            )
            start = end + 1
        return logs


def describe(log: dict[str, Any]) -> tuple[str, str]:
    token = TOKENS.get(log["address"].lower(), log["address"])
    amount = Decimal(int(log["data"], 16)) / USDC_DECIMALS
    sender = "0x" + log["topics"][1][-40:]
    return f"{amount} {token} from {sender}", log["transactionHash"]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="Polygon address to watch")
    parser.add_argument("--interval", type=int, default=30, help="poll interval in seconds")
    parser.add_argument("--confirmations", type=int, default=12)
    parser.add_argument("--lookback", type=int, default=200, help="blocks to scan on startup")
    parser.add_argument("--once", action="store_true", help="single scan, then exit")
    args = parser.parse_args()

    rpc = Rpc(os.environ.get("POLYGON_RPC_URL", "https://1rpc.io/matic"))
    head = rpc.block_number()
    scanned_to = head - args.lookback
    pending: dict[str, tuple[int, str]] = {}  # tx hash -> (block, description)
    print(f"Watching {args.address} for USDC transfers from block {scanned_to} (head {head}).")
    print(f"Confirmations required: {args.confirmations}. Ctrl+C to stop.")

    while True:
        try:
            head = rpc.block_number()
            if head > scanned_to:
                for log in rpc.transfers_to(args.address, scanned_to + 1, head):
                    text, tx = describe(log)
                    block = int(log["blockNumber"], 16)
                    if tx not in pending:
                        pending[tx] = (block, text)
                        print(f"PENDING   {text}  (block {block}, tx {tx})")
                scanned_to = head
            for tx, (block, text) in list(pending.items()):
                if head - block + 1 >= args.confirmations:
                    print(f"CONFIRMED {text}  (tx {tx})")
                    del pending[tx]
        except (httpx.HTTPError, RuntimeError) as error:
            print(f"RPC hiccup, retrying next tick: {error}")
        if args.once:
            if pending:
                print(f"({len(pending)} transfer(s) still below {args.confirmations} confs)")
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
