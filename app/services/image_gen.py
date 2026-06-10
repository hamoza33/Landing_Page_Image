"""Generate the 8 vertical section images for a landing page.

Strategy (per the plan):

* Build a *style bible* string from the product brief — palette, materials,
  cultural cues — and inject it into every section prompt for visual continuity.
* For each of the 8 sections, build a scene prompt from the relevant slice of
  Arabic copy plus a section-specific staging direction (hero, features grid,
  before/after, etc.). Forbid flat backgrounds and embedded text.
* Request 1024×3072 (Yunwu's documented 3:1 cap). If the API refuses that
  custom size, fall back to 1024×1536 (preset) and pad to 3072 in PIL with a
  reflective + lightly blurred band so the final image is still 1:3.
* Hero + lifestyle optionally use the image-edit endpoint with the user's
  product photo as a reference, so the *real* product appears in those scenes.
* Run all 8 in parallel under a semaphore.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable

from PIL import Image, ImageFilter

from app.config import Settings, settings as default_settings
from app.schemas import LandingCopy, ProductBrief, SECTION_KEYS
from app.services.yunwu_client import YunwuClient, YunwuError

log = logging.getLogger(__name__)


# Sections that benefit from anchoring on the user's actual product photo.
EDIT_SECTIONS = {"hero", "lifestyle"}

# Yunwu preset that's known-good as a fallback when 1024x3072 is rejected.
FALLBACK_SIZE = "1024x1536"


@dataclass
class GeneratedSection:
    key: str
    index: int
    prompt: str
    image_bytes: bytes  # always normalized to settings.image_width × settings.section_height PNG


class ImageGenerator:
    """Generate the 8 portrait section images, visually consistent."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)

    @staticmethod
    def fresh_seed() -> int:
        return random.randint(10_000, 9_999_999)

    async def render_section(
        self,
        *,
        key: str,
        index: int,
        brief: ProductBrief,
        copy: LandingCopy,
        seed: int,
        product_image: bytes | None = None,
        prompt_override: str | None = None,
    ) -> tuple[str, bytes]:
        """Render a single section; returns (prompt_used, normalized_png_bytes)."""

        style = self._style_bible(brief)
        prompt = prompt_override if prompt_override else self._build_section_prompt(
            key, index, style, copy, seed
        )
        use_edit = product_image is not None and key in EDIT_SECTIONS
        img_bytes = await self._call_image_api(
            prompt=prompt,
            use_edit=use_edit,
            product_image=product_image,
        )
        normalized = _normalize_to_size(
            img_bytes,
            target_w=self.settings.image_width,
            target_h=self.settings.section_height,
        )
        return prompt, normalized

    async def generate_all(
        self,
        brief: ProductBrief,
        copy: LandingCopy,
        *,
        product_image: bytes | None = None,
    ) -> list[GeneratedSection]:
        """Render all 8 sections in parallel (used by tests / CLI)."""

        seed = self.fresh_seed()
        sem = asyncio.Semaphore(max(1, self.settings.image_concurrency))

        async def run_one(idx: int, key: str) -> GeneratedSection:
            async with sem:
                prompt, normalized = await self.render_section(
                    key=key,
                    index=idx,
                    brief=brief,
                    copy=copy,
                    seed=seed,
                    product_image=product_image,
                )
                return GeneratedSection(
                    key=key,
                    index=idx,
                    prompt=prompt,
                    image_bytes=normalized,
                )

        tasks = [run_one(i, k) for i, k in enumerate(SECTION_KEYS)]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------ building blocks

    @staticmethod
    def _style_bible(brief: ProductBrief) -> str:
        keywords = ", ".join(brief.visual_style_keywords) if brief.visual_style_keywords else (
            "warm sand and ivory palette, soft golden hour light, modern Khaleeji "
            "aesthetic, refined editorial mood"
        )
        materials = ", ".join(brief.materials) if brief.materials else "natural premium materials"
        return (
            f"Editorial product photography illustration for the GCC market. "
            f"Subject: {brief.name} ({brief.category}). Materials: {materials}. "
            f"Visual style: {keywords}. Cohesive color story, high craft, "
            f"never a flat solid-color background — always include subtle texture, "
            f"depth, gradients, props, or environmental detail. No embedded text, "
            f"no logos, no watermarks. Vertical composition, top edge naturally "
            f"connects to bottom edge of the previous section."
        )

    @staticmethod
    def _section_scene(key: str, copy: LandingCopy) -> str:
        """Per-section staging direction in English (for the image model)."""

        if key == "hero":
            h = copy.hero
            return (
                f"Hero scene establishing the product as a desirable object of "
                f"focus. Centered composition, dramatic lighting. Mood: "
                f"\"{h.headline} — {h.subhead}\"."
            )
        if key == "features":
            f = copy.features
            n = len(f.items)
            return (
                f"Feature showcase scene: the product depicted from multiple "
                f"angles or with up to {n} small contextual vignettes around it. "
                f"Clean, premium catalog vibe."
            )
        if key == "before_after":
            ba = copy.before_after
            return (
                f"Two-state comparison scene. Top half: a 'before' state — "
                f"\"{ba.before}\". Bottom half: 'after' state with the product — "
                f"\"{ba.after}\". Soft visual divider, NOT a hard split line."
            )
        if key == "testimonials":
            return (
                "Lifestyle portraits scene: 2-3 abstract / silhouetted GCC users "
                "(no facial detail) shown enjoying the product in tasteful, "
                "respectful settings (modern majlis, kitchen, balcony at dusk)."
            )
        if key == "faq":
            return (
                "Calm explanatory scene: the product on a textured surface with "
                "abstract icon-like elements floating around it (question marks, "
                "leaves, sparkles) — illustrative, not literal UI."
            )
        if key == "lifestyle":
            return (
                "Lifestyle hero: the product integrated into a real Khaleeji "
                "daily moment — modern Gulf interior, soft daylight, lived-in "
                "warmth. Product is the focal point but feels naturally placed."
            )
        if key == "education":
            return (
                "How-it-works scene: cutaway / exploded illustration showing the "
                "key components or steps, infographic style but painterly, never "
                "flat. Limited to visual elements only."
            )
        if key == "closing":
            return (
                "Closing scene: the product elevated on a pedestal-like form, "
                "with rising light or particles, an aspirational 'final note' "
                "feel. Strong vertical lift toward the top."
            )
        return "Editorial vertical product scene."

    def _build_section_prompt(
        self,
        key: str,
        idx: int,
        style: str,
        copy: LandingCopy,
        seed: int,
    ) -> str:
        scene = self._section_scene(key, copy)
        # Connection cue so adjacent sections feel continuous.
        if idx == 0:
            connection = "This is section 1 of 8 — sets the visual tone."
        elif idx == len(SECTION_KEYS) - 1:
            connection = (
                f"This is section 8 of 8 — its TOP edge must visually continue "
                f"from the previous section's bottom palette and texture."
            )
        else:
            connection = (
                f"This is section {idx + 1} of 8 — both TOP and BOTTOM edges "
                f"must blend smoothly with neighboring sections (same palette, "
                f"texture continuity)."
            )
        return (
            f"{style}\n\n{scene}\n\n{connection}\n"
            f"Tall portrait orientation, 1:3 aspect ratio. "
            f"Style seed reference: {seed}-{key}."
        )

    # --------------------------------------------------------------- API call

    async def _call_image_api(
        self,
        *,
        prompt: str,
        use_edit: bool,
        product_image: bytes | None,
    ) -> bytes:
        """Try ideal 1024x3072, fall back to 1024x1536 on 4xx."""

        ideal = f"{self.settings.image_width}x{self.settings.section_height}"
        sizes_to_try = [ideal, FALLBACK_SIZE]

        last_err: Exception | None = None
        for size in sizes_to_try:
            try:
                if use_edit and product_image is not None:
                    images = await self.client.image_edit(
                        prompt=prompt,
                        size=size,
                        reference_images=[product_image],
                    )
                else:
                    images = await self.client.image(prompt=prompt, size=size)
                if images:
                    return images[0]
            except YunwuError as exc:
                last_err = exc
                log.warning("Image API failed at size=%s: %s", size, exc)
                continue
        raise YunwuError(f"All image sizes failed; last error: {last_err}")


