"""Shareable flex cards (PRD F-23), memecoin-bot style.

A landscape card in Fade's liquid-glass look: the brand's glass candlesticks
over a darkened wash of the banner art, a frosted-glass stat panel, a big
glowing PnL figure, and a QR code to the sharer's referral link. Green for
wins, red for losses — the brand art is red/green channel-swapped rather than
recolored, so it's the same glass, just the other side of the trade.

Three kinds of card:
  - resolved win   "CALLED IT"   sent automatically when a market settles
  - resolved loss  "GOT FADED"   sent automatically when a market settles
  - open position  "LIVE"        on demand, from the Flex button in /positions

Split into a data pipeline (build_*_data -> FlexCardData) and a renderer
(render_flex_card_image) on purpose: pixels aren't practically unit-testable,
so tests pin the data exactly and check the renderer emits a valid image.

Font: Geist (SIL OFL 1.1, vendored with its license in fonts/). Geist is
Latin-only and market titles are untrusted text, so anything the font can't
draw is filtered out before rendering. A missing glyph renders as a tofu box,
which shipped once in Stage 8; a test now covers every fixed label.

Art (assets/) is derived from brand/ at card size: flex_bg.jpg is the banner
cover-cropped to 1200x800, flex_candles.jpg is the logo's three glass candles.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache, lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from fade.bot.handlers.common import sanitize

WIDTH = 1200
HEIGHT = 800

_HERE = Path(__file__).resolve().parent
FONT_PATH = _HERE / "fonts" / "Geist-Variable.ttf"
_BG_PATH = _HERE / "assets" / "flex_bg.jpg"
_CANDLES_PATH = _HERE / "assets" / "flex_candles.jpg"

_BASE = (14, 11, 10)
_TEXT = (242, 239, 234)
_MUTED = (150, 144, 138)
_DIVIDER = (52, 46, 42)
_WIN = (150, 226, 74)
_LOSS = (240, 70, 60)

# Frosted panel (x0, y0, x1, y1), its corner radius, and inner padding.
_PANEL = (606, 52, 1152, 748)
_PANEL_RADIUS = 38
_PAD = 46
_FOOTER_H = 132  # matches _qr_tile's target size

STATUS_WIN = "CALLED IT"
STATUS_LOSS = "GOT FADED"
STATUS_LIVE = "LIVE"

CTA_LINE_1 = "Think I'm wrong?"
CTA_LINE_2 = "Scan to fade me"
CTA_NO_LINK = "Trade it on Fade"

# FreeType faces aren't safe to share across threads, and renders run via
# asyncio.to_thread — serialize them. A render is ~200 ms; contention is nil.
_RENDER_LOCK = threading.Lock()


@dataclass(frozen=True)
class FlexCardData:
    """Everything the renderer needs, pre-formatted and pre-sanitized.

    The first six fields are the original Stage 8 contract and keep their
    exact formats; the rest default to the Stage 8 behavior (a resolved win,
    no hold time, no QR) so older callers render unchanged in meaning.
    """

    title: str
    side_label: str  # "YES", "NO", or "" if the outcome wasn't reported
    entry_price_label: str  # e.g. "$0.61"
    exit_price_label: str  # e.g. "$1.00" (settled) or "$0.70" (current)
    pnl_dollar_label: str  # e.g. "+$15.60" / "-$11.00"
    pnl_percent_label: str  # e.g. "+63.9%" / "-100.0%"
    won: bool = True  # drives green vs red everywhere
    status_label: str = STATUS_WIN
    invested_label: str = ""  # e.g. "$24.40"; "" hides the stat
    held_label: str = ""  # e.g. "3d 4h"; "" hides the clock
    exit_caption: str = "Settled"  # "Settled" or "Now"
    referral_link: str = ""  # "" means no QR / CTA


# -- formatting ------------------------------------------------------------------


def _signed_money(value: Decimal) -> str:
    value = Decimal(value).quantize(Decimal("0.01"))
    return f"+${value:,.2f}" if value >= 0 else f"-${-value:,.2f}"


def _signed_pct(value: Decimal) -> str:
    value = Decimal(value).quantize(Decimal("0.1"))
    return f"+{value:.1f}%" if value >= 0 else f"-{-value:.1f}%"


def format_held(delta: timedelta) -> str:
    """Trojan-style hold time: '45s', '12m 24s', '5h 3m', '3d 4h'."""
    seconds = max(int(delta.total_seconds()), 0)
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# -- data pipeline ---------------------------------------------------------------


def build_flex_card_data(
    position,
    pnl: Decimal,
    *,
    referral_link: str = "",
    held: timedelta | None = None,
) -> FlexCardData:
    """Format a RESOLVED position (win or loss) into card-ready strings.

    `position` is a `fade.bot.handlers.resolutions.ResolvedPosition`; typed
    loosely here to avoid a cycle (handlers import fade.social).
    """
    cost_basis = position.avg_price * position.shares
    pct = (pnl / cost_basis * 100) if cost_basis else Decimal("0")
    won = pnl >= 0
    return FlexCardData(
        title=sanitize(position.title, limit=90),
        side_label=sanitize(getattr(position, "outcome", "") or "", limit=8).upper(),
        entry_price_label=f"${position.avg_price:.2f}",
        exit_price_label=f"${position.final_price:.2f}",
        pnl_dollar_label=_signed_money(pnl),
        pnl_percent_label=_signed_pct(pct),
        won=won,
        status_label=STATUS_WIN if won else STATUS_LOSS,
        invested_label=f"${cost_basis:,.2f}",
        held_label=format_held(held) if held is not None else "",
        exit_caption="Settled",
        referral_link=referral_link,
    )


def build_open_flex_card_data(
    view,
    *,
    referral_link: str = "",
    held: timedelta | None = None,
) -> FlexCardData:
    """Format an OPEN position (`trading.service.PositionView`) for flexing.

    Prefers the data API's own PnL figures; computes them from prices only
    when the API didn't report them.
    """
    avg = view.avg_price if view.avg_price is not None else Decimal("0")
    current = view.cur_price if view.cur_price is not None else avg
    invested = avg * view.shares
    pnl = view.cash_pnl if view.cash_pnl is not None else (current - avg) * view.shares
    if view.percent_pnl is not None:
        pct = Decimal(str(view.percent_pnl))
    else:
        pct = (pnl / invested * 100) if invested else Decimal("0")
    return FlexCardData(
        title=sanitize(view.title, limit=90),
        side_label=sanitize(view.outcome or "", limit=8).upper(),
        entry_price_label=f"${avg:.2f}",
        exit_price_label=f"${current:.2f}",
        pnl_dollar_label=_signed_money(pnl),
        pnl_percent_label=_signed_pct(pct),
        won=pnl >= 0,
        status_label=STATUS_LIVE,
        invested_label=f"${invested:,.2f}",
        held_label=format_held(held) if held is not None else "",
        exit_caption="Now",
        referral_link=referral_link,
    )


async def held_duration(
    session_factory, telegram_user_id: int, token_id: str, now: datetime | None = None
) -> timedelta | None:
    """How long the user has held `token_id`: since their first filled BUY.

    None when the position wasn't opened through Fade (no order on record).
    """
    from sqlalchemy import func, select

    from fade.db.models import Order

    async with session_factory() as session:
        first = await session.scalar(
            select(func.min(Order.created_at)).where(
                Order.telegram_user_id == telegram_user_id,
                Order.token_id == token_id,
                Order.side == "BUY",
                Order.status == "filled",
            )
        )
    if first is None:
        return None
    if first.tzinfo is None:  # SQLite hands back naive UTC
        first = first.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - first


# -- fonts and glyph coverage --------------------------------------------------------


@cache
def _font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    font.set_variation_by_axes([weight])
    return font


def _ink(ch: str) -> bytes:
    img = Image.new("L", (64, 64))
    ImageDraw.Draw(img).text((8, 8), ch, font=_font(40), fill=255)
    return img.tobytes()


@lru_cache(maxsize=1)
def _notdef_ink() -> bytes:
    return _ink("")  # private-use codepoint: never in Geist, draws .notdef


@lru_cache(maxsize=4096)
def has_glyph(ch: str) -> bool:
    """True if Geist can draw `ch` rather than its tofu box."""
    return ch.isspace() or _ink(ch) != _notdef_ink()


def drawable(text: str) -> str:
    """Drop characters the font can't draw (emoji, CJK) and tidy the spacing."""
    return " ".join("".join(ch for ch in text if has_glyph(ch)).split())


