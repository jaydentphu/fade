"""Backend credential for gasless relayer operations.

Two kinds exist (verified live 2026-07-04):
- RelayerApiKey (UUID + owner address): only authorizes gasless transactions
  from the key owner's OWN signer. Deploying another user's wallet fails with
  "from <user signer> does not match auth <key address>".
- BuilderApiKey (HMAC key/secret/passphrase from the builder dashboard):
  authorizes relaying on behalf of the builder's users — what Fade needs.

We use the Builder API key when configured and fall back to the Relayer key
(useful only for single-user/dev scenarios).
"""

from __future__ import annotations

from polymarket.auth import ApiKey, BuilderApiKey, RelayerApiKey

from fade.config import Settings


def build_relayer_api_key(settings: Settings) -> ApiKey:
    if settings.builder_api_key:
        return BuilderApiKey(
            key=settings.builder_api_key,
            secret=settings.builder_api_secret,
            passphrase=settings.builder_api_passphrase,
        )
    return RelayerApiKey(
        key=settings.relayer_api_key,
        address=settings.relayer_api_key_address,
    )
