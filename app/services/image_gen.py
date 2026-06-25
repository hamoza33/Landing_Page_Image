"""Generate the 8 vertical section images for a landing page.

Strategy:

* Build a *style bible* string from the product brief for visual consistency.
* Generate sections SEQUENTIALLY (hero first, closing last) so each image
  can reference the bottom crop of the previous section for seamless continuity.
* Every prompt includes Arabic text rendering instructions and the actual Arabic
  copy content from LandingCopy.
* Strong anti-duplication instructions ensure no two consecutive sections repeat
  the same text, icons, or visual elements.
* All sections use image_edit with reference images:
  - Sections WITH product ref (hero, features, before_after, lifestyle, closing):
    send [product_image, prev_bottom_crop] (hero only gets [product_image]).
  - Sections WITHOUT product ref (testimonials, faq, education):
    send [prev_bottom_crop] only but include product concept description in prompt.
* Use gpt-image-2 model with the image parameter.
* Arabic text is rendered sharp, crisp, and high-resolution.
"""

from __future__ import annotations

import io
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable

from PIL import Image

from app.config import Settings, settings as default_settings
from app.schemas import LandingCopy, ProductBrief, SECTION_KEYS
from app.services.yunwu_client import YunwuClient, YunwuError

log = logging.getLogger(__name__)


# Sections that receive the product photo as a reference image.
PRODUCT_REF_SECTIONS: set[str] = {"hero", "features", "before_after", "lifestyle", "closing"}

# Yunwu preset that's known-good as a fallback when 1024x3072 is rejected.
FALLBACK_SIZE = "1024x1536"


@dataclass
class GeneratedSection:
    key: str
    index: int
    prompt: str
    image_bytes: bytes  # always normalized to settings.image_width x settings.section_height PNG


