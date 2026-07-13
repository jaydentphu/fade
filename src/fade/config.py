"""Settings loaded from environment variables.

Secrets live in env vars only (.env for local dev, gitignored). Nothing in this
module may log or expose secret values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

from dotenv import load_dotenv

_SECRET_FIELDS = {
    "telegram_bot_token",
    "telegram_webhook_secret",
    "fade_signing_key",
    "relayer_api_key",
    "builder_api_key",
    "builder_api_secret",
    "builder_api_passphrase",
    "sentry_dsn",
}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_user_id: int
    database_url: str
    signing_database_url: str
    fade_signing_key: str
    relayer_api_key: str
    relayer_api_key_address: str
    builder_code: str
    # Builder API key (HMAC triple): required to relay transactions on behalf of
    # OTHER users' signers. The Relayer API key above only covers our own address.
    builder_api_key: str = ""
    builder_api_secret: str = ""
    builder_api_passphrase: str = ""
    polygon_rpc_url: str = "https://1rpc.io/matic"
    # Required in webhook (prod) mode only; polling mode ignores it.
    telegram_webhook_secret: str = ""
    # Per-user rolling-24h BUY volume ceiling (PRD §7.7). 0 disables. See D-12.
    daily_volume_cap_usd: Decimal = Decimal("1000")
    # First-time withdrawal-address cooldown, minutes (PRD §7.3). 0 disables
    # (dev/test default); production should set it — see D-13 and DEPLOYMENT.md.
    withdrawal_cooldown_minutes: int = 0
    # Optional Sentry DSN for error reporting; empty = Sentry off (see F-26
    # monitoring in DEPLOYMENT.md). Requires the "sentry" optional dependency.
    sentry_dsn: str = ""
    # PRD §7.8 Mini App custody (engineering slice). Default OFF: the classic
    # server-side signer flow is byte-identical while this is false.
    miniapp_custody: bool = False
    miniapp_url: str = ""  # public https URL of the Mini App page (required if on)
    attestation_version: str = field(default="2026-07-04")

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        shown = {
            name: ("***" if name in _SECRET_FIELDS else getattr(self, name))
            for name in self.__dataclass_fields__
        }
        inner = ", ".join(f"{k}={v!r}" for k, v in shown.items())
        return f"Settings({inner})"


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it.")
    return value


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        admin_user_id=int(os.environ.get("FADE_ADMIN_USER_ID", "0")),
        database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///fade_dev.db"),
        signing_database_url=os.environ.get(
            "SIGNING_DATABASE_URL", "sqlite+aiosqlite:///signing_dev.db"
        ),
        fade_signing_key=_require("FADE_SIGNING_KEY"),
        relayer_api_key=_require("RELAYER_API_KEY"),
        relayer_api_key_address=_require("RELAYER_API_KEY_ADDRESS"),
        builder_code=_require("FADE_BUILDER_CODE"),
        builder_api_key=os.environ.get("FADE_BUILDER_API_KEY", ""),
        builder_api_secret=os.environ.get("FADE_BUILDER_API_SECRET", ""),
        builder_api_passphrase=os.environ.get("FADE_BUILDER_API_PASSPHRASE", ""),
        polygon_rpc_url=os.environ.get("POLYGON_RPC_URL", "https://1rpc.io/matic"),
        telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        daily_volume_cap_usd=Decimal(os.environ.get("FADE_DAILY_VOLUME_CAP_USD") or "1000"),
        withdrawal_cooldown_minutes=int(os.environ.get("FADE_WITHDRAWAL_COOLDOWN_MINUTES") or "0"),
        sentry_dsn=os.environ.get("SENTRY_DSN", ""),
        miniapp_custody=os.environ.get("FADE_MINIAPP_CUSTODY", "").lower() in ("1", "true", "yes"),
        miniapp_url=os.environ.get("FADE_MINIAPP_URL", ""),
    )
