"""Generate the 8 vertical section images using Yunwu gpt-image-2-all.

Key design points:

* **Every** section is rendered via the image-edit endpoint with the user's
  product photo as reference image #1, so the real product is integrated
  into every visual.
* For section N>=2, the **bottom strip** of section N-1 is passed as
  reference image #2. The prompt explicitly instructs the model that the
  top edge of section N must continue the colors/texture of that strip.
* All prompts are Jinja2 templates in ``app/prompts/sections/*.j2``,
  variables come from the ``ProductBrief`` and the ``LandingCopy``.
* Output size is exactly ``settings.image_size`` (default 1024×1536),
  which is the largest portrait Yunwu currently supports.

The orchestrator (``app.pipeline.Pipeline``) is responsible for running
sections in order when ``settings.seamless_flow`` is True; this module
just renders one section at a time.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image

from app.config import Settings, settings as default_settings
from app.schemas import LandingCopy, ProductBrief, SECTION_KEYS
from app.services.yunwu_client import YunwuClient, YunwuError

log = logging.getLogger(__name__)


SECTION_ROLES = {
    "hero":          "Strongest brand impression of the whole page.",
    "features":      "Showcase core features as vignettes around the product.",
    "before_after":  "A tasteful before/after mood, never medical.",
    "testimonials":  "Lifestyle ambience that suggests real customers love it.",
    "faq":           "Calm reading-room panel with space for Q&A overlays.",
    "lifestyle":     "The target user enjoying the product in daily life.",
    "education":     "Visual explainer — how / why the product works.",
    "closing":       "Final, confident, gift-worthy hero shot for the CTA.",
}


@dataclass
class GeneratedSection:
    key: str
    index: int
    prompt: str
    image_bytes: bytes  # exactly settings.image_width × settings.image_height PNG


class ImageGenerator:
    """Render a single section image using Yunwu's image-edit API."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts" / "sections"
        self._jinja = Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # ----------------------------------------------------------- public API

    def build_prompt(
        self,
        *,
        key: str,
        index: int,
        brief: ProductBrief,
        copy: LandingCopy,
        seamless_top: bool,
    ) -> str:
        tmpl = self._jinja.get_template(f"{key}.j2")
        ctx = {
            "section_number": index + 1,
            "section_role": SECTION_ROLES.get(key, ""),
            "brief": brief,
            "copy": copy,
            "palette_str": ", ".join(brief.palette_hex) if brief.palette_hex else "warm sand, ivory, soft gold",
            "style_keywords": ", ".join(brief.visual_style_keywords) if brief.visual_style_keywords else "editorial, premium, warm, modern Khaleeji",
            "seamless_top": seamless_top,
        }
        return tmpl.render(**ctx)

    async def render_section(
        self,
        *,
        key: str,
        index: int,
        brief: ProductBrief,
        copy: LandingCopy,
        product_image: bytes,
        previous_bottom_strip: bytes | None = None,
        prompt_override: str | None = None,
    ) -> tuple[str, bytes]:
        """Render a single section. Returns ``(prompt_used, normalized_png_bytes)``.

        ``product_image`` is mandatory — every section uses it as ref #1.
        ``previous_bottom_strip`` (optional) is passed as ref #2 to enforce
        seamless top continuation with the previous section.
        """

        seamless_top = previous_bottom_strip is not None
        prompt = prompt_override or self.build_prompt(
            key=key,
            index=index,
            brief=brief,
            copy=copy,
            seamless_top=seamless_top,
        )

        refs: list[bytes] = [product_image]
        if previous_bottom_strip is not None:
            refs.append(previous_bottom_strip)

        size = self.settings.image_size
        try:
            images = await self.client.image_edit(
                prompt=prompt,
                size=size,
                reference_images=refs,
                quality=self.settings.image_quality,
                fmt=self.settings.image_format,
            )
        except YunwuError as exc:
            # Fall back to single-reference if the edit endpoint chokes on
            # multi-image — keeps the pipeline alive.
            log.warning("image_edit failed with %d refs (%s); retrying with product only.", len(refs), exc)
            images = await self.client.image_edit(
                prompt=prompt,
                size=size,
                reference_images=[product_image],
                quality=self.settings.image_quality,
                fmt=self.settings.image_format,
            )
        if not images:
            raise YunwuError("Image API returned no images")
        normalized = _normalize_png(
            images[0],
            target_w=self.settings.image_width,
            target_h=self.settings.image_height,
        )
        return prompt, normalized


# ---------------------------------------------------------------- seam helpers


def extract_bottom_strip(png_bytes: bytes, *, strip_height: int) -> bytes:
    """Return the bottom ``strip_height`` rows of ``png_bytes`` as a PNG."""

    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB")
        h = im.height
        crop = im.crop((0, max(0, h - strip_height), im.width, h))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()


def blend_top_into_previous(
    current_png: bytes,
    previous_png: bytes,
    *,
    blend_height: int,
) -> bytes:
    """Alpha-blend the top ``blend_height`` rows of ``current_png`` with the
    bottom ``blend_height`` rows of ``previous_png`` to eliminate any
    remaining visible seam. Returns the new ``current_png`` PNG bytes.

    Pure PIL belt-and-suspenders pass — the model already does most of the
    work via the seam reference image.
    """

    if blend_height <= 0:
        return current_png

    with Image.open(io.BytesIO(current_png)) as cur, Image.open(io.BytesIO(previous_png)) as prev:
        cur = cur.convert("RGB")
        prev = prev.convert("RGB")
        if cur.width != prev.width:
            prev = prev.resize((cur.width, prev.height), Image.LANCZOS)

        h = blend_height
        prev_strip = prev.crop((0, prev.height - h, prev.width, prev.height))
        cur_strip = cur.crop((0, 0, cur.width, h))

        mask = Image.new("L", (cur.width, h))
        for row in range(h):
            # row=0 → 0% current (100% prev), row=h-1 → 100% current.
            v = round(255 * row / max(1, h - 1))
            mask.paste(v, (0, row, cur.width, row + 1))

        blended = Image.composite(cur_strip, prev_strip, mask)
        out = cur.copy()
        out.paste(blended, (0, 0))

        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# --------------------------------------------------------------------- private


def _normalize_png(image_bytes: bytes, *, target_w: int, target_h: int) -> bytes:
    """Resize/letterbox into exactly ``target_w × target_h`` PNG."""

    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        if (im.width, im.height) != (target_w, target_h):
            scale_w = target_w / im.width
            scale_h = target_h / im.height
            scale = max(scale_w, scale_h)  # cover
            new_w = round(im.width * scale)
            new_h = round(im.height * scale)
            im = im.resize((new_w, new_h), Image.LANCZOS)
            left = (im.width - target_w) // 2
            top = (im.height - target_h) // 2
            im = im.crop((left, top, left + target_w, top + target_h))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