class ImageGenerator:
    """Generate the 8 portrait section images sequentially for seamless continuity."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)

    async def generate_all(
        self,
        brief: ProductBrief,
        copy: LandingCopy,
        *,
        product_image: bytes | None = None,
        advertiser_angle: str | None = None,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[GeneratedSection]:
        style = self._style_bible(brief)
        seed = random.randint(10_000, 9_999_999)

        results: list[GeneratedSection] = []
        prev_bottom_crop: bytes | None = None

        for idx, key in enumerate(SECTION_KEYS):
            if progress:
                await progress(
                    f"generating section {idx + 1} of {len(SECTION_KEYS)}: {key}"
                )

            # Get the previous section key for anti-duplication context
            prev_key = SECTION_KEYS[idx - 1] if idx > 0 else None

            prompt = self._build_section_prompt(
                key, idx, style, copy, seed, brief,
                advertiser_angle=advertiser_angle,
                prev_key=prev_key,
            )

            # Build reference images list
            reference_images: list[bytes] = []
            if idx == 0:
                # Hero: only product image
                if product_image is not None:
                    reference_images = [product_image]
            else:
                # Subsequent sections
                if key in PRODUCT_REF_SECTIONS and product_image is not None:
                    reference_images = [product_image]
                    if prev_bottom_crop is not None:
                        reference_images.append(prev_bottom_crop)
                else:
                    # Sections without product ref
                    if prev_bottom_crop is not None:
                        reference_images = [prev_bottom_crop]

            img_bytes = await self._call_image_api(
                prompt=prompt,
                reference_images=reference_images,
            )
            normalized = _normalize_to_size(
                img_bytes,
                target_w=self.settings.image_width,
                target_h=self.settings.section_height,
            )

            # Crop bottom of this section for the next section's continuity reference
            prev_bottom_crop = _crop_bottom(normalized, height=200)

            results.append(GeneratedSection(
                key=key,
                index=idx,
                prompt=prompt,
                image_bytes=normalized,
            ))

        return results

    async def regenerate_section(
        self,
        brief: ProductBrief,
        copy: LandingCopy,
        *,
        section_key: str,
        custom_prompt: str | None = None,
        product_image: bytes | None = None,
        prev_section_image: bytes | None = None,
        advertiser_angle: str | None = None,
    ) -> GeneratedSection:
        """Regenerate a single section with optional custom prompt."""
        idx = list(SECTION_KEYS).index(section_key)

        if custom_prompt:
            prompt = custom_prompt
        else:
            style = self._style_bible(brief)
            seed = random.randint(10_000, 9_999_999)
            prev_key = SECTION_KEYS[idx - 1] if idx > 0 else None
            prompt = self._build_section_prompt(
                section_key, idx, style, copy, seed, brief,
                advertiser_angle=advertiser_angle,
                prev_key=prev_key,
            )

        # Build reference images
        reference_images: list[bytes] = []
        if idx == 0:
            if product_image is not None:
                reference_images = [product_image]
        else:
            if section_key in PRODUCT_REF_SECTIONS and product_image is not None:
                reference_images = [product_image]
                if prev_section_image is not None:
                    prev_crop = _crop_bottom(prev_section_image, height=200)
                    reference_images.append(prev_crop)
            else:
                if prev_section_image is not None:
                    prev_crop = _crop_bottom(prev_section_image, height=200)
                    reference_images = [prev_crop]

        img_bytes = await self._call_image_api(
            prompt=prompt,
            reference_images=reference_images,
        )
        normalized = _normalize_to_size(
            img_bytes,
            target_w=self.settings.image_width,
            target_h=self.settings.section_height,
        )

        return GeneratedSection(
            key=section_key,
            index=idx,
            prompt=prompt,
            image_bytes=normalized,
        )

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
            f"never a flat solid-color background - always include subtle texture, "
            f"depth, gradients, props, or environmental detail. "
            f"Vertical composition, top edge naturally "
            f"connects to bottom edge of the previous section."
        )

    @staticmethod
    def _get_section_arabic_text(key: str, copy: LandingCopy) -> str:
        """Extract the Arabic text content for a section to embed in prompts."""
        if key == "hero":
            h = copy.hero
            return (
                f"Arabic headline text: {h.headline}\n"
                f"Arabic subheadline: {h.subhead}\n"
                f"Arabic CTA button: {h.cta}"
            )
        if key == "features":
            f = copy.features
            items_text = "\n".join(
                f"- {item.title}: {item.description}" for item in f.items
            )
            return (
                f"Arabic section headline: {f.headline}\n"
                f"Arabic feature items:\n{items_text}"
            )
        if key == "before_after":
            ba = copy.before_after
            return (
                f"Arabic section headline: {ba.headline}\n"
                f"Arabic before state: {ba.before}\n"
                f"Arabic after state: {ba.after}"
            )
        if key == "testimonials":
            t = copy.testimonials
            items_text = "\n".join(
                f"- {item.name} ({item.location}): {item.quote}" for item in t.items
            )
            return (
                f"Arabic section headline: {t.headline}\n"
                f"Arabic testimonials:\n{items_text}"
            )
        if key == "faq":
            fq = copy.faq
            items_text = "\n".join(
                f"- {item.question} / {item.answer}" for item in fq.items
            )
            return (
                f"Arabic section headline: {fq.headline}\n"
                f"Arabic Q&A:\n{items_text}"
            )
        if key == "lifestyle":
            ls = copy.lifestyle
            return (
                f"Arabic section headline: {ls.headline}\n"
                f"Arabic body text: {ls.body}"
            )
        if key == "education":
            ed = copy.education
            return (
                f"Arabic section headline: {ed.headline}\n"
                f"Arabic body text: {ed.body}"
            )
        if key == "closing":
            cl = copy.closing
            return (
                f"Arabic section headline: {cl.headline}\n"
                f"Arabic body text: {cl.body}\n"
                f"Arabic CTA button: {cl.cta}"
            )
        return ""

    def _build_section_prompt(
        self,
        key: str,
        idx: int,
        style: str,
        copy: LandingCopy,
        seed: int,
        brief: ProductBrief,
        *,
        advertiser_angle: str | None = None,
        prev_key: str | None = None,
    ) -> str:
        scene = self._section_scene(key, copy)
        arabic_text = self._get_section_arabic_text(key, copy)

        # Connection and continuity instructions
        if idx == 0:
            connection = (
                "This is section 1 of 8, it sets the visual tone for the entire landing page. "
                "The first reference image is the product photo - use it as visual anchor."
            )
        else:
            product_ref_note = ""
            if key in PRODUCT_REF_SECTIONS:
                product_ref_note = (
                    "The first reference image is the product photo - ensure the product "
                    "appears prominently in this scene. "
                    "The second reference image shows the bottom of the previous section."
                )
            else:
                # For non-product sections, describe product concept in words
                product_concept = (
                    f"Although no product photo is included as reference for this section, "
                    f"maintain the visual identity of the product ({brief.name}, {brief.category}). "
                    f"Use the color palette, materials ({', '.join(brief.materials) if brief.materials else 'premium materials'}), "
                    f"and thematic elements that relate to the product without showing the actual product photo. "
                    f"The reference image shows the bottom of the previous section."
                )
                product_ref_note = product_concept

            connection = (
                f"This is section {idx + 1} of 8. "
                f"The TOP of this image must seamlessly continue from the bottom of the "
                f"previous section - match colors, lighting, and texture exactly. "
                f"{product_ref_note}"
            )

        # Anti-duplication instructions
        anti_duplication = (
            "CRITICAL - NO DUPLICATION ALLOWED: "
        )
        if prev_key:
            prev_arabic = self._get_section_arabic_text(prev_key, copy)
            anti_duplication += (
                f"The previous section was [{prev_key}]. "
                f"DO NOT repeat any text, heading, icon, visual element, or layout pattern "
                f"from the previous section. Every piece of text in this section must be "
                f"completely different from what appeared before. "
                f"The previous section contained this text (DO NOT use any of it again): "
                f"{prev_arabic[:100]}... "
                f"This section must show ONLY the text listed below and nothing else. "
                f"Do not duplicate any icons, decorative elements, or graphical motifs "
                f"that were already used in prior sections."
            )
        else:
            anti_duplication += (
                "Each section of this landing page must be unique. "
                "Do not repeat text, icons, or visual patterns in later sections."
            )

        # Arabic text rendering instructions - emphasize sharpness
        arabic_instructions = (
            "ARABIC TEXT RENDERING - SHARP AND CLEAR: "
            "Render the following Arabic text directly in the image using crisp, "
            "sharp, pixel-perfect Arabic typography. The text must be rendered at "
            "high resolution with clean edges - absolutely NO blurriness, NO painted "
            "or hand-drawn text effect, NO watercolor text. Use modern digital Arabic "
            "fonts with precise letterforms. Text must be perfectly legible and "
            "razor-sharp at any zoom level. Right-to-left direction. "
            "Include decorative icons and visual elements that complement the text "
            "but keep the text itself pristine and sharp.\n\n"
            f"{arabic_text}"
        )

        # Advertiser angle incorporation
        angle_text = ""
        if advertiser_angle:
            if idx == 0:
                # Hero section gets the full marketing angle
                angle_text = (
                    f"\n\nMARKETING ANGLE: This landing page promotes the following angle: "
                    f"{advertiser_angle}. Make this angle the central theme of the hero section. "
                    f"The headline and visual composition should immediately communicate this message."
                )
            else:
                # Other sections get a derived/supporting angle based on section type
                section_angle_map = {
                    "features": f"Show how the product features support the main promise of: {advertiser_angle}. Focus on specific capabilities.",
                    "before_after": f"Illustrate the transformation that happens when using this product, connected to the angle: {advertiser_angle}.",
                    "testimonials": f"Show social proof and real-life satisfaction related to the promise of: {advertiser_angle}.",
                    "faq": f"Address common questions buyers have about the product, relating to: {advertiser_angle}.",
                    "lifestyle": f"Show the aspirational lifestyle achieved through the product, embodying the spirit of: {advertiser_angle}.",
                    "education": f"Explain how the product works to deliver on the promise of: {advertiser_angle}.",
                    "closing": f"Create urgency and a final call to action reinforcing: {advertiser_angle}.",
                }
                derived = section_angle_map.get(key, f"Support the overall marketing angle: {advertiser_angle}")
                angle_text = (
                    f"\n\nSECTION MARKETING FOCUS: {derived} "
                    f"Do NOT repeat the exact marketing phrase from the hero - use a fresh perspective "
                    f"that builds on the same idea but says it differently."
                )

        return (
            f"{style}\n\n"
            f"{scene}\n\n"
            f"{connection}\n\n"
            f"{anti_duplication}\n\n"
            f"{arabic_instructions}"
            f"{angle_text}\n\n"
            f"Tall portrait orientation, 1:3 aspect ratio. "
            f"Style seed reference: {seed}-{key}."
        )

    @staticmethod
    def _section_scene(key: str, copy: LandingCopy) -> str:
        """Per-section staging direction."""

        if key == "hero":
            return (
                "Hero scene establishing the product as a desirable object of "
                "focus. Centered composition, dramatic lighting, premium editorial feel. "
                "The product should be the star of this opening scene."
            )
        if key == "features":
            f = copy.features
            n = len(f.items)
            return (
                f"Feature showcase scene: the product depicted from multiple "
                f"angles or with up to {n} small contextual vignettes around it. "
                f"Clean, premium catalog vibe with feature highlight areas."
            )
        if key == "before_after":
            return (
                "Two-state comparison scene. Top portion shows a before state "
                "without the product. Bottom portion shows the after state with the "
                "product present and the improvement visible. "
                "Soft visual transition between the two states."
            )
        if key == "testimonials":
            return (
                "Lifestyle portraits scene: 2-3 abstract or silhouetted GCC users "
                "(no facial detail) shown enjoying the product in tasteful, "
                "respectful settings such as a modern majlis, kitchen, or balcony at dusk. "
                "Include decorative quote marks and testimonial card areas."
            )
        if key == "faq":
            return (
                "Calm explanatory scene: the product on a textured surface with "
                "abstract icon-like elements floating around it such as question marks, "
                "leaves, and sparkles. Illustrative style with areas for text content."
            )
        if key == "lifestyle":
            return (
                "Lifestyle hero: the product integrated into a real Khaleeji "
                "daily moment in a modern Gulf interior with soft daylight and lived-in "
                "warmth. Product is the focal point but feels naturally placed."
            )
        if key == "education":
            return (
                "How-it-works scene: cutaway or exploded illustration showing the "
                "key components or steps, infographic style but painterly. "
                "Include numbered step areas and icon elements."
            )
        if key == "closing":
            return (
                "Closing scene: the product elevated on a pedestal-like form, "
                "with rising light or particles, an aspirational final note "
                "feel. Strong vertical lift toward the top with a call-to-action area."
            )
        return "Editorial vertical product scene."

    # --------------------------------------------------------------- API call

    async def _call_image_api(
        self,
        *,
        prompt: str,
        reference_images: list[bytes],
    ) -> bytes:
        """Generate an image using image_edit with reference images.

        Always uses image_edit since we always have at least one reference image.
        Falls back to smaller size on 4xx errors.
        """

        ideal = f"{self.settings.image_width}x{self.settings.section_height}"
        sizes_to_try = [ideal, FALLBACK_SIZE]

        last_err: Exception | None = None
        for size in sizes_to_try:
            try:
                if reference_images:
                    images = await self.client.image_edit(
                        prompt=prompt,
                        size=size,
                        reference_images=reference_images,
                    )
                else:
                    # Fallback for case with no references (should not happen normally)
                    images = await self.client.image(prompt=prompt, size=size)
                if images:
                    return images[0]
            except YunwuError as exc:
                last_err = exc
                log.warning("Image API failed at size=%s: %s", size, exc)
                continue
        raise YunwuError(f"All image sizes failed; last error: {last_err}")


# --------------------------------------------------------------------- helpers


def _crop_bottom(image_bytes: bytes, height: int = 200) -> bytes:
    """Crop the bottom N pixels from an image, returned as PNG bytes."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        crop_h = min(height, h)
        cropped = im.crop((0, h - crop_h, w, h))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()


def _normalize_to_size(image_bytes: bytes, *, target_w: int, target_h: int) -> bytes:
    """Resize/pad arbitrary image to exactly ``target_w x target_h`` PNG.
    
    Uses scale-to-fill with center crop to avoid any blurred padding.
    """

    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        
        # Scale to FILL the target dimensions (no empty space)
        scale_w = target_w / im.width
        scale_h = target_h / im.height
        scale = max(scale_w, scale_h)  # Use the larger scale to fill completely
        
        new_w = max(1, round(im.width * scale))
        new_h = max(1, round(im.height * scale))
        im = im.resize((new_w, new_h), Image.LANCZOS)

        # Center crop to exact target size
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        out = im.crop((left, top, left + target_w, top + target_h))

        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# Convenience wrapper used by the orchestrator.
async def generate_sections(
    brief: ProductBrief,
    copy: LandingCopy,
    *,
    product_image: bytes | None = None,
    advertiser_angle: str | None = None,
    client: YunwuClient | None = None,
    settings: Settings | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> list[GeneratedSection]:
    gen = ImageGenerator(client=client, settings=settings)
    return await gen.generate_all(
        brief, copy, product_image=product_image,
        advertiser_angle=advertiser_angle, progress=progress,
    )
