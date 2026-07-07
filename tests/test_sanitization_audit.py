"""Input sanitization audit (PRD §7.6): hostile market titles and usernames
render safely in broadcast cards — Telegram markup neutralized, command
injection defanged, 4-byte emoji passed through without breaking anything.
"""

from __future__ import annotations

from decimal import Decimal

from fade.bot.handlers.common import sanitize
from fade.social.broadcast import render_trade_card

MARKUP_TITLE = "Will *bold* _italic_ `code` [link](https://evil.example) resolve?"
INJECTION_NAME = "/withdraw@fadebot && rm -rf"
EMOJI_TITLE = "Will 🚀 hit 💯 by 2027? 🤖👨‍👩‍👧‍👦"  # includes 4-byte and ZWJ sequences
EMOJI_NAME = "degen🐋whale"


def render(who: str, question: str) -> str:
    return render_trade_card(
        who=who,
        question=question,
        side_label="YES",
        amount_usdc=Decimal("25"),
        price=Decimal("0.61"),
        hide_amounts=False,
    )


def test_market_title_with_telegram_markup_renders_inert():
    text = render("priya", MARKUP_TITLE)
    for ch in ("*", "_", "`", "[", "]"):
        assert ch not in text
    assert "Will bold italic code" in text  # words survive, markup doesn't


def test_username_with_command_injection_cannot_start_a_command():
    text = render(INJECTION_NAME, "Will it rain?")
    # The card must not begin with a slash-command, so Telegram clients and
    # our own command parser can never interpret it as one.
    assert not text.startswith("/")
    for line in text.splitlines():
        assert not line.strip().startswith("/")


def test_four_byte_emoji_pass_through_safely():
    text = render(EMOJI_NAME, EMOJI_TITLE)
    assert "🚀" in text and "💯" in text  # emoji preserved, not mangled
    assert "🐋" in text
    # And the string is valid UTF-8 encodable (no lone surrogates / corruption).
    text.encode("utf-8")


def test_sanitize_truncates_absurd_titles_without_breaking_emoji():
    hostile = ("🔥" * 200) + "*`_[]"
    cleaned = sanitize(hostile)
    assert len(cleaned) <= 121
    for ch in ("*", "_", "`", "[", "]"):
        assert ch not in cleaned
    cleaned.encode("utf-8")


def test_combined_hostile_card_is_fully_neutralized():
    text = render(INJECTION_NAME + " *pwn*", MARKUP_TITLE + " 🚀")
    for ch in ("*", "_", "`", "[", "]"):
        assert ch not in text
    assert not text.startswith("/")
    assert "🚀" in text
