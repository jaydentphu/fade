"""Shared copy/formatting helpers used by multiple handlers.

Money-adjacent formatting: plain and precise, per brand voice rules.
"""

from __future__ import annotations

from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

GROUP_REDIRECT_TEXT = "Wallets are a DM thing. Tap below and we'll talk in private."

_NETWORK_LABELS = {
    "evm": "Polygon/EVM",
    "svm": "Solana",
    "btc": "Bitcoin",
    "tron": "Tron",
}


def sanitize(text: str, limit: int = 120) -> str:
    """Neutralize untrusted text (market titles, usernames) before rendering.

    Strips characters that alter Markdown parsing or look like commands, and
    truncates. PRD §7.6: untrusted input must never alter rendering/parsing.
    """
    cleaned = (text or "").replace("\n", " ").strip()
    for ch in ("\\", "`", "*", "_", "[", "]"):
        cleaned = cleaned.replace(ch, "")
    cleaned = cleaned.lstrip("/")
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


async def edit_card(query, text: str, reply_markup=None) -> None:
    """Edit the message a tapped button sits on, whether it's text or a photo.

    Market cards are photo messages (their text lives in the caption), and
    Telegram's editMessageText errors on those — so photos get their caption
    edited and text messages are edited exactly as before.
    """
    photo = getattr(getattr(query, "message", None), "photo", None)
    if isinstance(photo, (tuple, list)) and photo:
        await query.edit_message_caption(caption=text, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, reply_markup=reply_markup)


def dm_button(bot_username: str, payload: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start={payload}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("Open DM", url=url)]])


def format_balance(balance: Decimal) -> str:
    return f"{balance:.2f}"


def format_deposit_lines(deposit_addresses: dict[str, str]) -> str:
    """One line per network, known networks first in a fixed order."""
    ordered_keys = [k for k in _NETWORK_LABELS if k in deposit_addresses]
    other_keys = sorted(k for k in deposit_addresses if k not in _NETWORK_LABELS)
    lines = []
    for key in ordered_keys + other_keys:
        label = _NETWORK_LABELS.get(key, key.upper())
        lines.append(f"{label}: `{deposit_addresses[key]}`")
    return "\n".join(lines)
