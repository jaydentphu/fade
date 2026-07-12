"""Shareable "Called It" flex cards (PRD F-23).

Generated only for positions that resolve in profit. Split into a data
pipeline (`build_flex_card_data`) and a renderer (`render_flex_card_image`)
on purpose: pixel-level image output isn't practically unit-testable, so
tests assert the data pipeline's output instead of decoding pixels.

Uses Pillow's bundled Aileron Regular font (via `ImageFont.load_default`,
scalable since Pillow 10.1) rather than shipping a separate font file or
fetching one — one fewer asset to license-track or vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from fade.bot.handlers.common import sanitize

WIDTH = 1080
HEIGHT = 1080

_BG = "#14100E"
_CARD = "#1F1B18"
_BORDER = "#2A2422"
_TEXT_PRIMARY = "#F2EFEA"
_TEXT_MUTED = "#8A8580"
_WIN = "#639922"
_ACCENT = "#FFB59E"


@dataclass(frozen=True)
class FlexCardData:
    """Everything the renderer needs, pre-formatted and pre-sanitized.

    Labels are plain ASCII on purpose: Pillow's bundled Aileron font (see
    module docstring) only has a limited character set, and glyphs outside
    it silently render as a tofu box (confirmed by comparing getbbox() of
    '¢'/crown-emoji/arrows against a known-missing glyph — all four returned
    the same box). No currency symbols beyond '$', no emoji, no arrows.
    """

    title: str
    side_label: str  # "YES", "NO", or "" if the outcome wasn't reported
    entry_price_label: str  # e.g. "$0.61"
    exit_price_label: str  # e.g. "$1.00"
    pnl_dollar_label: str  # e.g. "+$15.60"
    pnl_percent_label: str  # e.g. "+63.9%"


def build_flex_card_data(position, pnl: Decimal) -> FlexCardData:
    """Format a resolved (winning) position into card-ready strings.

    `position` is a `fade.bot.handlers.resolutions.ResolvedPosition`; typed
    loosely here to avoid a cycle (handlers import fade.social).
    """
    cost_basis = position.avg_price * position.shares
    pct = (pnl / cost_basis * 100) if cost_basis else Decimal("0")
    return FlexCardData(
        title=sanitize(position.title, limit=90),
        side_label=sanitize(getattr(position, "outcome", "") or "", limit=8).upper(),
        entry_price_label=f"${position.avg_price:.2f}",
        exit_price_label=f"${position.final_price:.2f}",
        pnl_dollar_label=f"+${pnl:.2f}",
        pnl_percent_label=f"+{pct:.1f}%",
    )


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_flex_card_image(data: FlexCardData) -> bytes:
    """Draw the flex card and return PNG bytes."""
    img = Image.new("RGB", (WIDTH, HEIGHT), _BG)
    draw = ImageDraw.Draw(img)

    margin = 72
    draw.rounded_rectangle(
        (margin, margin, WIDTH - margin, HEIGHT - margin),
        radius=32,
        fill=_CARD,
        outline=_BORDER,
        width=2,
    )

    pad = margin + 56
    label_font = _font(30)
    title_font = _font(44)
    side_font = _font(32)
    pnl_font = _font(120)
    pct_font = _font(48)
    row_font = _font(32)
    row_label_font = _font(24)

    title_lines = _wrap(draw, data.title, title_font, WIDTH - 2 * pad)[:3]

    # Sum the block's height up front so it can be centered in the card
    # instead of always hugging the top with dead space below.
    block_height = 70 + 58 * len(title_lines) + 24
    if data.side_label:
        block_height += 60
    block_height += 140 + 90 + 32 + 32
    available = (HEIGHT - 2 * margin) - block_height
    y = margin + max(56, available // 2)

    draw.text((pad, y), "CALLED IT", font=label_font, fill=_ACCENT)
    y += 70

    for line in title_lines:
        draw.text((pad, y), line, font=title_font, fill=_TEXT_PRIMARY)
        y += 58
    y += 24

    if data.side_label:
        draw.text((pad, y), data.side_label, font=side_font, fill=_TEXT_MUTED)
        y += 60

    draw.text((pad, y), data.pnl_dollar_label, font=pnl_font, fill=_WIN)
    y += 140

    draw.text((pad, y), data.pnl_percent_label, font=pct_font, fill=_WIN)
    y += 90

    draw.text((pad, y), "ENTRY", font=row_label_font, fill=_TEXT_MUTED)
    draw.text((pad, y + 32), data.entry_price_label, font=row_font, fill=_TEXT_PRIMARY)
    col2 = pad + 320
    draw.text((col2, y), "SETTLED", font=row_label_font, fill=_TEXT_MUTED)
    draw.text((col2, y + 32), data.exit_price_label, font=row_font, fill=_TEXT_PRIMARY)

    footer_font = _font(28)
    footer_text = "fade"
    bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    draw.text(
        (WIDTH - margin - 56 - (bbox[2] - bbox[0]), HEIGHT - margin - 56),
        footer_text,
        font=footer_font,
        fill=_TEXT_MUTED,
    )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_flex_card(position, pnl: Decimal) -> bytes:
    """Full pipeline: resolved position -> PNG bytes, ready to send."""
    return render_flex_card_image(build_flex_card_data(position, pnl))