# -- drawing primitives ---------------------------------------------------------------


def _swap_rg(img: Image.Image) -> Image.Image:
    r, g, b = img.split()
    return Image.merge("RGB", (g, r, b))


def _mirror_hue(img: Image.Image) -> Image.Image:
    """Reflect hues across the brand's red/green axis: the logo's lime glass
    (~81 deg) lands on red (~3 deg) and vice versa. A plain R/G channel swap
    turns that lime into amber, which doesn't read as a loss."""
    h, s, v = img.convert("HSV").split()
    return Image.merge("HSV", (h.point(lambda x: (60 - x) % 256), s, v)).convert("RGB")


def _candle_art(won: bool) -> Image.Image:
    art = Image.open(_CANDLES_PATH).convert("RGB")
    art = ImageEnhance.Color(ImageEnhance.Contrast(art).enhance(1.12)).enhance(1.15)
    return art if won else _mirror_hue(art)


def _solid(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", size, color)


def _rounded_mask(size: tuple[int, int], radius: int, inset: int = 0) -> Image.Image:
    """Anti-aliased rounded-rect mask: drawn at 4x, downsampled."""
    s = 4
    w, h = size
    big = Image.new("L", (w * s, h * s), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (inset * s, inset * s, (w - inset) * s - 1, (h - inset) * s - 1),
        radius=max(radius - inset, 1) * s,
        fill=255,
    )
    return big.resize(size, Image.LANCZOS)


def _radial(
    size: tuple[int, int], center: tuple[int, int], radii: tuple[int, int], strength: int
) -> Image.Image:
    """Soft elliptical mask — glows and vignettes."""
    mask = Image.new("L", size, 0)
    cx, cy = center
    rx, ry = radii
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=strength)
    return mask.filter(ImageFilter.GaussianBlur(max(rx, ry) * 0.45))


