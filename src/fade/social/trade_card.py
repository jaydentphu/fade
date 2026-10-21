"""Trade cards: the market card (DM) and the group broadcast card (F-8, F-17).

Same visual family as the flex cards (fade.social.flex_card): the brand's
glass candles over the banner wash on the left, a frosted panel on the right.

- Market card: what you get when you paste a Polymarket link. Implied odds
  as the headline, a YES/NO probability bar, the two prices as glass tiles.
- Broadcast card: what a clan sees when a member buys. The trader squares off
  against an open slot — the side anyone tapping Fade would take.

The images carry no buttons. Telegram can't put buttons inside a photo, so
the real size / Fade / Copy buttons stay in the inline keyboard under the
image, and the caption repeats the facts as text so notification previews
and chat search keep working. Data pipeline / renderer split, as with flex
cards: tests pin the data exactly and check the renderer emits an image.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from fade.bot.handlers.common import sanitize
from fade.social.flex_card import (
    _BASE,
    _BRAND,
    _DIVIDER,
    _LOSS,
    _MUTED,
    _PAD,
    _PANEL,
    _RENDER_LOCK,
    _TEXT,
    _WIN,
    _brand_row,
    _cta,
    _draw_text,
    _font,
    _glass_panel,
    _glow_number,
    _rounded_mask,
    _solid,
    _title_block,
    _toned_scene,
    drawable,
)

SIDE_COLORS = {"YES": _WIN, "NO": _LOSS}
STATUS_MARKET = "MARKET"
STATUS_NEW_POSITION = "NEW POSITION"
MARKET_CTA = ("Think you know?", "Pick a side below")
BROADCAST_CTA = ("Think they're wrong?", "Fade them below")
OPEN_SLOT = "open slot"

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_JPEG_QUALITY = 90  # Telegram recompresses photos anyway; JPEG keeps uploads ~1/3 of PNG


@dataclass(frozen=True)
class MarketCardData:
    title: str
    resolves_label: str  # "RESOLVES SEP 30, 2026" or "OPEN MARKET"
    yes_label: str  # "61¢", or "—" when unpriced
    no_label: str  # "39¢"
    odds_label: str  # "61%" (implied YES probability), "" when unpriced
    yes_share: float | None  # 0..1 for the probability bar; None when unpriced


@dataclass(frozen=True)
class BroadcastCardData:
    title: str
    trader: str  # sanitized display name
    initials: str  # "PR"
    side: str  # "YES" | "NO"
    price_label: str  # entry, "61¢"
    amount_label: str  # "$50", or "" when the trader hides amounts (F-17)
    fade_side: str  # the opposite side: what a Fade tap buys
    fade_price_label: str  # implied opposite price, "39¢"


# -- data pipeline ---------------------------------------------------------------


def _cents(price) -> str:
    return "—" if price is None else f"{Decimal(price) * 100:.0f}¢"


def _money(amount) -> str:
    value = Decimal(amount).quantize(Decimal("0.01"))
    return f"${value:,.0f}" if value == value.to_integral_value() else f"${value:,.2f}"


def resolves_label(end_date: str | None) -> str:
    try:
        day = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        day = None
    if day is None:
        return "OPEN MARKET"
    return f"RESOLVES {_MONTHS[day.month - 1]} {day.day}, {day.year}"


def _initials(name: str) -> str:
    return "".join(ch for ch in drawable(name) if ch.isalnum())[:2].upper() or "?"


def build_market_card_data(card) -> MarketCardData:
    """`card` is a `fade.trading.markets.MarketCard`."""
    yes = card.yes_price
    return MarketCardData(
        title=sanitize(card.question),
        resolves_label=resolves_label(card.end_date),
        yes_label=_cents(yes),
        no_label=_cents(card.no_price),
        odds_label="" if yes is None else f"{Decimal(yes) * 100:.0f}%",
        yes_share=None if yes is None else float(yes),
    )


def build_broadcast_card_data(
    *, who: str, question: str, side_label: str, amount_usdc, price, hide_amounts: bool
) -> BroadcastCardData:
    """Same inputs as broadcast.render_trade_card, so both views of one
    trade can never disagree. Amounts obey the trader's privacy toggle."""
    side = "NO" if side_label.strip().upper() == "NO" else "YES"
    trader = sanitize(who, limit=24)
    return BroadcastCardData(
        title=sanitize(question),
        trader=trader,
        initials=_initials(trader),
        side=side,
        price_label=_cents(price),
        amount_label="" if hide_amounts or amount_usdc is None else _money(amount_usdc),
        fade_side="YES" if side == "NO" else "NO",
        fade_price_label=_cents(None if price is None else 1 - Decimal(price)),
    )


# -- drawing primitives ------------------------------------------------------------


def _tint(color, amount: float) -> tuple[int, int, int]:
    return tuple(int(c * amount + b * (1 - amount)) for c, b in zip(color, _BASE, strict=True))


