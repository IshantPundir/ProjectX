"""Brand cards for the reel (Pillow) — 1280x720 dark-cinematic, violet accent.

Card beats (point/outro) render to a PNG that the renderer turns into
a video segment under Arjun's narration. Clips (candidate footage) are NOT cards.

Pillow is imported lazily inside ``render_card`` so the pure ``wrap_to_width``
helper (and its tests) import cleanly in the lean nexus image. The renderer runs
only in the vision image (Pillow present).
"""
from __future__ import annotations

import html
import os
from typing import Callable

# 16:9, matches clips.TARGET_W/H so the concat demuxer joins without re-encode.
CARD_W, CARD_H = 1280, 720

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_WORDMARK = os.path.join(_ASSETS, "binqle-wordmark.png")
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Brand palette (dark-cinematic over the BinQle violet accent).
_BG_TOP = (12, 22, 32)       # #0C1620
_BG_BOT = (8, 14, 20)        # #080E14
_INK = (255, 255, 255)
_INK_SOFT = (195, 203, 206)  # #C3CBCE
_ACCENT = (108, 92, 208)     # #6C5CD0
_ACCENT_SOFT = (155, 143, 224)  # #9B8FE0
_WORDMARK_INK = (234, 238, 241)  # recolor the dark logo light so it reads


def wrap_to_width(text: str, max_width: float, measure: Callable[[str], float]) -> list[str]:
    """Greedy word-wrap ``text`` so each line measures <= ``max_width``.

    ``measure`` returns a line's rendered width (inject ``font.getlength`` in the
    renderer; a length stand-in in tests). A single word wider than ``max_width``
    takes its own line (no character splitting).
    """
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def format_identity_tag(candidate_name: str | None, role_title: str | None) -> str | None:
    """Build the first-card identity subtitle: ``"FirstName · Role Title"``.

    Pure + deterministic — identity is a known fact, never LLM-authored. Degrades
    gracefully: only one part present → just that part; neither → ``None``.
    """
    first = (candidate_name or "").strip().split()
    name = first[0] if first else ""
    role = (role_title or "").strip()
    parts = [p for p in (name, role) if p]
    return " · ".join(parts) if parts else None


# Point-card polarity: which moment is this card framing?
_POINT_GLYPHS = ("★", "✓", "△")


def parse_point_glyph(on_screen_text: str) -> tuple[str, str, tuple[int, int, int]]:
    """Split a point card's leading polarity glyph from its phrase + pick its color.

    ``★`` (differentiating strength) and ``✓`` (met requirement) render in the violet
    accent. ``△`` (a gap / unmet requirement) renders in NEUTRAL soft ink — this is
    evidence behind a verdict, not an alarm. Missing glyph → defaults to ``★``.
    """
    text = (on_screen_text or "").strip()
    glyph = "★"
    for g in _POINT_GLYPHS:
        if text.startswith(g):
            glyph = g
            text = text[len(g):].strip()
            break
    color = _INK_SOFT if glyph == "△" else _ACCENT_SOFT
    return glyph, text, color


def render_card(*, kind: str, out_path: str, on_screen_text: str | None = None,
                subtitle: str | None = None,
                width: int = CARD_W, height: int = CARD_H) -> str:
    """Render one card beat to ``out_path`` (PNG). Returns the path."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = _background(Image, ImageDraw, ImageFilter, width, height)
    draw = ImageDraw.Draw(img)

    def font(path: str, size: int) -> "ImageFont.FreeTypeFont":
        return ImageFont.truetype(path, size)

    def text_w(s: str, f) -> float:
        return draw.textlength(s, font=f)

    def centered_block(s: str, f, *, top: int, fill, line_gap: int = 14,
                       max_frac: float = 0.82) -> int:
        """Draw a centered, wrapped block starting at y=top; return y after it."""
        lines = wrap_to_width(s, width * max_frac, lambda t: text_w(t, f))
        asc, desc = f.getmetrics()
        lh = asc + desc + line_gap
        y = top
        for ln in lines:
            w = text_w(ln, f)
            draw.text(((width - w) / 2, y), ln, font=f, fill=fill)
            y += lh
        return y

    # Unescape HTML entities the LLM sometimes emits (e.g. "&amp;" -> "&").
    text = html.unescape((on_screen_text or "").strip())
    if kind == "point":
        glyph, phrase, glyph_color = parse_point_glyph(text)
        gfont = font(_FONT_BOLD, 96)
        gw = text_w(glyph, gfont)
        draw.text(((width - gw) / 2, 170), glyph, font=gfont, fill=glyph_color)
        phrase = phrase or text
        y = centered_block(phrase, font(_FONT_BOLD, 54), top=320, fill=_INK)
        if subtitle:
            centered_block(subtitle, font(_FONT_REG, 30), top=y + 22, fill=_INK_SOFT)
    elif kind == "outro":
        centered_block(text, font(_FONT_BOLD, 50), top=230, fill=_INK)
        _cta_pill(draw, font(_FONT_BOLD, 30), "▶  Watch full interview", width, y=470)
        _paste_wordmark(Image, img, y=600, target_w=220)
    else:  # generic fallback
        centered_block(text, font(_FONT_BOLD, 52), top=300, fill=_INK)

    img.convert("RGB").save(out_path, "PNG")
    return out_path


def _background(Image, ImageDraw, ImageFilter, width: int, height: int):
    """Vertical dark gradient + a soft violet glow, top-right."""
    base = Image.new("RGB", (width, height), _BG_BOT)
    grad = Image.new("RGB", (1, height))
    gp = grad.load()
    for y in range(height):
        t = y / max(1, height - 1)
        gp[0, y] = tuple(int(a + (b - a) * t) for a, b in zip(_BG_TOP, _BG_BOT))
    base.paste(grad.resize((width, height)), (0, 0))

    glow = Image.new("RGB", (width, height), _BG_BOT)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([width - 520, -260, width + 200, 320], fill=_ACCENT)
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    return Image.blend(base, glow, 0.22)


def _paste_wordmark(Image, img, *, y: int, target_w: int) -> None:
    """Composite the wordmark, recolored light (its alpha as a mask), centered-x."""
    if not os.path.exists(_WORDMARK):
        return
    mark = Image.open(_WORDMARK).convert("RGBA")
    scale = target_w / mark.width
    mark = mark.resize((target_w, max(1, int(mark.height * scale))))
    light = Image.new("RGBA", mark.size, (*_WORDMARK_INK, 255))
    light.putalpha(mark.getchannel("A"))
    img.paste(light, ((img.width - target_w) // 2, y), light)


def _cta_pill(draw, f, s: str, width: int, *, y: int) -> None:
    tw = draw.textlength(s, font=f)
    asc, desc = f.getmetrics()
    pad_x, pad_y = 34, 18
    pw, ph = tw + pad_x * 2, asc + desc + pad_y * 2
    x0 = (width - pw) / 2
    draw.rounded_rectangle([x0, y, x0 + pw, y + ph], radius=ph / 2, fill=_ACCENT)
    draw.text((x0 + pad_x, y + pad_y), s, font=f, fill=_INK)
