"""FastAPI entry point.

Single-page upload form, background job runner, status / result page,
admin dashboard with settings management, jobs history.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import (
    create_session,
    destroy_session,
    require_auth,
    set_session_cookie,
    verify_session,
)
from app.config import settings
from app.pipeline import Pipeline
from app.schemas import JobRecord
from app.settings_store import (
    change_credentials,
    get_setting,
    load_settings,
    update_section,
    verify_credentials,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Landing Page Generator", version="0.3.0")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static files: app assets + per-job artifacts.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")

_JOBS: dict[str, JobRecord] = {}
_JOBS_LOCK = asyncio.Lock()

# Step progress mapping
STEP_PROGRESS = {
    "queued": 5,
    "analyzing product image": 15,
    "generating Arabic copy": 35,
    "generating 8 section images": 55,
    "stitching final long image": 85,
    "complete": 100,
}


# ─────────────────────────────────────────────────────────────── Public Routes


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    ui_settings = get_setting("ui") or {}
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "show_api_icons": ui_settings.get("show_api_icons", True),
            "api_icons": ui_settings.get("api_icons_config", {}),
        },
    )


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


@app.post("/generate-url")
async def generate_from_url(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Generate from an image URL instead of file upload."""
    body = await request.json()
    image_url = body.get("image_url", "").strip()
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url is required.")

    # Download the image
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to download image: {exc}")

    content_type = resp.headers.get("content-type", "image/jpeg")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="URL does not point to an image.")

    raw = resp.content
    if not raw:
        raise HTTPException(status_code=400, detail="Downloaded empty file.")

    job_id = uuid.uuid4().hex[:12]
    upload_path = settings.upload_dir / f"{job_id}_url_download"
    upload_path.write_bytes(raw)

    record = JobRecord(id=job_id, status="pending", step="queued")
    async with _JOBS_LOCK:
        _JOBS[job_id] = record

    background_tasks.add_task(_run_job, job_id, raw, content_type)

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
    # Pipeline uses live settings (reads from dashboard JSON)
    pipeline = Pipeline()
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


# ─────────────────────────────────────────────────────────── Jobs History


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request) -> HTMLResponse:
    """Show all jobs with their status and progress."""
    jobs_with_progress = []
    for job in reversed(list(_JOBS.values())):  # newest first
        progress = STEP_PROGRESS.get(job.step, 50)
        if job.status == "done":
            progress = 100
        elif job.status == "error":
            progress = STEP_PROGRESS.get(job.step, 50)
        jobs_with_progress.append({
            "id": job.id,
            "status": job.status,
            "step": job.step,
            "error": job.error,
            "progress": progress,
        })
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": jobs_with_progress},
    )


# ─────────────────────────────────────────────────────────────── Admin Routes


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request) -> HTMLResponse:
    session = verify_session(request)
    if session:
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if verify_credentials(username, password):
        token = create_session(username)
        response = RedirectResponse(url="/admin/dashboard", status_code=302)
        set_session_cookie(response, token)
        return response
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "اسم المستخدم أو كلمة المرور غير صحيحة"},
    )


@app.post("/admin/logout")
async def admin_logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=302)
    destroy_session(request, response)
    return response


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request) -> HTMLResponse:
    redirect = require_auth(request)
    if redirect:
        return redirect

    all_settings = load_settings()

    # Load default prompts from files for display
    prompts_dir = BASE_DIR / "prompts"
    default_copy = ""
    default_analyzer = ""
    try:
        default_copy = (prompts_dir / "copy_system_ar.txt").read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        from app.services.analyzer import ANALYZER_INSTRUCTIONS
        default_analyzer = ANALYZER_INSTRUCTIONS
    except ImportError:
        pass

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "settings": _make_dot_dict(all_settings),
            "default_prompts": _make_dot_dict({
                "copy_system_ar": default_copy,
                "analyzer_instructions": default_analyzer,
            }),
        },
    )


# ─────────────────────────────────────────────────────── Admin Settings API


@app.post("/admin/api/settings/api")
async def save_api_settings(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    update_section("api", data)
    return JSONResponse({"status": "ok"})


@app.post("/admin/api/settings/image")
async def save_image_settings(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    update_section("image", data)
    return JSONResponse({"status": "ok"})


@app.post("/admin/api/settings/prompts")
async def save_prompts_settings(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    update_section("prompts", data)
    return JSONResponse({"status": "ok"})


@app.post("/admin/api/settings/ui")
async def save_ui_settings(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    # Merge icon config properly
    current = get_setting("ui") or {}
    current_icons = current.get("api_icons_config", {})
    new_icons = data.get("api_icons_config", {})
    for key, val in new_icons.items():
        if key in current_icons:
            current_icons[key].update(val)
        else:
            current_icons[key] = val
    data["api_icons_config"] = current_icons
    update_section("ui", data)
    return JSONResponse({"status": "ok"})


@app.post("/admin/api/settings/credentials")
async def save_credentials(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    username = data.get("username")
    password = data.get("password")
    if not username:
        raise HTTPException(status_code=400, detail="اسم المستخدم مطلوب")
    change_credentials(new_username=username, new_password=password if password else None)
    return JSONResponse({"status": "ok"})


# ─────────────────────────────────────────────────────────────── Helpers


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


class _DotDict(dict):
    """Dict subclass that allows attribute access for Jinja2 templates."""
    def __getattr__(self, key):
        val = self.get(key)
        if isinstance(val, dict):
            return _DotDict(val)
        return val if val is not None else ""

    def __getitem__(self, key):
        val = super().__getitem__(key)
        if isinstance(val, dict):
            return _DotDict(val)
        return val


def _make_dot_dict(d: dict) -> _DotDict:
    """Recursively convert a dict to _DotDict for template access."""
    result = _DotDict()
    for key, val in d.items():
        if isinstance(val, dict):
            result[key] = _make_dot_dict(val)
        else:
            result[key] = val
    return result
