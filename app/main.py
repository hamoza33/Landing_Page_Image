"""FastAPI entry point.

Single-page upload form, background job runner, status / result page.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.pipeline import Pipeline
from app.schemas import JobRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Landing Page Generator", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static files: app assets + per-job artifacts.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")

_JOBS: dict[str, JobRecord] = {}
_JOBS_LOCK = asyncio.Lock()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/generate")
async def generate(
    request: Request,
    image: UploadFile,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image.")

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    job_id = uuid.uuid4().hex[:12]
    upload_path = settings.upload_dir / f"{job_id}_{image.filename or 'upload'}"
    upload_path.write_bytes(raw)

    record = JobRecord(id=job_id, status="pending", step="queued")
    async with _JOBS_LOCK:
        _JOBS[job_id] = record

    background_tasks.add_task(_run_job, job_id, raw, image.content_type)

    return JSONResponse(
        {
            "job_id": job_id,
            "status_url": str(request.url_for("job_status", job_id=job_id)),
            "view_url": str(request.url_for("job_view", job_id=job_id)),
        }
    )


async def _run_job(job_id: str, image_bytes: bytes, mime: str) -> None:
    record = _JOBS.get(job_id)
    if record is None:
        return
    pipeline = Pipeline(settings)
    try:
        await pipeline.run(job=record, image_bytes=image_bytes, mime=mime)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the user
        log.exception("Job %s failed", job_id)
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"


@app.get("/jobs/{job_id}", name="job_status")
async def job_status(job_id: str) -> JSONResponse:
    record = _JOBS.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    payload = record.model_dump()
    payload["long_image_url"] = _file_url(record.long_image)
    payload["section_urls"] = [_file_url(p) for p in record.sections]
    payload["copy_url"] = _file_url(record.copy_path)
    payload["brief_url"] = _file_url(record.brief_path)
    return JSONResponse(payload)


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse, name="job_view")
async def job_view(request: Request, job_id: str) -> HTMLResponse:
    record = _JOBS.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return templates.TemplateResponse(
        request,
        "job.html",
        {
            "job": record,
            "long_image_url": _file_url(record.long_image),
            "section_urls": [_file_url(p) for p in record.sections],
            "copy_url": _file_url(record.copy_path),
            "brief_url": _file_url(record.brief_path),
        },
    )


def _file_url(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path).resolve()
    out = settings.output_dir.resolve()
    try:
        rel = p.relative_to(out)
    except ValueError:
        return None
    return f"/files/{rel.as_posix()}"
