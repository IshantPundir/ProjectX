"""Question banner overlaid on candidate clips (Pillow) — transparent top band.

The banner is a transparent full-frame PNG composited onto a clip via an ffmpeg
``overlay`` filter (see clips.cut_clip). Pillow is imported lazily inside
``render_question_banner`` so the pure ``plan_banner_texts`` helper (and its
tests) import cleanly in the lean nexus image; the renderer runs only in the
vision image.
"""
from __future__ import annotations

import html
import os

from app.modules.reel import cards

# Reuse the cards' brand surface so the overlay matches the cards.
_FONT_BOLD = cards._FONT_BOLD
_FONT_REG = cards._FONT_REG
_INK = cards._INK
_ACCENT_SOFT = cards._ACCENT_SOFT


def plan_banner_texts(clips: list[tuple[str | None, str | None]]) -> list[str | None]:
    """Per-clip banner text (or None) — show iff the question changed vs the
    IMMEDIATELY preceding clip. Dedup on question_id; fall back to label string
    when a question_id is None. A clip with no label never shows."""
    out: list[str | None] = []
    prev_qid: str | None = None
    prev_label: str | None = None
    first = True
    for qid, label in clips:
        if not label:
            show = False
        elif first:
            show = True
        elif qid is not None and prev_qid is not None:
            show = qid != prev_qid
        else:
            show = label != prev_label
        out.append(label if show else None)
        prev_qid, prev_label, first = qid, label, False
    return out


def render_question_banner(*, text: str, out_path: str,
                           width: int = cards.CARD_W, height: int = cards.CARD_H) -> str:
    """Render a transparent banner PNG: a dark top scrim + a violet ``Q`` + the
    wrapped white question, top-center. Returns ``out_path``."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Dark scrim across the top so white text reads over bright footage.
    scrim_h = 170
    scrim = Image.new("RGBA", (width, scrim_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(scrim_h):
        a = int(150 * (1 - y / scrim_h))   # 150 alpha at top → 0 at the band's base
        sd.line([(0, y), (width, y)], fill=(6, 11, 16, a))
    scrim = scrim.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(scrim, (0, 0))

    draw = ImageDraw.Draw(img)

    def font(path: str, size: int):
        return ImageFont.truetype(path, size)

    def text_w(s: str, f) -> float:
        return draw.textlength(s, font=f)

    qfont = font(_FONT_REG, 34)
    text = html.unescape(text.strip())
    # Wrap to <= 2 lines within 84% width.
    lines = cards.wrap_to_width(text, width * 0.84, lambda t: text_w(t, qfont))[:2]

    # Violet "Q" prefix sits to the left of the first line, top band.
    pre_font = font(_FONT_BOLD, 34)
    prefix = "Q"
    asc, desc = qfont.getmetrics()
    lh = asc + desc + 8
    block_h = lh * len(lines)
    top = 34
    pw = text_w(prefix + "  ", pre_font)
    # center the (prefix + widest line) block horizontally
    widest = max((text_w(ln, qfont) for ln in lines), default=0)
    block_w = pw + widest
    x0 = (width - block_w) / 2
    draw.text((x0, top), prefix, font=pre_font, fill=_ACCENT_SOFT)
    y = top
    for ln in lines:
        draw.text((x0 + pw, y), ln, font=qfont, fill=_INK)
        y += lh

    img.save(out_path, "PNG")
    return out_path
