"""Settings loaded from environment variables.

Secrets live in env vars only (.env for local dev, gitignored). Nothing in this
module may log or expose secret values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_user_id: int

    def __repr__(self) -> str:  # keep the token out of logs and tracebacks
        return f"Settings(admin_user_id={self.admin_user_id}, telegram_bot_token=***)"


def load_settings() -> Settings:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it.")
    admin_raw = os.environ.get("FADE_ADMIN_USER_ID", "0")
    return Settings(telegram_bot_token=token, admin_user_id=int(admin_raw))
