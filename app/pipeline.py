"""High-level pipeline orchestrator.

Coordinates analyzer → copy writer → image generator → stitcher and writes
artifacts under ``settings.output_dir / <job_id>/``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import Settings, settings as default_settings
from app.schemas import JobRecord, LandingCopy, ProductBrief
from app.services.analyzer import ProductAnalyzer
from app.services.copy_writer import CopyWriter
from app.services.image_gen import ImageGenerator
from app.services.stitcher import Stitcher
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


class Pipeline:
    """One-shot orchestrator. Each call processes a single product image."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self.client = YunwuClient(self.settings)
        self.analyzer = ProductAnalyzer(client=self.client, settings=self.settings)
        self.writer = CopyWriter(client=self.client, settings=self.settings)
        self.image_gen = ImageGenerator(client=self.client, settings=self.settings)
        self.stitcher = Stitcher(self.settings)

    async def run(
        self,
        *,
        job: JobRecord,
        image_bytes: bytes,
        mime: str,
        progress: callable | None = None,
    ) -> JobRecord:
        job_dir = self.settings.output_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)

        async def _progress(step: str) -> None:
            job.step = step
            log.info("[job=%s] %s", job.id, step)
            if progress is not None:
                await progress(step)

        job.status = "running"

        await _progress("analyzing product image")
        brief: ProductBrief = await self.analyzer.analyze(image_bytes, mime=mime)
        brief_path = job_dir / "brief.json"
        brief_path.write_text(brief.model_dump_json(indent=2), encoding="utf-8")
        job.brief_path = str(brief_path)

        await _progress("generating Arabic copy")
        copy: LandingCopy = await self.writer.generate(brief)
        copy_path = job_dir / "copy.json"
        copy_path.write_text(copy.model_dump_json(indent=2), encoding="utf-8")
        job.copy_path = str(copy_path)

        await _progress("generating 8 section images")
        sections = await self.image_gen.generate_all(brief, copy, product_image=image_bytes)
        section_paths: list[str] = []
        for sec in sections:
            fname = SECTION_FILENAMES.get(sec.key, f"section_{sec.index + 1}_{sec.key}.png")
            path = job_dir / fname
            path.write_bytes(sec.image_bytes)
            section_paths.append(str(path))
        job.sections = section_paths

        await _progress("stitching final long image")
        ordered = sorted(sections, key=lambda s: s.index)
        long_path = job_dir / "landing_long.png"
        self.stitcher.stitch([s.image_bytes for s in ordered], output_path=long_path)
        job.long_image = str(long_path)

        # Bundle prompts for debugging.
        prompts_path = job_dir / "prompts.json"
        prompts_path.write_text(
            json.dumps(
                {s.key: s.prompt for s in ordered},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        job.status = "done"
        job.step = "complete"
        await _progress("complete")
        return job