def _diagonal(size: tuple[int, int]) -> Image.Image:
    """0 at the top-left corner rising to 255 at the bottom-right."""
    vertical = Image.linear_gradient("L").resize(size)
    horizontal = Image.linear_gradient("L").transpose(Image.Transpose.TRANSPOSE).resize(size)
    return ImageChops.add(vertical, horizontal, scale=2.0)


def _screen_onto(card: Image.Image, art: Image.Image, xy: tuple[int, int], feather: bool) -> None:
    """Screen-blend glass art onto the card so its near-black backdrop vanishes."""
    art = ImageChops.subtract(art, _solid(art.size, (22, 18, 16)))
    x, y = xy
    region = card.crop((x, y, x + art.width, y + art.height))
    lit = ImageChops.screen(region, art)
    if feather:
        w, h = art.size
        mask = _radial(art.size, (w // 2, h // 2), (int(w * 0.48), int(h * 0.5)), 255)
        lit = Image.composite(lit, region, mask)
    card.paste(lit, (x, y))


def _text_width(text: str, font: ImageFont.FreeTypeFont, tracking: int = 0) -> int:
    if not text:
        return 0
    if not tracking:
        return round(font.getlength(text))
    return round(sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1))


def _draw_text(draw, xy, text, font, fill, tracking: int = 0) -> None:
    if not tracking:
        draw.text(xy, text, font=font, fill=fill)
        return
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> list[str]:
    ellipsis = "…" if has_glyph("…") else "..."
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        while font.getlength(current) > max_width and len(current) > 1:
            cut = len(current)
            while cut > 1 and font.getlength(current[:cut]) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + ellipsis) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + ellipsis
    return lines


