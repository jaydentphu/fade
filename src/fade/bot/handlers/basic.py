"""Utility handlers: /ping, /help, and the single command registry.

COMMANDS is the one source of truth for the bot's public surface: /help,
onboarding's command list, and Telegram's "/" autocomplete menu
(set_my_commands at startup) are all rendered from it, so they can't drift
apart. Admin commands (/pause, /resume, /stats) are deliberately absent —
they're not for the public menu.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

PING_TEXT = "Still standing. Which is more than your portfolio will be able to say."

# (command, menu description). Order = display order in /help and the menu.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Create your account, or see your overview"),
    ("wallet", "Balance + deposit info (DM)"),
    ("positions", "Open positions, one-tap sell (DM)"),
    ("withdraw", "Cash out to any address (DM)"),
    ("export", "Export your private key (DM)"),
    ("trending", "What's hot on Polymarket right now"),
    ("search", "Find a specific market"),
    ("history", "Your recent orders (DM)"),
    ("slippage", "View or set your worst-price guard (DM)"),
    ("clan", "Your clan: create, invite, kick, customize"),
    ("clans", "Clan leaderboards: today, week, all time"),
    ("setup", "Register this group for trade posts (and claim it for your clan)"),
    ("join", "Get your trades posted in this group"),
    ("leaderboard", "This group's member rankings: weekly + all-time"),
    ("record", "A member's all-time record"),
    ("invite", "Your referral link + recruit count"),
    ("challenge", "Head-to-head wager: /challenge @user <link>"),
    ("privacy", "Hide or show your trade sizes in broadcasts"),
    ("security", "What Fade can and cannot do with your funds"),
    ("help", "This list"),
    ("ping", "Check I'm alive"),
)

COMMANDS_TEXT = (
    "Commands:\n"
    + "\n".join(f"• /{name} — {description}" for name, description in COMMANDS)
    + "\n\nOr just paste a Polymarket link in DM to trade it. In a group, every "
    "member's trade posts with ⚔️ Fade and 📋 Copy buttons."
)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(PING_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help: the current command list. Static text, safe in any chat —
    no balances, no addresses, ever (PRD F-7 group-safety)."""
    if update.message is None:
        return
    await update.message.reply_text(COMMANDS_TEXT)
