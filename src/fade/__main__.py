"""Dev entrypoint: long polling. Prod will use webhook mode behind FastAPI."""

from __future__ import annotations

from fade.bot.app import build_application
from fade.config import load_settings


def main() -> None:
    settings = load_settings()
    app = build_application(settings)
    print("Fade bot starting (polling mode). Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
