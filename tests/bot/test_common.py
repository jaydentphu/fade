"""Sanitization of untrusted text (PRD §7.6)."""

from __future__ import annotations

from fade.bot.handlers.common import sanitize


def test_sanitize_strips_markdown_control_characters():
    hostile = "Will *bold* _sneaky_ `code` [link](x) \\ market?"
    cleaned = sanitize(hostile)
    for ch in ("*", "_", "`", "[", "]", "\\"):
        assert ch not in cleaned
    assert "Will bold sneaky code" in cleaned


def test_sanitize_neutralizes_command_prefix_and_newlines():
    assert not sanitize("/start now").startswith("/")
    assert "\n" not in sanitize("line1\nline2")


def test_sanitize_truncates_long_titles():
    cleaned = sanitize("x" * 500)
    assert len(cleaned) <= 121  # limit + ellipsis
