"""Signing store: encryption round-trip and separation from the main app DB."""

from __future__ import annotations

import pytest

from fade.wallets.signing import SigningStore


async def test_create_and_retrieve_round_trip(settings):
    store = SigningStore(settings.signing_database_url, settings.fade_signing_key)
    created = await store.create_signer(101)
    loaded = await store.get_private_key(101)
    assert created == loaded
    assert created.startswith("0x") and len(created) == 66
    await store.close()


async def test_create_is_idempotent(settings):
    store = SigningStore(settings.signing_database_url, settings.fade_signing_key)
    first = await store.create_signer(102)
    second = await store.create_signer(102)
    assert first == second
    await store.close()


async def test_missing_signer_raises(settings):
    store = SigningStore(settings.signing_database_url, settings.fade_signing_key)
    await store._ensure_schema()
    with pytest.raises(KeyError):
        await store.get_private_key(999)
    await store.close()


async def test_key_is_encrypted_at_rest_in_separate_file(settings, tmp_path):
    store = SigningStore(settings.signing_database_url, settings.fade_signing_key)
    key = await store.create_signer(103)
    await store.close()

    signing_file = tmp_path / "signing.db"
    main_file = tmp_path / "main.db"
    assert signing_file.exists()
    raw = signing_file.read_bytes()
    # Plaintext key must not appear in the store file (it is Fernet-encrypted).
    assert key.encode() not in raw
    assert key.removeprefix("0x").encode() not in raw
    # And the signing store must not be the main application database.
    assert not main_file.exists() or b"signer_keys" not in main_file.read_bytes()