def _pill(card: Image.Image, xy: tuple[int, int], text: str, color, height: int = 36) -> int:
    """Glassy capsule label; returns its width."""
    font = _font(17, 600)
    tracking = 2
    pad_x = 16
    width = _text_width(text, font, tracking) + pad_x * 2
    x, y = xy
    mask = _rounded_mask((width, height), height // 2)
    tint = tuple(int(c * 0.16 + b * 0.84) for c, b in zip(color, _BASE, strict=True))
    card.paste(_solid((width, height), tint), (x, y), mask)
    ring = ImageChops.subtract(mask, _rounded_mask((width, height), height // 2, inset=2))
    card.paste(_solid((width, height), color), (x, y), ring.point(lambda v: int(v * 0.7)))
    top, bottom = font.getbbox(text)[1], font.getbbox(text)[3]
    text_y = y + (height - (bottom - top)) / 2 - top
    _draw_text(ImageDraw.Draw(card), (x + pad_x, text_y), text, font, color, tracking)
    return width


def _clock(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, color) -> None:
    cx, cy = center
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=2)
    draw.line((cx, cy, cx, cy - radius + 4), fill=color, width=2)
    draw.line((cx, cy, cx + radius - 5, cy), fill=color, width=2)


def _glow_number(card: Image.Image, xy: tuple[int, int], text: str, font, color) -> None:
    """The headline figure: soft neon halo, then a face lit from above."""
    draw = ImageDraw.Draw(card)
    left, top, right, bottom = draw.textbbox(xy, text, font=font)
    pad = 60
    box = (left - pad, top - pad, right + pad, bottom + pad)
    size = (box[2] - box[0], box[3] - box[1])
    layer = Image.new("L", size, 0)
    ImageDraw.Draw(layer).text((xy[0] - box[0], xy[1] - box[1]), text, font=font, fill=255)

    halo = layer.filter(ImageFilter.GaussianBlur(24)).point(lambda v: int(v * 0.85))
    region = card.crop(box)
    region.paste(_solid(size, color), (0, 0), halo)

    # Light from above; red takes less of it or it washes out to peach.
    lift = 0.45 if color == _WIN else 0.22
    lit = tuple(min(255, int(c + (255 - c) * lift)) for c in color)
    gradient = Image.linear_gradient("L").resize(size)  # 0 top -> 255 bottom
    face = Image.composite(_solid(size, color), _solid(size, lit), gradient)
    region.paste(face, (0, 0), layer)
    card.paste(region, box[:2])


def _qr_tile(link: str, target: int = 132) -> Image.Image:
    """Dark-on-light QR with a two-module quiet zone, integer module size."""
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0, box_size=1)
    qr.add_data(link)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    box = max(2, target // (modules + 4))
    side = (modules + 4) * box
    tile = _solid((side, side), (246, 244, 240))
    draw = ImageDraw.Draw(tile)
    offset = 2 * box
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x, y = offset + c * box, offset + r * box
                draw.rectangle((x, y, x + box - 1, y + box - 1), fill=_BASE)
    return tile


# -- composition ----------------------------------------------------------------------


@lru_cache(maxsize=2)
def _scene(won: bool) -> Image.Image:
    """Backdrop + hero art, cached per color: the expensive, data-free part."""
    accent = _WIN if won else _LOSS
    bg = Image.open(_BG_PATH).convert("RGB")
    if won:  # the banner's liquid blobs are orange/red; swapped, they go green
        bg = _swap_rg(bg)
    bg = bg.filter(ImageFilter.GaussianBlur(10))
    bg = Image.blend(bg, _solid(bg.size, _BASE), 0.6)

    glow = _radial(bg.size, (300, 410), (290, 300), strength=85)
    bg = Image.composite(_solid(bg.size, accent), bg, glow)

    candles = _candle_art(won)  # green-red-green, or red-green-red for losses
    height = 620
    width = round(candles.width * height / candles.height)
    candles = candles.resize((width, height), Image.LANCZOS)
    _screen_onto(bg, candles, (300 - width // 2, 400 - height // 2), feather=True)

    center = (WIDTH // 2, HEIGHT // 2)
    inner = _radial(bg.size, center, (int(WIDTH * 0.66), int(HEIGHT * 0.74)), 255)
    edges = ImageChops.invert(inner).point(lambda v: int(v * 0.6))
    return Image.composite(_solid(bg.size, (5, 4, 3)), bg, edges)


def _glass_panel(card: Image.Image, accent) -> None:
    """Frosted glass: blurred backdrop, dark tint, top sheen, lit rim, shadow."""
    x0, y0, x1, y1 = _PANEL
    size = (x1 - x0, y1 - y0)
    mask = _rounded_mask(size, _PANEL_RADIUS)

    shadow = Image.new("L", card.size, 0)
    shadow.paste(mask.point(lambda v: int(v * 0.8)), (x0, y0 + 24))
    card.paste(_solid(card.size, (0, 0, 0)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(36)))

    body = card.crop(_PANEL).filter(ImageFilter.GaussianBlur(28))
    body = Image.blend(body, _solid(size, (17, 14, 12)), 0.62)
    body = Image.blend(body, _solid(size, accent), 0.03)
    sheen = ImageChops.invert(Image.linear_gradient("L").resize(size))
    sheen = sheen.point(lambda v: int(max(0, v - 150) * 0.28))  # top ~40% only
    body = Image.composite(_solid(size, (255, 255, 255)), body, sheen)
    card.paste(body, (x0, y0), mask)

    ring = ImageChops.subtract(mask, _rounded_mask(size, _PANEL_RADIUS, inset=2))
    light = ImageChops.invert(_diagonal(size)).point(lambda v: int(36 + v * 0.55))
    card.paste(_solid(size, (255, 255, 255)), (x0, y0), ImageChops.multiply(ring, light))


def render_flex_card_image(data: FlexCardData) -> bytes:
    """Draw the flex card and return PNG bytes."""
    with _RENDER_LOCK:
        return _render(data)


def _render(data: FlexCardData) -> bytes:
    accent = _WIN if data.won else _LOSS
    card = _scene(data.won).copy()
    _glass_panel(card, accent)
    draw = ImageDraw.Draw(card)

    x0, y0, x1, y1 = _PANEL
    left, right = x0 + _PAD, x1 - _PAD
    content_w = right - left

    # Brand row: glass candle mark + wordmark, status capsule on the right.
    brand_y = y0 + _PAD
    mark = _candle_art(data.won)
    mark = mark.resize((round(mark.width * 34 / mark.height), 34), Image.LANCZOS)
    _screen_onto(card, mark, (left - 4, brand_y), feather=False)
    wordmark = _font(24, 600)
    _draw_text(draw, (left + mark.width + 6, brand_y + 3), "FADE", wordmark, _TEXT, tracking=7)
    status = drawable(data.status_label).upper()
    status_w = _text_width(status, _font(17, 600), 2) + 32
    _pill(card, (right - status_w, brand_y - 1), status, accent)

    # Market question: two lines max, stepping the size down before cutting,
    # so a long title never squeezes the headline figure.
    title = drawable(data.title)
    for size in (31, 28, 26):
        title_font = _font(size, 500)
        lines = _wrap(title, title_font, content_w, max_lines=99)
        if len(lines) <= 2:
            break
    lines = _wrap(title, title_font, content_w, max_lines=2)
    y = brand_y + 34 + 30
    for line in lines:
        draw.text((left, y), line, font=title_font, fill=_TEXT)
        y += round(title_font.size * 1.3)

    # Side + hold time.
    meta_y = y + 12
    meta_x = left
    if data.side_label:
        meta_x += _pill(card, (left, meta_y), drawable(data.side_label), accent) + 18
    if data.held_label:
        _clock(draw, (meta_x + 10, meta_y + 18), 10, _MUTED)
        held = drawable(data.held_label)
        draw.text((meta_x + 28, meta_y + 6), held, font=_font(20, 500), fill=_MUTED)
    top_stack_end = meta_y + 36

    # Bottom stack, anchored to the panel floor so the money always sits in
    # the same place regardless of title length.
    # Footer height is fixed whether or not there's a QR, so cards without a
    # referral link keep the same proportions as cards with one.
    tile = _qr_tile(data.referral_link) if data.referral_link else None
    footer_h = _FOOTER_H
    footer_top = y1 - _PAD - footer_h
    divider_y = footer_top - 26
    price_y = divider_y - 26 - 22
    value_y = price_y - 24 - 36
    label_y = value_y - 28

    # Headline figure: as big as fits between the two stacks, centered in the
    # gap so a one-line title doesn't leave it floating low with dead space above.
    pct_text = data.pnl_percent_label.replace("-", "−", 1)  # typographic minus
    gap_top, gap_bottom = top_stack_end + 22, label_y - 30
    size = 132
    while size > 48:
        font = _font(size, 760)
        box_l, box_t, box_r, box_b = draw.textbbox((0, 0), pct_text, font=font)
        if box_r - box_l <= content_w and box_b - box_t <= gap_bottom - gap_top:
            break
        size -= 4
    font = _font(size, 760)
    box_l, box_t, _, box_b = draw.textbbox((0, 0), pct_text, font=font)
    ink_top = gap_top + (gap_bottom - gap_top - (box_b - box_t)) // 2
    _glow_number(card, (left - box_l, ink_top - box_t), pct_text, font, accent)
    draw = ImageDraw.Draw(card)

    # Stats: invested | profit or loss.
    label_font = _font(15, 600)
    value_font = _font(32, 600)
    if data.invested_label:
        _draw_text(draw, (left, label_y), "INVESTED", label_font, _MUTED, tracking=2)
        draw.text((left, value_y), data.invested_label, font=value_font, fill=_TEXT)
    pnl_caption = ("PROFIT" if data.won else "LOSS") if data.status_label != STATUS_LIVE else (
        "CURRENT GAIN" if data.won else "CURRENT LOSS"
    )
    col2 = left + content_w // 2
    _draw_text(draw, (col2, label_y), pnl_caption, label_font, _MUTED, tracking=2)
    draw.text((col2, value_y), data.pnl_dollar_label, font=value_font, fill=accent)

    prices = f"Entry {data.entry_price_label}  ·  {data.exit_caption} {data.exit_price_label}"
    draw.text((left, price_y), drawable(prices), font=_font(19, 400), fill=_MUTED)

    draw.line((left, divider_y, right, divider_y), fill=_DIVIDER, width=1)

    # Footer: QR to the sharer's referral link, with the call to action.
    if tile is not None:
        tile_y = footer_top + (footer_h - tile.height) // 2
        card.paste(tile, (right - tile.width, tile_y), _rounded_mask(tile.size, 14))
        cta_top = footer_top + (footer_h - 64) // 2
        draw.text((left, cta_top), CTA_LINE_1, font=_font(20, 400), fill=_MUTED)
        draw.text((left, cta_top + 28), CTA_LINE_2, font=_font(28, 650), fill=_TEXT)
    else:
        draw.text((left, footer_top + (footer_h - 28) // 2), CTA_NO_LINK,
                  font=_font(22, 500), fill=_MUTED)

    buf = BytesIO()
    card.save(buf, format="PNG")
    return buf.getvalue()


def generate_flex_card(
    position, pnl: Decimal, *, referral_link: str = "", held: timedelta | None = None
) -> bytes:
    """Full pipeline for a resolved position -> PNG bytes, ready to send."""
    return render_flex_card_image(
        build_flex_card_data(position, pnl, referral_link=referral_link, held=held)
    )


def generate_open_flex_card(
    view, *, referral_link: str = "", held: timedelta | None = None
) -> bytes:
    """Full pipeline for an open position -> PNG bytes, ready to send."""
    return render_flex_card_image(
        build_open_flex_card_data(view, referral_link=referral_link, held=held)
    )
