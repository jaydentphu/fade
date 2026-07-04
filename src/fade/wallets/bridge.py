"""Polymarket bridge API: personal deposit addresses (and later, withdrawals).

Public data only — no key material passes through this module.
"""

from __future__ import annotations

import httpx

BRIDGE_URL = "https://bridge.polymarket.com"


class BridgeError(RuntimeError):
    pass


async def fetch_deposit_addresses(wallet_address: str, builder_code: str) -> dict[str, str]:
    """Return the per-network bridge deposit addresses for a deposit wallet."""
    headers = {"X-Builder-Code": builder_code} if builder_code else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{BRIDGE_URL}/deposit", json={"address": wallet_address}, headers=headers
        )
    if response.status_code not in (200, 201):
        raise BridgeError(f"Bridge /deposit returned {response.status_code}")
    addresses = response.json().get("address", {})
    if not isinstance(addresses, dict) or not addresses:
        raise BridgeError("Bridge /deposit returned no addresses")
    return addresses
