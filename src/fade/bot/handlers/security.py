"""/security: plain-language disclosure of what Fade can and cannot do with
user funds (PRD §7.9). Post-Polycule, this is a feature, not fine print.

Keep this text in sync with docs/SECURITY.md (the long form).
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

SECURITY_TEXT = (
    "🔒 What Fade can and cannot do with your money\n\n"
    "Your funds live in YOUR wallet on Polygon — never pooled with anyone "
    "else's.\n\n"
    "Fade's backend CAN:\n"
    "• place and cancel orders for you (every order has a worst-price guard)\n"
    "• send withdrawals you explicitly confirm in DM\n"
    "• read your balance and positions to show them to you\n\n"
    "Fade will NEVER, in normal operation:\n"
    "• move funds anywhere you didn't confirm\n"
    "• show your balance or addresses in a group chat\n"
    "• write your keys to logs or the main database — they're encrypted in a "
    "separate store\n\n"
    "The honest part: Fade holds an encrypted signing key for your wallet. "
    "If someone fully compromised our server AND its encryption key, they "
    "could misuse your key. Mitigations: encryption at rest, one small "
    "auditable signing module, an instant kill switch that can pause trading "
    "but can never block withdrawals — and /export, which hands you your own "
    "key so you can leave any time.\n\n"
    "Don't deposit more than you'd bet with friends. Full write-up: "
    "docs/SECURITY.md in the repo."
)


async def security(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(SECURITY_TEXT)
