"""High-level pipeline orchestrator.

Flow:
    1. **Research** the product photo → ``ProductBrief`` (vision + optional
       Tavily web research, see ``app.services.researcher``).
    2. **Write** Arabic GCC copy → ``LandingCopy``.
    3. **Generate images sequentially** with seamless visual continuity:
          - hero uses the product photo as the only reference.
          - each subsequent section uses ``[product_photo, prev_bottom_strip]``
            so its top edge continues from the previous section.
       PIL also runs a final alpha-blend pass to erase any residual seam.
    4. **Overlay Arabic text** (headlines, CTAs, lists…) onto each rendered
       PNG using ``TextOverlay`` — the model is told NOT to draw text, we
       do it ourselves for type quality.
    5. Persist each section's state to ``output/<job_id>/`` as it lands so
       the UI sees progress in real time.

A failing section does NOT abort the job. Any section can be regenerated
later through ``regenerate_section``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.config import Settings, settings as default_settings
from app.schemas import (
    JobRecord,
    JobSection,
    LandingCopy,
    ProductBrief,
    SECTION_KEYS,
)
from app.services.copy_writer import CopyWriter
from app.services.image_gen import (
    ImageGenerator,
    blend_top_into_previous,
    extract_bottom_strip,
)
from app.services.researcher import ProductResearcher
from app.services.text_overlay import TextOverlay
from app.services.yunwu_client import YunwuClient

log = logging.getLogger(__name__)


SECTION_FILENAMES = {
    "hero": "section_1_hero.png",
    "features": "section_2_features.png",
    "before_after": "section_3_before_after.png",
    "testimonials": "section_4_testimonials.png",
    "faq": "section_5_faq.png",
    "lifestyle": "section_6_lifestyle.png",
    "education": "section_7_education.png",
    "closing": "section_8_closing.png",
}


PersistFn = Callable[[JobRecord], Awaitable[None]]


class Pipeline:
    """Orchestrator. Turns a single product image into 8 stacked panels."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.client = YunwuClient(self.settings)
        self.researcher = ProductResearcher(client=self.client, settings=self.settings)
        self.writer = CopyWriter(client=self.client, settings=self.settings)
        self.image_gen = ImageGenerator(client=self.client, settings=self.settings)
        self.overlay = TextOverlay(settings=self.settings)

    # -------------------------------------------------------------------- run

    async def run(
        self,
        *,
        job: JobRecord,
        image_bytes: bytes,
        mime: str,
        persist: PersistFn | None = None,
    ) -> JobRecord:
        job_dir = self.settings.output_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)

        async def _step(step: str) -> None:
            job.step = step
            log.info("[job=%s] %s", job.id, step)
            if persist:
                await persist(job)

        job.status = "running"

        # 1. Research --------------------------------------------------------
        await _step("identifying the product (vision + web)")
        brief: ProductBrief = await self.researcher.research(image_bytes, mime=mime)
        (job_dir / "brief.json").write_text(brief.model_dump_json(indent=2), encoding="utf-8")
        job.brief_path = str(job_dir / "brief.json")
        job.product_name = brief.name

        # 2. Copy ------------------------------------------------------------
        await _step("writing Arabic copy")
        copy: LandingCopy = await self.writer.generate(brief)
        (job_dir / "copy.json").write_text(copy.model_dump_json(indent=2), encoding="utf-8")
        job.copy_path = str(job_dir / "copy.json")

        # 3. Images ----------------------------------------------------------
        job.sections = [
            JobSection(key=k, index=i, status="pending")
            for i, k in enumerate(SECTION_KEYS)
        ]
        if persist:
            await persist(job)

        await _step("generating 8 section images")
        await self._render_sections_sequential(
            job=job,
            brief=brief,
            copy=copy,
            product_image=image_bytes,
            persist=persist,
        )

        # Finalize
        any_error = any(s.status == "error" for s in job.sections)
        job.status = "error" if any_error else "done"
        job.step = "complete with errors" if any_error else "complete"
        if any_error:
            failed = [s.key for s in job.sections if s.status == "error"]
            job.error = f"Some sections failed: {', '.join(failed)}"
        else:
            job.error = None
        if persist:
            await persist(job)
        return job

    # --------------------------------------------------- sequential renderer

    async def _render_sections_sequential(
        self,
        *,
        job: JobRecord,
        brief: ProductBrief,
        copy: LandingCopy,
        product_image: bytes,
        persist: PersistFn | None,
    ) -> None:
        """Render sections one-by-one carrying the seam strip forward."""

        job_dir = self.settings.output_dir / job.id
        previous_bottom_strip: bytes | None = None
        previous_png: bytes | None = None
        prompts: dict[str, str] = {}

        for sec in job.sections:
            sec.status = "running"
            if persist:
                await persist(job)
            try:
                prompt, png = await self.image_gen.render_section(
                    key=sec.key,
                    index=sec.index,
                    brief=brief,
                    copy=copy,
                    product_image=product_image,
                    previous_bottom_strip=previous_bottom_strip,
                )

                # Belt-and-suspenders: blend the top of this PNG into the
                # bottom of the previous one so any leftover seam vanishes.
                if previous_png is not None and self.settings.seam_blend_height > 0:
                    png = blend_top_into_previous(
                        current_png=png,
                        previous_png=previous_png,
                        blend_height=self.settings.seam_blend_height,
                    )

                # Text overlay (Arabic headlines/CTAs in PIL).
                png_with_text = self.overlay.apply(
                    section_key=sec.key,
                    png_bytes=png,
                    copy=copy,
                )

                fname = SECTION_FILENAMES.get(sec.key, f"section_{sec.index + 1}_{sec.key}.png")
                path = job_dir / fname
                path.write_bytes(png_with_text)

                sec.prompt = prompt
                sec.image_path = str(path)
                sec.status = "done"
                sec.error = None
                prompts[sec.key] = prompt

                # Carry the seam strip from the *raw* (text-free) render so the
                # next section's seam reference is clean colors only.
                previous_bottom_strip = extract_bottom_strip(
                    png, strip_height=self.settings.seam_strip_height
                )
                previous_png = png
            except Exception as exc:  # noqa: BLE001
                log.exception("[job=%s] section %s failed", job.id, sec.key)
                sec.status = "error"
                sec.error = f"{type(exc).__name__}: {exc}"
                # On error, the chain is broken — next section starts fresh
                # (no seam ref). Better than passing a bogus strip.
                previous_bottom_strip = None
                previous_png = None
            finally:
                if persist:
                    await persist(job)

        if prompts:
            (job_dir / "prompts.json").write_text(
                json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # ------------------------------------------------------ regenerate one

    async def regenerate_section(
        self,
        *,
        job: JobRecord,
        section_key: str,
        custom_prompt: str | None = None,
        persist: PersistFn | None = None,
    ) -> JobSection:
        """Re-render a single section, optionally with a user-edited prompt.

        For section N>=1 the seam reference is read from the previous
        section's saved PNG (its bottom strip), so visual continuity with
        the upstream section is preserved.
        """

        if not job.brief_path or not job.copy_path:
            raise RuntimeError("Job is missing brief/copy; cannot regenerate.")
        sec = next((s for s in job.sections if s.key == section_key), None)
        if sec is None:
            raise RuntimeError(f"Unknown section key: {section_key}")

        brief = ProductBrief.model_validate_json(
            Path(job.brief_path).read_text(encoding="utf-8")
        )
        copy = LandingCopy.model_validate_json(
            Path(job.copy_path).read_text(encoding="utf-8")
        )

        if not job.upload_path or not Path(job.upload_path).exists():
            raise RuntimeError("Original product image missing; cannot regenerate.")
        product_image = Path(job.upload_path).read_bytes()

        # Seam reference from previous section.
        previous_bottom_strip: bytes | None = None
        previous_png: bytes | None = None
        if sec.index > 0:
            prev = next(s for s in job.sections if s.index == sec.index - 1)
            if prev.image_path and Path(prev.image_path).exists():
                previous_png = Path(prev.image_path).read_bytes()
                previous_bottom_strip = extract_bottom_strip(
                    previous_png, strip_height=self.settings.seam_strip_height
                )

        sec.status = "running"
        sec.error = None
        if persist:
            await persist(job)

        try:
            prompt, png = await self.image_gen.render_section(
                key=sec.key,
                index=sec.index,
                brief=brief,
                copy=copy,
                product_image=product_image,
                previous_bottom_strip=previous_bottom_strip,
                prompt_override=custom_prompt,
            )
            if previous_png is not None and self.settings.seam_blend_height > 0:
                png = blend_top_into_previous(
                    current_png=png,
                    previous_png=previous_png,
                    blend_height=self.settings.seam_blend_height,
                )
            png = self.overlay.apply(section_key=sec.key, png_bytes=png, copy=copy)

            job_dir = self.settings.output_dir / job.id
            fname = SECTION_FILENAMES.get(sec.key, f"section_{sec.index + 1}_{sec.key}.png")
            path = job_dir / fname
            path.write_bytes(png)
            sec.prompt = prompt
            sec.image_path = str(path)
            sec.status = "done"
            sec.error = None
        except Exception as exc:  # noqa: BLE001
            log.exception("[job=%s] regenerate %s failed", job.id, section_key)
            sec.status = "error"
            sec.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # Recompute job-level status.
            if all(s.status == "done" for s in job.sections):
                job.status = "done"
                job.step = "complete"
                job.error = None
            elif any(s.status == "error" for s in job.sections):
                job.status = "error"
                failed = [s.key for s in job.sections if s.status == "error"]
                job.error = f"Some sections failed: {', '.join(failed)}"
                job.step = "complete with errors"
            if persist:
                await persist(job)
        return sec
