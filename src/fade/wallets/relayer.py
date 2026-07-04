"""Shared Relayer API key for gasless deposit-wallet deployment.

One backend-wide credential (Fade's own, from env), reused for every user —
verified against the SDK: it authenticates our backend to Polymarket's relayer
via static headers regardless of which user's wallet is being acted on.
"""

from __future__ import annotations

from polymarket.auth import RelayerApiKey

from fade.config import Settings


def build_relayer_api_key(settings: Settings) -> RelayerApiKey:
    return RelayerApiKey(
        key=settings.relayer_api_key,
        address=settings.relayer_api_key_address,
    )
