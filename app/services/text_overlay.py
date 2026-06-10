"""Render Arabic text overlays onto generated section images.

Why we do this in PIL rather than asking the image model:
    - Diffusion image models are notoriously bad at rendering Arabic glyphs
      and ligatures correctly.
    - Doing the text in PIL gives us pixel-perfect control over typography,
      RTL bidi, line wrapping, and consistent brand styling.

How:
    1. Pick a layout per section (where to place the headline, subhead,
       body, CTA, list items).
    2. Reshape Arabic text with ``arabic-reshaper`` and apply bidirectional
       ordering with ``python-bidi`` so PIL draws the glyphs correctly.
    3. Draw a soft translucent panel behind the text so it stays readable
       on top of any background.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings, settings as default_settings
from app.schemas import LandingCopy

log = logging.getLogger(__name__)


# Section → ordered list of overlay specs to draw.
# Each spec is (vertical_anchor_ratio, role, lines_source_fn).
# vertical_anchor_ratio: 0.0 = top, 1.0 = bottom (where the BLOCK starts).
# role: "headline" | "subhead" | "body" | "cta" | "list"


@dataclass
class _Block:
    role: str
    text: str
    anchor: float          # 0..1 — where the block's TOP sits
    panel: bool = True     # draw translucent panel behind
    panel_align: str = "center"  # "center" | "right" | "left"


def _hex_to_rgba(h: str, default_alpha: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r, g, b, default_alpha)
    if len(h) == 8:
        r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        return (r, g, b, a)
    return (31, 41, 55, default_alpha)


def _shape_ar(text: str) -> str:
    if not text:
        return ""
    return get_display(arabic_reshaper.reshape(text))


class TextOverlay:
    """Draw Arabic copy onto a generated PNG."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    # ------------------------------------------------------------------ API

    def apply(
        self,
        *,
        section_key: str,
        png_bytes: bytes,
        copy: LandingCopy,
    ) -> bytes:
        if not self.settings.overlay_text_enabled:
            return png_bytes

        blocks = self._blocks_for(section_key, copy)
        if not blocks:
            return png_bytes

        with Image.open(io.BytesIO(png_bytes)) as im:
            base = im.convert("RGBA")

            text_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(text_layer)

            W, H = base.size
            pad_x = int(W * self.settings.overlay_padding_ratio)
            max_text_w = int(W * self.settings.overlay_max_width_ratio)

            for block in blocks:
                self._draw_block(draw, block, W, H, pad_x, max_text_w)

            composed = Image.alpha_composite(base, text_layer).convert("RGB")
            buf = io.BytesIO()
            composed.save(buf, format="PNG", optimize=True)
            return buf.getvalue()

    # ------------------------------------------------------------------ fonts

    def _font(self, *, bold: bool, size: int) -> ImageFont.FreeTypeFont:
        path = self.settings.font_arabic_bold if bold else self.settings.font_arabic_regular
        key = (str(path), size)
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        if not Path(path).exists():
            log.warning("Font missing at %s; falling back to PIL default.", path)
            font = ImageFont.load_default()
            self._font_cache[key] = font  # type: ignore[assignment]
            return font  # type: ignore[return-value]
        font = ImageFont.truetype(str(path), size)
        self._font_cache[key] = font
        return font

    # ------------------------------------------------------------- block plan

    def _blocks_for(self, key: str, copy: LandingCopy) -> list[_Block]:
        out: list[_Block] = []
        if key == "hero":
            out.append(_Block("headline", copy.hero.headline, anchor=0.06))
            out.append(_Block("subhead", copy.hero.subhead, anchor=0.20))
            out.append(_Block("cta", copy.hero.cta, anchor=0.86))
        elif key == "features":
            out.append(_Block("headline", copy.features.headline, anchor=0.05))
            text = "\n".join(f"• {it.title} — {it.description}" for it in copy.features.items)
            out.append(_Block("list", text, anchor=0.25))
        elif key == "before_after":
            out.append(_Block("headline", copy.before_after.headline, anchor=0.05))
            out.append(_Block("body", f"قبل: {copy.before_after.before}", anchor=0.42))
            out.append(_Block("body", f"بعد: {copy.before_after.after}", anchor=0.62))
        elif key == "testimonials":
            out.append(_Block("headline", copy.testimonials.headline, anchor=0.05))
            chunks = []
            for t in copy.testimonials.items:
                chunks.append(f"« {t.quote} »\n— {t.name} · {t.location}")
            out.append(_Block("body", "\n\n".join(chunks), anchor=0.22))
        elif key == "faq":
            out.append(_Block("headline", copy.faq.headline, anchor=0.05))
            chunks = [f"س: {it.question}\nج: {it.answer}" for it in copy.faq.items]
            out.append(_Block("body", "\n\n".join(chunks), anchor=0.18))
        elif key == "lifestyle":
            out.append(_Block("headline", copy.lifestyle.headline, anchor=0.62))
            out.append(_Block("body", copy.lifestyle.body, anchor=0.74))
        elif key == "education":
            out.append(_Block("headline", copy.education.headline, anchor=0.05))
            out.append(_Block("body", copy.education.body, anchor=0.18))
        elif key == "closing":
            out.append(_Block("headline", copy.closing.headline, anchor=0.10))
            out.append(_Block("body", copy.closing.body, anchor=0.32))
            out.append(_Block("cta", copy.closing.cta, anchor=0.82))
        return out

    # ------------------------------------------------------------- drawing

    def _draw_block(
        self,
        draw: ImageDraw.ImageDraw,
        block: _Block,
        W: int,
        H: int,
        pad_x: int,
        max_text_w: int,
    ) -> None:
        size = self._size_for_role(block.role)
        bold = block.role in {"headline", "cta"}
        font = self._font(bold=bold, size=size)
        text_color = _hex_to_rgba(self.settings.overlay_text_color)
        shadow_color = _hex_to_rgba(self.settings.overlay_shadow_color)

        # Wrap each input line into lines that fit max_text_w.
        wrapped: list[str] = []
        for raw_line in (block.text or "").split("\n"):
            wrapped.extend(_wrap_text(raw_line, font, max_text_w, draw))

        if not wrapped:
            return

        shaped = [_shape_ar(line) for line in wrapped]
        line_h = int(size * 1.45)
        block_h = line_h * len(shaped)

        top = int(H * block.anchor)
        # Translucent rounded panel behind the text for legibility.
        if block.panel:
            panel_pad = int(size * 0.55)
            text_widths = [draw.textlength(line, font=font) for line in shaped]
            text_w = int(max(text_widths)) if text_widths else 0
            panel_w = min(W - 2 * pad_x, text_w + 2 * panel_pad)
            panel_h = block_h + 2 * panel_pad
            panel_left = (W - panel_w) // 2
            panel_top = top - panel_pad // 2
            # Background panel: white with mild alpha for cosmetics-style legibility.
            panel_color = (255, 255, 255, 200)
            if block.role == "cta":
                panel_color = (24, 90, 70, 235)  # rich green CTA bar by default
            radius = int(size * 0.35)
            draw.rounded_rectangle(
                [panel_left, panel_top, panel_left + panel_w, panel_top + panel_h],
                radius=radius,
                fill=panel_color,
            )
            line_color = (255, 255, 255, 255) if block.role == "cta" else text_color
        else:
            line_color = text_color

        # Draw each line right-aligned (RTL) inside the available width.
        for i, line in enumerate(shaped):
            line_w = draw.textlength(line, font=font)
            x = (W - int(line_w)) // 2  # center each line within image
            y = top + i * line_h
            # Soft shadow for non-panel text only.
            if not block.panel:
                draw.text((x + 2, y + 2), line, font=font, fill=shadow_color)
            draw.text((x, y), line, font=font, fill=line_color)

    def _size_for_role(self, role: str) -> int:
        s = self.settings
        if role == "headline":
            return s.overlay_headline_size
        if role == "subhead":
            return s.overlay_subhead_size
        if role == "cta":
            return s.overlay_headline_size
        return s.overlay_body_size


# --------------------------------------------------------------------- wrap


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_w: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Greedy word-wrap on whitespace; respects empty lines."""

    if not text:
        return [""]
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = w if not cur else f"{cur} {w}"
        if draw.textlength(_shape_ar(candidate), font=font) <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
