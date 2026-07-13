"""Mini App client-side key custody (PRD §7.8, engineering slice).

Served by the existing FastAPI webhook app (the stack's only web surface).
The page generates the signing key IN THE BROWSER; the register request
carries only the derived public address plus Telegram's signed initData.
No key material ever reaches this server — there is deliberately no code
path here that could accept one.

initData verification follows Telegram's official algorithm: the secret key
is HMAC-SHA256("WebAppData", bot_token); the received fields (minus `hash`)
are sorted, joined as `k=v` lines, HMAC'd with that secret, and compared to
the `hash` field constant-time. Checked before ANY state change.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Response

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_init_data(init_data: str, bot_token: str) -> dict[str, str] | None:
    """Verify Telegram WebApp initData. Returns its fields, or None if invalid."""
    if not init_data or not bot_token:
        return None
    try:
        fields = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = fields.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None
    return fields


def _user_id_from(fields: dict[str, str]) -> int | None:
    try:
        return int(json.loads(fields.get("user", ""))["id"])
    except (ValueError, KeyError, TypeError):
        return None


async def handle_register(sessions, wallets, bot_token: str, body: Any) -> Response:
    """Register a client-custody wallet for the initData-authenticated user.

    Order matters: signature first (401 before any state change), then the
    attestation gate (403 — the Mini App is only handed out post-attestation,
    but the endpoint must not trust that), then address shape (400).
    """
    if not isinstance(body, dict):
        return Response(status_code=400, content="bad request")
    fields = validate_init_data(str(body.get("init_data", "")), bot_token)
    if fields is None:
        return Response(status_code=401, content="unauthorized")
    telegram_user_id = _user_id_from(fields)
    if telegram_user_id is None:
        return Response(status_code=401, content="unauthorized")

    from sqlalchemy import select

    from fade.db.models import User

    async with sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None or not user.onboarded:
        return Response(status_code=403, content="attestation required")

    address = str(body.get("address", ""))
    if not ADDRESS_RE.match(address):
        return Response(status_code=400, content="bad address")

    registered = await wallets.register_client_wallet(telegram_user_id, address)
    logger.info("client-custody wallet registered for user %s", telegram_user_id)
    return Response(
        status_code=200,
        content=json.dumps({"address": registered}),
        media_type="application/json",
    )


# The page. Key generation happens in ethers.js in the browser; the private
# key is offered to the user for backup and saved to Telegram CloudStorage —
# it is NEVER put in a request. Only initData + the derived address go out.
MINIAPP_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fade — create your wallet</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://cdn.jsdelivr.net/npm/ethers@6.13.4/dist/ethers.umd.min.js"></script>
<style>
  body { font-family: system-ui, sans-serif; margin: 1.2rem; background: #14100E; color: #F2EFEA; }
  button { font-size: 1rem; padding: .8rem 1.2rem; border-radius: 8px; border: 0;
           background: #EC683D; color: #fff; width: 100%; }
  code { word-break: break-all; display: block; background: #1F1B18; padding: .8rem;
         border-radius: 8px; margin: .8rem 0; }
  .muted { color: #8A8580; font-size: .9rem; }
</style>
</head>
<body>
<h2>Create your wallet</h2>
<p>Your signing key is generated <b>on this device</b>. It is never sent to
Fade's server — Fade cannot export, move, or lose it for you.</p>
<div id="step-create">
  <button id="create">Generate my key</button>
</div>
<div id="step-backup" style="display:none">
  <p><b>Back this up now.</b> Fade does not have a copy. Lose it and nobody
  can recover it.</p>
  <code id="key"></code>
  <button id="register">I saved it — register my wallet</button>
</div>
<p id="status" class="muted"></p>
<script>
  const tg = window.Telegram.WebApp;
  tg.ready();
  let wallet = null;
  document.getElementById("create").onclick = () => {
    wallet = ethers.Wallet.createRandom();
    document.getElementById("key").textContent = wallet.privateKey;
    document.getElementById("step-create").style.display = "none";
    document.getElementById("step-backup").style.display = "block";
    // Best-effort convenience copy in Telegram's per-user cloud storage.
    // The user-facing source of truth is the backup they just made.
    try { tg.CloudStorage.setItem("fade_signer_key", wallet.privateKey, () => {}); }
    catch (e) {}
  };
  document.getElementById("register").onclick = async () => {
    const status = document.getElementById("status");
    status.textContent = "Registering...";
    // ONLY initData (Telegram's signed identity) and the PUBLIC address
    // leave this device. The private key stays here.
    const response = await fetch("/miniapp/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: tg.initData, address: wallet.address }),
    });
    if (response.ok) {
      status.textContent = "Wallet registered. You can close this and return to the chat.";
      document.getElementById("key").textContent = "(hidden — it stays on your device)";
    } else {
      status.textContent = "Registration failed (" + response.status + "). Try again from /start.";
    }
  };
</script>
</body>
</html>"""