def _centered(draw, cx: float, y: float, text: str, font, fill) -> None:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (right - left) / 2 - left, y), text, font=font, fill=fill)


def _ring(size: tuple[int, int], radius: int, width: int) -> Image.Image:
    return ImageChops.subtract(
        _rounded_mask(size, radius), _rounded_mask(size, radius, inset=width)
    )


def _tile(card: Image.Image, box, label: str, value: str, color) -> None:
    """A glass price tile: tinted body, colored rim, label over a big value."""
    x, y, w, h = box
    card.paste(_solid((w, h), _tint(color, 0.13)), (x, y), _rounded_mask((w, h), 20))
    card.paste(_solid((w, h), color), (x, y), _ring((w, h), 20, 2).point(lambda v: int(v * 0.7)))
    draw = ImageDraw.Draw(card)
    _draw_text(draw, (x + 22, y + 18), label, _font(15, 650), _tint(color, 0.85), tracking=2)
    draw.text((x + 20, y + 40), value, font=_font(40, 650), fill=color)


def _probability_bar(card: Image.Image, box, share: float) -> None:
    """YES share in green, NO in red, softly glowing, with a hairline split."""
    x, y, w, h = box
    split = max(h, min(w - h, round(w * share)))
    bar = _solid((w, h), _LOSS)
    bar.paste(_solid((split, h), _WIN), (0, 0))
    mask = _rounded_mask((w, h), h // 2)

    pad = 24
    halo = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    halo.paste(mask, (pad, pad))
    halo = halo.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.55))
    glow = _solid(halo.size, _LOSS)
    glow.paste(_solid((split + pad, halo.height), _WIN), (0, 0))
    card.paste(glow, (x - pad, y - pad), halo)

    card.paste(bar, (x, y), mask)
    ImageDraw.Draw(card).line((x + split, y, x + split, y + h - 1), fill=_BASE, width=3)


def _orb(card: Image.Image, center, radius: int, color, initials: str) -> None:
    """The trader: a glowing glass bead in their side's color."""
    cx, cy = center
    d = radius * 2
    pad = 40
    halo = Image.new("L", (d + 2 * pad, d + 2 * pad), 0)
    ImageDraw.Draw(halo).ellipse((pad, pad, pad + d, pad + d), fill=255)
    halo = halo.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.6))
    card.paste(_solid(halo.size, color), (cx - radius - pad, cy - radius - pad), halo)

    disc = _rounded_mask((d, d), radius)
    card.paste(_solid((d, d), _tint(color, 0.22)), (cx - radius, cy - radius), disc)
    sheen = Image.linear_gradient("L").resize((d, d)).point(lambda v: max(0, 140 - v) // 2)
    card.paste(_solid((d, d), (255, 255, 255)), (cx - radius, cy - radius),
               ImageChops.multiply(sheen, disc))
    card.paste(_solid((d, d), color), (cx - radius, cy - radius), _ring((d, d), radius, 3))

    draw = ImageDraw.Draw(card)
    font = _font(round(radius * 0.7), 650)
    lit = tuple(min(255, int(c + (255 - c) * 0.35)) for c in color)
    _, top, _, bottom = draw.textbbox((0, 0), initials, font=font)
    _centered(draw, cx, cy - (bottom - top) / 2 - top, initials, font, lit)


def _open_slot(card: Image.Image, center, radius: int, color) -> None:
    """The fade side, still open: a dashed ring with a question mark."""
    cx, cy = center
    s = 4  # supersample the dashes so they stay smooth
    d = radius * 2
    layer = Image.new("L", (d * s, d * s), 0)
    ring = ImageDraw.Draw(layer)
    dashes = 22
    for i in range(dashes):
        start = i * 360 / dashes
        ring.arc((s, s, d * s - s, d * s - s), start, start + 360 / dashes * 0.55,
                 fill=255, width=2 * s)
    layer = layer.resize((d, d), Image.LANCZOS)
    card.paste(_solid((d, d), color), (cx - radius, cy - radius), layer)
    draw = ImageDraw.Draw(card)
    font = _font(round(radius * 0.75), 500)
    _, top, _, bottom = draw.textbbox((0, 0), "?", font=font)
    _centered(draw, cx, cy - (bottom - top) / 2 - top, "?", font, color)


def _to_jpeg(card: Image.Image) -> bytes:
    buf = BytesIO()
    card.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY, subsampling=0)
    return buf.getvalue()


def _frame(tone: str, accent, status: str, green_mark: bool):
    """Shared card frame: scene, glass panel, brand row. Returns the canvas
    plus the panel's content box (left, right, top-of-content, bottom)."""
    card = _toned_scene(tone).copy()
    _glass_panel(card, accent)
    x0, y0, x1, y1 = _PANEL
    left, right = x0 + _PAD, x1 - _PAD
    _brand_row(card, left, right, y0 + _PAD, status, accent, green_mark=green_mark)
    return card, left, right, y0 + _PAD + 34, y1 - _PAD


