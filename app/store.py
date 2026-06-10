"""Tiny on-disk job store.

Each job's ``output_dir/<id>/job.json`` is the source of truth so jobs survive
container restarts. We keep an in-memory dict keyed by job id for fast access
and snapshot-to-disk on every state change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from app.config import Settings, settings as default_settings
from app.schemas import JobRecord

log = logging.getLogger(__name__)


class JobStore:
    """In-memory + on-disk store for ``JobRecord``s."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or default_settings
        self._records: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- IO

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.output_dir / job_id

    def _job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def load_from_disk(self) -> None:
        """Scan output_dir/* and rehydrate any job.json found."""

        out = self.settings.output_dir
        if not out.exists():
            return
        loaded = 0
        for child in sorted(out.iterdir()):
            if not child.is_dir():
                continue
            jf = child / "job.json"
            if not jf.exists():
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                rec = JobRecord.model_validate(data)
                self._records[rec.id] = rec
                loaded += 1
            except Exception as exc:  # noqa: BLE001 — skip malformed
                log.warning("Skipping unreadable %s: %s", jf, exc)
        log.info("Loaded %d job(s) from disk.", loaded)

    async def persist(self, record: JobRecord) -> None:
        path = self._job_file(record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically — no half-baked json.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    # ---------------------------------------------------------------- API

    async def create(self, job_id: str) -> JobRecord:
        async with self._lock:
            record = JobRecord(id=job_id, status="pending", step="queued", created_at=time.time())
            self._records[job_id] = record
            await self.persist(record)
            return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._records.get(job_id)

    def list(self) -> list[JobRecord]:
        """Newest first."""

        return sorted(self._records.values(), key=lambda r: r.created_at or 0, reverse=True)

    async def save(self, record: JobRecord) -> None:
        async with self._lock:
            self._records[record.id] = record
            await self.persist(record)
