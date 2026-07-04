"""Polymarket bridge API: personal deposit addresses (and later, withdrawals).

Public data only — no key material passes through this module.
"""

from __future__ import annotations

import httpx

BRIDGE_URL = "https://bridge.polymarket.com"


class BridgeError(RuntimeError):
    pass


# Native USDC on Polygon: the default withdrawal destination token.
USDC_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
POLYGON_CHAIN_ID = "137"


async def fetch_deposit_addresses(wallet_address: str, builder_code: str) -> dict[str, str]:
    """Return the per-network bridge deposit addresses for a deposit wallet."""
    payload = {"address": wallet_address}
    addresses = (await _bridge_post("/deposit", payload, builder_code)).get("address", {})
    if not isinstance(addresses, dict) or not addresses:
        raise BridgeError("Bridge /deposit returned no addresses")
    return addresses


async def fetch_withdrawal_address(
    wallet_address: str,
    recipient: str,
    builder_code: str,
    to_chain_id: str = POLYGON_CHAIN_ID,
    to_token: str = USDC_POLYGON,
) -> str:
    """Register a withdrawal and return the EVM bridge address to send pUSD to."""
    payload = {
        "address": wallet_address,
        "toChainId": to_chain_id,
        "toTokenAddress": to_token,
        "recipientAddr": recipient,
    }
    data = await _bridge_post("/withdraw", payload, builder_code)
    evm = data.get("address", {}).get("evm")
    if not evm:
        raise BridgeError("Bridge /withdraw returned no EVM address")
    return evm


async def _bridge_post(path: str, payload: dict[str, str], builder_code: str) -> dict:
    headers = {"X-Builder-Code": builder_code} if builder_code else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{BRIDGE_URL}{path}", json=payload, headers=headers)
    if response.status_code not in (200, 201):
        raise BridgeError(f"Bridge {path} returned {response.status_code}")
    return response.json()