# -- renderers ---------------------------------------------------------------------------


def render_market_card_image(data: MarketCardData) -> bytes:
    with _RENDER_LOCK:
        card, left, right, top, bottom = _frame("neutral", _BRAND, STATUS_MARKET, green_mark=True)
        draw = ImageDraw.Draw(card)
        width = right - left

        _draw_text(draw, (left, top + 26), drawable(data.resolves_label), _font(15, 600),
                   _MUTED, tracking=2)
        title_end = _title_block(draw, data.title, left, top + 56, width)

        # Bottom-up: call to action, divider, price tiles, probability bar.
        cta_top = bottom - 64
        divider_y = cta_top - 26
        tile_h = 96
        tile_top = divider_y - 26 - tile_h
        bar_h = 14
        bar_top = tile_top - 30 - bar_h

        tile_w = (width - 16) // 2
        _tile(card, (left, tile_top, tile_w, tile_h), "YES", data.yes_label, _WIN)
        _tile(card, (left + tile_w + 16, tile_top, tile_w, tile_h), "NO", data.no_label, _LOSS)
        if data.yes_share is not None:
            _probability_bar(card, (left, bar_top, width, bar_h), data.yes_share)

        # Headline odds, centered in whatever space the title leaves.
        draw = ImageDraw.Draw(card)
        if data.odds_label:
            font = _font(112, 760)
            box = draw.textbbox((0, 0), data.odds_label, font=font)
            gap_top, gap_bottom = title_end + 20, bar_top - 26
            ink_top = gap_top + (gap_bottom - gap_top - (box[3] - box[1])) // 2
            _glow_number(card, (left - box[0], ink_top - box[1]), data.odds_label, font, _WIN)
            draw = ImageDraw.Draw(card)
            draw.text((left + box[2] - box[0] + 18, ink_top + (box[3] - box[1]) - 52),
                      "chance", font=_font(22, 500), fill=_MUTED)
            draw.text((left + box[2] - box[0] + 18, ink_top + (box[3] - box[1]) - 26),
                      "of YES", font=_font(22, 500), fill=_MUTED)

        draw.line((left, divider_y, right, divider_y), fill=_DIVIDER, width=1)
        _cta(draw, left, cta_top, *MARKET_CTA)
        return _to_jpeg(card)


def render_broadcast_card_image(data: BroadcastCardData) -> bytes:
    side_color = SIDE_COLORS[data.side]
    fade_color = SIDE_COLORS[data.fade_side]
    tone = "green" if data.side == "YES" else "red"
    with _RENDER_LOCK:
        card, left, right, top, bottom = _frame(tone, side_color, STATUS_NEW_POSITION,
                                                green_mark=data.side == "YES")
        draw = ImageDraw.Draw(card)
        width = right - left
        title_end = _title_block(draw, data.title, left, top + 30, width)

        cta_top = bottom - 64
        divider_y = cta_top - 26

        # Trader vs open slot, centered between the title and the divider.
        radius = 64
        block_h = radius * 2 + 20 + 30 + 12 + 40 + 10 + 26
        block_top = title_end + (divider_y - title_end - block_h) // 2
        cy = block_top + radius
        col_l, col_r = left + width // 4, left + 3 * width // 4

        _orb(card, (col_l, cy), radius, side_color, data.initials)
        _open_slot(card, (col_r, cy), radius, _tint(fade_color, 0.55))
        draw = ImageDraw.Draw(card)
        _centered(draw, left + width / 2, cy - 16, "VS", _font(28, 700), _MUTED)

        name_y = cy + radius + 20
        _centered(draw, col_l, name_y, drawable(data.trader), _font(24, 500), _TEXT)
        _centered(draw, col_r, name_y, OPEN_SLOT, _font(24, 500), _MUTED)
        side_y = name_y + 42
        _centered(draw, col_l, side_y, f"{data.side} · {data.price_label}", _font(34, 650),
                  side_color)
        _centered(draw, col_r, side_y, f"{data.fade_side} · {data.fade_price_label}",
                  _font(34, 650), _tint(fade_color, 0.7))
        if data.amount_label:
            _centered(draw, col_l, side_y + 50, data.amount_label, _font(22, 500), _MUTED)

        draw.line((left, divider_y, right, divider_y), fill=_DIVIDER, width=1)
        _cta(draw, left, cta_top, *BROADCAST_CTA)
        return _to_jpeg(card)


def generate_market_card(card) -> bytes:
    return render_market_card_image(build_market_card_data(card))


def generate_broadcast_card(**kwargs) -> bytes:
    return render_broadcast_card_image(build_broadcast_card_data(**kwargs))
