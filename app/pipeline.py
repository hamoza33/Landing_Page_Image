"""High-level pipeline orchestrator.

Coordinates analyzer → copy writer → image generator and writes artifacts
under ``settings.output_dir / <job_id>/``. Each section is rendered
independently and its state is persisted, so a single failed section does
not kill the whole job and any section can be regenerated later.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.config import Settings, settings as default_settings
from app.schemas import JobRecord, JobSection, LandingCopy, ProductBrief, SECTION_KEYS
from app.services.analyzer import ProductAnalyzer
from app.services.copy_writer import CopyWriter
from app.services.image_gen import ImageGenerator
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
    """Orchestrator. Renders one product image into 8 stacked panels."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.client = YunwuClient(self.settings)
        self.analyzer = ProductAnalyzer(client=self.client, settings=self.settings)
        self.writer = CopyWriter(client=self.client, settings=self.settings)
        self.image_gen = ImageGenerator(client=self.client, settings=self.settings)

    # ------------------------------------------------------------------ run

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
        await _step("analyzing product image")
        brief: ProductBrief = await self.analyzer.analyze(image_bytes, mime=mime)
        brief_path = job_dir / "brief.json"
        brief_path.write_text(brief.model_dump_json(indent=2), encoding="utf-8")
        job.brief_path = str(brief_path)
        job.product_name = brief.name

        await _step("generating Arabic copy")
        copy: LandingCopy = await self.writer.generate(brief)
        copy_path = job_dir / "copy.json"
        copy_path.write_text(copy.model_dump_json(indent=2), encoding="utf-8")
        job.copy_path = str(copy_path)

        # Initialize per-section state up front, persist so the UI sees the grid.
        job.sections = [
            JobSection(key=k, index=i, status="pending")
            for i, k in enumerate(SECTION_KEYS)
        ]
        if persist:
            await persist(job)

        # Render each section in parallel (bounded by IMAGE_CONCURRENCY).
        # Failures are recorded on the section but don't break the others —
        # the user can hit "regenerate" later.
        await _step("generating 8 section images")
        # Use a shared seed per job for visual continuity across regenerations.
        seed = self.image_gen.fresh_seed()
        sem = asyncio.Semaphore(max(1, self.settings.image_concurrency))
        prompts: dict[str, str] = {}
        any_error = False

        async def _render_one(sec: JobSection) -> None:
            nonlocal any_error
            async with sem:
                try:
                    sec.status = "running"
                    if persist:
                        await persist(job)
                    prompt, png = await self.image_gen.render_section(
                        key=sec.key,
                        index=sec.index,
                        brief=brief,
                        copy=copy,
                        seed=seed,
                        product_image=image_bytes,
                    )
                    fname = SECTION_FILENAMES.get(sec.key, f"section_{sec.index + 1}_{sec.key}.png")
                    path = job_dir / fname
                    path.write_bytes(png)
                    sec.prompt = prompt
                    sec.image_path = str(path)
                    sec.status = "done"
                    sec.error = None
                    prompts[sec.key] = prompt
                except Exception as exc:  # noqa: BLE001
                    log.exception("[job=%s] section %s failed", job.id, sec.key)
                    sec.status = "error"
                    sec.error = f"{type(exc).__name__}: {exc}"
                    any_error = True
                finally:
                    if persist:
                        await persist(job)

        await asyncio.gather(*[_render_one(s) for s in job.sections])

        # Stash prompts even if some sections failed (handy for debugging).
        if prompts:
            (job_dir / "prompts.json").write_text(
                json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        job.status = "error" if any_error else "done"
        job.step = "complete with errors" if any_error else "complete"
        if any_error and not job.error:
            failed = [s.key for s in job.sections if s.status == "error"]
            job.error = f"Some sections failed: {', '.join(failed)}"
        if persist:
            await persist(job)
        return job

    # --------------------------------------------------------- regenerate

    async def regenerate_section(
        self,
        *,
        job: JobRecord,
        section_key: str,
        custom_prompt: str | None = None,
        persist: PersistFn | None = None,
    ) -> JobSection:
        """Re-render a single section, optionally overriding its prompt."""

        if not job.brief_path or not job.copy_path:
            raise RuntimeError("Job is missing brief/copy; cannot regenerate.")
        sec = next((s for s in job.sections if s.key == section_key), None)
        if sec is None:
            raise RuntimeError(f"Unknown section key: {section_key}")

        brief = ProductBrief.model_validate_json(Path(job.brief_path).read_text(encoding="utf-8"))
        copy = LandingCopy.model_validate_json(Path(job.copy_path).read_text(encoding="utf-8"))

        # If the user uploaded an image, hand it to image-edit-anchored sections.
        product_image: bytes | None = None
        if job.upload_path and Path(job.upload_path).exists():
            product_image = Path(job.upload_path).read_bytes()

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
                seed=self.image_gen.fresh_seed(),
                product_image=product_image,
                prompt_override=custom_prompt,
            )
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
            # Recompute job-level status: done if every section is done.
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
