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
from app.schemas import JobRecord, SECTION_KEYS as SECTION_KEYS_IMPORT
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

# Prefix-based progress for sequential image generation steps
STEP_PROGRESS_PREFIXES = [
    ("generating section 1 of 8", 40),
    ("generating section 2 of 8", 45),
    ("generating section 3 of 8", 50),
    ("generating section 4 of 8", 55),
    ("generating section 5 of 8", 60),
    ("generating section 6 of 8", 65),
    ("generating section 7 of 8", 70),
    ("generating section 8 of 8", 75),
]


def _get_step_progress(step: str) -> int:
    """Return progress percentage for a given step string.

    Supports both exact matches and prefix-based matches for sequential
    image generation steps like 'generating section N of 8: key'.
    """
    exact = STEP_PROGRESS.get(step)
    if exact is not None:
        return exact
    for prefix, progress in STEP_PROGRESS_PREFIXES:
        if step.startswith(prefix):
            return progress
    return 50


# ─────────────────────────────────────────────────────────────── Public Routes


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the SPA dashboard page.

    Only non-sensitive UI settings are passed to the template context.
    Sensitive data (API keys, credentials) is fetched client-side via
    the authenticated /admin/api/settings endpoint after login.
    """
    all_settings = load_settings()
    ui_settings = all_settings.get("ui", {})

    return templates.TemplateResponse(
        request,
        "spa.html",
        {
            "settings": _make_dot_dict({"ui": ui_settings}),
        },
    )


@app.post("/generate")
async def generate(
    request: Request,
    image: UploadFile,
    background_tasks: BackgroundTasks,
    advertiser_angle: str | None = Form(None),
) -> JSONResponse:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image.")

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    job_id = uuid.uuid4().hex[:12]
    upload_path = settings.upload_dir / f"{job_id}_{image.filename or 'upload'}"
    upload_path.write_bytes(raw)

    record = JobRecord(id=job_id, status="pending", step="queued", advertiser_angle=advertiser_angle)
    async with _JOBS_LOCK:
        _JOBS[job_id] = record

    background_tasks.add_task(_run_job, job_id, raw, image.content_type, advertiser_angle)

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

    advertiser_angle = body.get("advertiser_angle", "").strip() or None

    # Download the image
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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

    record = JobRecord(id=job_id, status="pending", step="queued", advertiser_angle=advertiser_angle)
    async with _JOBS_LOCK:
        _JOBS[job_id] = record

    background_tasks.add_task(_run_job, job_id, raw, content_type, advertiser_angle)

    return JSONResponse(
        {
            "job_id": job_id,
            "status_url": str(request.url_for("job_status", job_id=job_id)),
            "view_url": str(request.url_for("job_view", job_id=job_id)),
        }
    )


async def _run_job(job_id: str, image_bytes: bytes, mime: str, advertiser_angle: str | None = None) -> None:
    record = _JOBS.get(job_id)
    if record is None:
        return
    # Pipeline uses live settings (reads from dashboard JSON)
    pipeline = Pipeline()
    try:
        await pipeline.run(job=record, image_bytes=image_bytes, mime=mime, advertiser_angle=advertiser_angle)
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
    payload["prompts"] = record.prompts
    return JSONResponse(payload)


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse, name="job_view")
async def job_view(request: Request, job_id: str) -> HTMLResponse:
    record = _JOBS.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    section_keys = list(SECTION_KEYS_IMPORT)
    return templates.TemplateResponse(
        request,
        "job.html",
        {
            "job": record,
            "long_image_url": _file_url(record.long_image),
            "section_urls": [_file_url(p) for p in record.sections],
            "copy_url": _file_url(record.copy_path),
            "brief_url": _file_url(record.brief_path),
            "section_keys": section_keys,
            "prompts": record.prompts,
        },
    )


@app.post("/jobs/{job_id}/regenerate/{section_key}")
async def regenerate_section(request: Request, job_id: str, section_key: str) -> JSONResponse:
    """Regenerate a single section image, optionally with a custom prompt."""
    record = _JOBS.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    if record.status != "done":
        raise HTTPException(status_code=400, detail="Job must be completed before regenerating sections")
    if section_key not in SECTION_KEYS_IMPORT:
        raise HTTPException(status_code=400, detail=f"Invalid section key: {section_key}")

    body = await request.json()
    custom_prompt = body.get("prompt", "").strip() or None

    # Load the product image and brief/copy from job directory
    job_dir = settings.output_dir / job_id
    product_path = job_dir / "product_upload.bin"
    brief_path = job_dir / "brief.json"
    copy_path = job_dir / "copy.json"

    if not brief_path.exists() or not copy_path.exists():
        raise HTTPException(status_code=400, detail="Job artifacts not found for regeneration")

    import json as json_mod
    from app.schemas import ProductBrief, LandingCopy
    from app.services.image_gen import ImageGenerator, PRODUCT_REF_SECTIONS
    from app.pipeline import SECTION_FILENAMES
    from app.config import Settings

    brief = ProductBrief.model_validate_json(brief_path.read_text(encoding="utf-8"))
    copy = LandingCopy.model_validate_json(copy_path.read_text(encoding="utf-8"))

    product_image: bytes | None = None
    if product_path.exists():
        product_image = product_path.read_bytes()

    # Get previous section image for continuity
    section_idx = list(SECTION_KEYS_IMPORT).index(section_key)
    prev_section_image: bytes | None = None
    if section_idx > 0 and len(record.sections) > section_idx - 1:
        prev_path = Path(record.sections[section_idx - 1])
        if prev_path.exists():
            prev_section_image = prev_path.read_bytes()

    live_settings = Settings.load_live()
    from app.services.yunwu_client import YunwuClient
    client = YunwuClient(live_settings)
    gen = ImageGenerator(client=client, settings=live_settings)

    result = await gen.regenerate_section(
        brief, copy,
        section_key=section_key,
        custom_prompt=custom_prompt,
        product_image=product_image,
        prev_section_image=prev_section_image,
        advertiser_angle=record.advertiser_angle,
    )

    # Save the new section image
    fname = SECTION_FILENAMES.get(section_key, f"section_{section_idx + 1}_{section_key}.png")
    path = job_dir / fname
    path.write_bytes(result.image_bytes)

    # Update the job record
    if section_idx < len(record.sections):
        record.sections[section_idx] = str(path)

    # Update stored prompt
    record.prompts[section_key] = result.prompt

    # Update prompts.json
    prompts_path = job_dir / "prompts.json"
    prompts_path.write_text(
        json_mod.dumps(record.prompts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return JSONResponse({
        "success": True,
        "section_key": section_key,
        "section_url": _file_url(str(path)),
        "prompt": result.prompt,
    })


# ─────────────────────────────────────────────────────────── Jobs History


def _build_jobs_list() -> list[dict]:
    """Build the jobs list with progress for both HTML and JSON endpoints."""
    jobs_with_progress = []
    for job in reversed(list(_JOBS.values())):  # newest first
        progress = _get_step_progress(job.step)
        if job.status == "done":
            progress = 100
        elif job.status == "error":
            progress = _get_step_progress(job.step)
        jobs_with_progress.append({
            "id": job.id,
            "status": job.status,
            "step": job.step,
            "error": job.error,
            "progress": progress,
        })
    return jobs_with_progress


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request) -> HTMLResponse:
    """Show all jobs with their status and progress."""
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": _build_jobs_list()},
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
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept

    if verify_credentials(username, password):
        token = create_session(username)
        if wants_json:
            response = JSONResponse({"success": True, "error": None})
        else:
            response = RedirectResponse(url="/admin/dashboard", status_code=302)
        set_session_cookie(response, token)
        return response

    if wants_json:
        return JSONResponse(
            {"success": False, "error": "اسم المستخدم أو كلمة المرور غير صحيحة"}
        )
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


# ─────────────────────────────────────────────────────── Admin JSON API


@app.get("/admin/api/status")
async def admin_api_status(request: Request) -> JSONResponse:
    """Return authentication status as JSON."""
    session = verify_session(request)
    if session:
        return JSONResponse({"authenticated": True, "username": session["username"]})
    return JSONResponse({"authenticated": False, "username": None})


@app.get("/admin/api/jobs")
async def admin_api_jobs(request: Request) -> JSONResponse:
    """Return all jobs as JSON for the SPA."""
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return JSONResponse(_build_jobs_list())


# ─────────────────────────────────────────────────────── Admin Settings API


@app.get("/admin/api/settings")
async def admin_api_get_settings(request: Request) -> JSONResponse:
    """Return full settings JSON (including secrets) to authenticated users only."""
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")

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

    return JSONResponse({
        "settings": all_settings,
        "default_prompts": {
            "copy_system_ar": default_copy,
            "analyzer_instructions": default_analyzer,
        },
    })


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


@app.post("/admin/api/settings/image_apis")
async def save_image_apis_settings(request: Request) -> JSONResponse:
    redirect = require_auth(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Unauthorized")
    data = await request.json()
    update_section("image_apis", data)
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
