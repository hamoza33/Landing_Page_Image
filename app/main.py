"""FastAPI entry point.

Routes:
  GET  /              — upload form + history of past jobs
  POST /generate      — start a new job, returns json with redirects
  GET  /jobs          — JSON list of all jobs
  GET  /jobs/{id}     — JSON job state
  GET  /jobs/{id}/view — HTML result page (stacked sections)
  POST /jobs/{id}/sections/{key}/regenerate
                       — re-render one section (form: prompt = optional override)
  GET  /files/...     — static job artifacts (mounted on settings.output_dir)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.pipeline import Pipeline
from app.schemas import JobRecord, JobSection
from app.store import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("app")


_STORE = JobStore(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _STORE.load_from_disk()
    yield


app = FastAPI(title="Landing Page Generator", version="0.2.0", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static files: app assets + per-job artifacts.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")


# ----------------------------------------------------------------- routes


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/settings", name="settings_view", response_class=HTMLResponse)
async def settings_view(request: Request) -> HTMLResponse:
    """Read-only overview of the live settings.

    Variables are env-driven and require a process restart to change. Use
    this page to see exactly what the running container is configured with,
    plus the API endpoints in play. Full reference: ``docs/SETTINGS.md``.
    """

    groups = [
        ("Yunwu API", [
            ("YUNWU_BASE_URL", settings.yunwu_base_url),
            ("YUNWU_CHAT_MODEL", settings.chat_model),
            ("YUNWU_IMAGE_EDIT_MODEL", settings.image_edit_model),
            ("YUNWU_IMAGE_MODEL", settings.image_model),
            ("HTTP_TIMEOUT (s)", settings.http_timeout),
        ]),
        ("Web Research", [
            ("WEB_RESEARCH_ENABLED", settings.web_research_enabled),
            ("TAVILY_API_KEY set", bool(settings.tavily_api_key)),
        ]),
        ("Image Generation", [
            ("IMAGE_SIZE", settings.image_size),
            ("IMAGE_QUALITY", settings.image_quality),
            ("IMAGE_FORMAT", settings.image_format),
            ("IMAGE_CONCURRENCY", settings.image_concurrency),
        ]),
        ("Seamless Continuity", [
            ("SEAMLESS_FLOW", settings.seamless_flow),
            ("SEAM_STRIP_HEIGHT (px)", settings.seam_strip_height),
            ("SEAM_BLEND_HEIGHT (px)", settings.seam_blend_height),
        ]),
        ("Text Overlay", [
            ("OVERLAY_TEXT_ENABLED", settings.overlay_text_enabled),
            ("OVERLAY_HEADLINE_SIZE", settings.overlay_headline_size),
            ("OVERLAY_SUBHEAD_SIZE", settings.overlay_subhead_size),
            ("OVERLAY_BODY_SIZE", settings.overlay_body_size),
            ("OVERLAY_TEXT_COLOR", settings.overlay_text_color),
            ("OVERLAY_SHADOW_COLOR", settings.overlay_shadow_color),
            ("OVERLAY_MAX_WIDTH_RATIO", settings.overlay_max_width_ratio),
            ("OVERLAY_PADDING_RATIO", settings.overlay_padding_ratio),
            ("FONT_ARABIC_REGULAR", str(settings.font_arabic_regular)),
            ("FONT_ARABIC_BOLD", str(settings.font_arabic_bold)),
        ]),
        ("Paths", [
            ("OUTPUT_DIR", str(settings.output_dir)),
            ("UPLOAD_DIR", str(settings.upload_dir)),
            ("ASSETS_DIR", str(settings.assets_dir)),
            ("PUBLIC_BASE_URL", settings.public_base_url),
        ]),
    ]
    apis = [
        ("Yunwu chat / vision",
         f"POST {settings.yunwu_base_url}/v1/chat/completions"),
        ("Yunwu image edit (multi-reference)",
         f"POST {settings.yunwu_base_url}/v1/images/generations  (model={settings.image_edit_model})"),
        ("Tavily web search",
         "POST https://api.tavily.com/search"),
    ]
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"groups": groups, "apis": apis},
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    jobs = [_job_payload(j) for j in _STORE.list()]
    return templates.TemplateResponse(request, "index.html", {"jobs": jobs})


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
    record = await _STORE.create(job_id)

    # Save the original upload inside the job dir so it's recoverable for
    # regeneration after restarts and visible to the user from the history.
    job_dir = settings.output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (image.filename or "upload").replace("/", "_")
    upload_path = job_dir / f"upload_{safe_name}"
    upload_path.write_bytes(raw)
    record.upload_path = str(upload_path)
    await _STORE.save(record)

    background_tasks.add_task(_run_job, job_id, raw, image.content_type)

    return JSONResponse(
        {
            "job_id": job_id,
            "status_url": str(request.url_for("job_status", job_id=job_id)),
            "view_url": str(request.url_for("job_view", job_id=job_id)),
        }
    )


async def _run_job(job_id: str, image_bytes: bytes, mime: str) -> None:
    record = _STORE.get(job_id)
    if record is None:
        return
    pipeline = Pipeline(settings)
    try:
        await pipeline.run(
            job=record,
            image_bytes=image_bytes,
            mime=mime,
            persist=_STORE.save,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Job %s failed", job_id)
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"
        await _STORE.save(record)


@app.get("/jobs", name="jobs_list")
async def jobs_list() -> JSONResponse:
    return JSONResponse({"jobs": [_job_payload(j) for j in _STORE.list()]})


@app.get("/jobs/{job_id}", name="job_status")
async def job_status(job_id: str) -> JSONResponse:
    record = _STORE.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return JSONResponse(_job_payload(record))


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse, name="job_view")
async def job_view(request: Request, job_id: str) -> HTMLResponse:
    record = _STORE.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    payload = _job_payload(record)
    return templates.TemplateResponse(
        request,
        "job.html",
        {"job": record, "payload": payload},
    )


_REGEN_LOCKS: dict[str, asyncio.Lock] = {}


def _section_lock(job_id: str, key: str) -> asyncio.Lock:
    k = f"{job_id}:{key}"
    if k not in _REGEN_LOCKS:
        _REGEN_LOCKS[k] = asyncio.Lock()
    return _REGEN_LOCKS[k]


@app.post("/jobs/{job_id}/sections/{section_key}/regenerate", name="section_regenerate")
async def regenerate_section(
    request: Request,
    job_id: str,
    section_key: str,
    background_tasks: BackgroundTasks,
    prompt: str | None = Form(default=None),
) -> JSONResponse:
    record = _STORE.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    sec = next((s for s in record.sections if s.key == section_key), None)
    if sec is None:
        raise HTTPException(status_code=404, detail=f"Unknown section: {section_key}")

    custom_prompt = prompt.strip() if prompt and prompt.strip() else None

    background_tasks.add_task(_run_regenerate, job_id, section_key, custom_prompt)

    return JSONResponse(
        {
            "ok": True,
            "job_id": job_id,
            "section": section_key,
            "status_url": str(request.url_for("job_status", job_id=job_id)),
        }
    )


async def _run_regenerate(job_id: str, section_key: str, custom_prompt: str | None) -> None:
    record = _STORE.get(job_id)
    if record is None:
        return
    lock = _section_lock(job_id, section_key)
    async with lock:
        pipeline = Pipeline(settings)
        try:
            await pipeline.regenerate_section(
                job=record,
                section_key=section_key,
                custom_prompt=custom_prompt,
                persist=_STORE.save,
            )
        except Exception as exc:  # noqa: BLE001 — already recorded on the section
            log.warning("Regenerate %s/%s failed: %s", job_id, section_key, exc)


# Convenience shortcut for sharing the result page on the bare subdomain
# without having to copy the long /jobs/<id>/view path.
@app.get("/j/{job_id}", include_in_schema=False)
async def job_short(job_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/jobs/{job_id}/view")


# --------------------------------------------------------------- helpers


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


def _section_payload(sec: JobSection) -> dict:
    return {
        "key": sec.key,
        "index": sec.index,
        "status": sec.status,
        "prompt": sec.prompt,
        "image_url": _file_url(sec.image_path),
        "error": sec.error,
    }


def _job_payload(record: JobRecord) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "status": record.status,
        "step": record.step,
        "error": record.error,
        "product_name": record.product_name,
        "upload_url": _file_url(record.upload_path),
        "copy_url": _file_url(record.copy_path),
        "brief_url": _file_url(record.brief_path),
        "view_url": f"/jobs/{record.id}/view",
        "sections": [_section_payload(s) for s in record.sections],
    }
