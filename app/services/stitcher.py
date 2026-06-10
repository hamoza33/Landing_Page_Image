"""Stitch 8 section images into one seamless tall PNG.

* Input: ordered list of section images (``GeneratedSection`` or raw bytes).
* Output: a single 1024 × 24576 PNG (8 × 3072).
* Seam blending: a 24-px alpha linear gradient at each join point.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw

from app.config import Settings, settings as default_settings

log = logging.getLogger(__name__)

SEAM_PX = 24  # height of the alpha-blend zone between adjacent sections


class Stitcher:
    """Combine section PNGs into the final long-image deliverable."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.width = self.settings.image_width
        self.section_h = self.settings.section_height

    def stitch(
        self,
        sections: list[bytes],
        *,
        output_path: Path | None = None,
    ) -> bytes:
        """Stitch and return the final PNG (also saves if ``output_path``)."""

        total_h = self.section_h * len(sections)
        canvas = Image.new("RGB", (self.width, total_h))

        images: list[Image.Image] = []
        for raw in sections:
            im = Image.open(io.BytesIO(raw)).convert("RGB").resize(
                (self.width, self.section_h), Image.LANCZOS
            )
            images.append(im)

        # Paste first section.
        canvas.paste(images[0], (0, 0))

        for idx in range(1, len(images)):
            y = idx * self.section_h
            canvas.paste(images[idx], (0, y))

            # Alpha-blend the seam zone.
            _blend_seam(canvas, images[idx - 1], images[idx], y, self.width, SEAM_PX)

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        png = buf.getvalue()

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(png)
            log.info("Saved stitched image (%d × %d) → %s", self.width, total_h, output_path)

        return png


def _blend_seam(
    canvas: Image.Image,
    top_img: Image.Image,
    bottom_img: Image.Image,
    join_y: int,
    width: int,
    seam: int,
) -> None:
    """Linear-gradient alpha blend in the seam region (centered on join_y).

    The seam is ``seam`` rows tall, centered on ``join_y``. The upper half
    is the bottom of the previous section; the lower half is the top of the
    current section. We composite a per-row mask so the transition is smooth.
    """

    half = seam // 2
    seam_h = seam
    if seam_h <= 0:
        return

    # Source strips: bottom of upper image, top of lower image — same height.
    upper_strip = top_img.crop((0, top_img.height - half, width, top_img.height))
    lower_strip = bottom_img.crop((0, 0, width, half))

    # Build a tall (seam_h) strip from each: pad upper with its own bottom row,
    # pad lower with its own top row. Easier: generate the full seam by stacking.
    upper_full = Image.new("RGB", (width, seam_h))
    lower_full = Image.new("RGB", (width, seam_h))
    upper_full.paste(upper_strip, (0, 0))
    upper_full.paste(
        upper_strip.crop((0, half - 1, width, half)).resize((width, seam_h - half)),
        (0, half),
    )
    lower_full.paste(
        lower_strip.crop((0, 0, width, 1)).resize((width, half)),
        (0, 0),
    )
    lower_full.paste(lower_strip, (0, half))

    # Build a vertical alpha gradient mask (0 at top → 255 at bottom).
    mask = Image.new("L", (width, seam_h))
    draw = ImageDraw.Draw(mask)
    for row in range(seam_h):
        v = round(255 * row / max(1, seam_h - 1))
        draw.line([(0, row), (width, row)], fill=v)

    blended = Image.composite(lower_full, upper_full, mask)
    dst_y = join_y - half
    canvas.paste(blended, (0, dst_y))
