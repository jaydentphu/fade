"""Key-material isolation audit (CLAUDE.md: the signing module is the ONLY code
that touches key material; nothing else imports it except via the public interface).

Two proofs:
1. Static: no module outside fade/wallets imports fade.wallets.signing.
2. Behavioral: WalletService's public surface never returns raw key material —
   with the single documented exception of export_key (PRD F-5, the user's own
   exit right, gated behind the /export warning flow).
"""

from __future__ import annotations

import ast
from pathlib import Path

import fade
import fade.wallets as wallets_pkg
from fade.wallets import WalletService

SRC_ROOT = Path(fade.__file__).parent


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_no_module_outside_wallets_imports_the_signing_module():
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        if "wallets" in path.parts or "migrations" in path.parts:
            continue
        for imported in _imports_of(path):
            if "wallets.signing" in imported:
                offenders.append(str(path))
    assert offenders == [], f"signing module imported outside fade/wallets: {offenders}"


class CapturingFakeClient:
    """Records the private key the SDK would receive, so we can prove the
    public surface never hands it back out."""

    captured_keys: list[str] = []

    def __init__(self) -> None:
        self.wallet = "0xFadE0000000000000000000000000000000000aa"

    @classmethod
    async def create(cls, **kwargs):
        cls.captured_keys.append(kwargs["private_key"])
        return cls()

    async def close(self) -> None:
        pass


async def test_public_interface_never_returns_raw_key_material(
    settings, session_factory, monkeypatch
):
    CapturingFakeClient.captured_keys = []
    monkeypatch.setattr(wallets_pkg, "AsyncSecureClient", CapturingFakeClient)
    service = WalletService(settings, session_factory)

    address = await service.create_wallet(2001)
    stored_address = await service.get_wallet_address(2001)
    key = CapturingFakeClient.captured_keys[0]

    # The generated key exists and is key-shaped, but no public return value
    # contains it in any form.
    assert key.startswith("0x") and len(key) == 66
    for value in (address, stored_address):
        assert key not in str(value)
        assert key.removeprefix("0x") not in str(value)

    # The scoped-client context yields a client object, never the key string.
    async with service.secure_client(2001) as client:
        assert not isinstance(client, str)
        assert key not in repr(client)

    # The ONE sanctioned exception: export_key (PRD F-5), used only by the
    # /export handler behind its warning flow.
    assert await service.export_key(2001) == key
    await service.close()


async def test_encrypted_at_rest_round_trip_with_env_key(settings, tmp_path):
    """Signing credentials are Fernet-encrypted with the env-provided key and
    survive a full store round-trip (goal proof #1, at-rest encryption)."""
    from fade.wallets.signing import SigningStore

    store = SigningStore(settings.signing_database_url, settings.fade_signing_key)
    key = await store.create_signer(2002)
    assert await store.get_private_key(2002) == key
    await store.close()

    # On disk: present only in encrypted form.
    raw = (tmp_path / "signing.db").read_bytes()
    assert key.encode() not in raw
    assert key.removeprefix("0x").encode() not in raw

    # A store opened with a DIFFERENT env key cannot decrypt.
    import pytest
    from cryptography.fernet import Fernet, InvalidToken

    wrong = SigningStore(settings.signing_database_url, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        await wrong.get_private_key(2002)
    await wrong.close()