# --------------------------------------------------------------------- helpers


def _normalize_to_size(image_bytes: bytes, *, target_w: int, target_h: int) -> bytes:
    """Resize/pad arbitrary image to exactly ``target_w × target_h`` PNG."""

    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        # Scale so width matches target while preserving aspect ratio.
        new_h = max(1, round(im.height * (target_w / im.width)))
        im = im.resize((target_w, new_h), Image.LANCZOS)

        if im.height == target_h:
            out = im
        elif im.height > target_h:
            # Crop centered vertically.
            top = (im.height - target_h) // 2
            out = im.crop((0, top, target_w, top + target_h))
        else:
            # Pad with reflected + lightly blurred edges so the seam isn't obvious.
            out = Image.new("RGB", (target_w, target_h))
            top_pad = (target_h - im.height) // 2
            out.paste(im, (0, top_pad))

            # Top band: reflect first slice of original, blur.
            top_band_h = top_pad
            if top_band_h > 0:
                slice_h = min(top_band_h, im.height)
                top_slice = im.crop((0, 0, target_w, slice_h)).transpose(Image.FLIP_TOP_BOTTOM)
                top_band = top_slice.resize((target_w, top_band_h), Image.LANCZOS)
                top_band = top_band.filter(ImageFilter.GaussianBlur(radius=12))
                out.paste(top_band, (0, 0))

            # Bottom band.
            bottom_band_h = target_h - (top_pad + im.height)
            if bottom_band_h > 0:
                slice_h = min(bottom_band_h, im.height)
                bottom_slice = im.crop((0, im.height - slice_h, target_w, im.height)).transpose(
                    Image.FLIP_TOP_BOTTOM
                )
                bottom_band = bottom_slice.resize((target_w, bottom_band_h), Image.LANCZOS)
                bottom_band = bottom_band.filter(ImageFilter.GaussianBlur(radius=12))
                out.paste(bottom_band, (0, top_pad + im.height))

        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# Convenience wrapper used by the orchestrator.
async def generate_sections(
    brief: ProductBrief,
    copy: LandingCopy,
    *,
    product_image: bytes | None = None,
    client: YunwuClient | None = None,
    settings: Settings | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> list[GeneratedSection]:
    gen = ImageGenerator(client=client, settings=settings)
    if progress:
        await progress("generating 8 section images")
    return await gen.generate_all(brief, copy, product_image=product_image)
