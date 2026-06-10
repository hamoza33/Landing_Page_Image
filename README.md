# Landing Page Image Generator

Turn one product photo into an Arabic-language landing page (one tall PNG) targeted at GCC audiences.

## Pipeline

```
upload  →  Yunwu vision (gpt-5.4)             — extracts product brief
        →  Yunwu Responses (gpt-5.4)          — writes 8-section Arabic copy
        →  Yunwu image gen (gpt-image-2)      — 8 × 1024×3072 portrait scenes
                                                (hero + lifestyle anchored on
                                                 the user's photo via gpt-image-2-all)
        →  Pillow stitcher                    — 1024 × 24576 long PNG with seam blends
```

The 8 sections are: `hero`, `features`, `before_after`, `testimonials`, `faq`, `lifestyle`, `education`, `closing`.

## Quick start (local)

```bash
cp .env.example .env
# edit .env and set YUNWU_API_KEY
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000/>, upload a product photo, and watch the job page until status is `done`.

## Configuration

All settings come from environment variables (see `.env.example`):

| Var | Default | Notes |
| --- | --- | --- |
| `YUNWU_API_KEY` | _(required)_ | Yunwu API key. Never commit. |
| `YUNWU_BASE_URL` | `https://yunwu.ai` | Root only — paths are appended internally. |
| `YUNWU_CHAT_MODEL` | `gpt-5.4` | Vision + copy generation. |
| `YUNWU_IMAGE_MODEL` | `gpt-image-2` | Section image generation. |
| `YUNWU_IMAGE_EDIT_MODEL` | `gpt-image-2-all` | Multi-image / reference-anchored route. |
| `ANALYZER_BACKEND` | `yunwu` | Set to `google_vision` to use Google Vision (needs `GOOGLE_VISION_API_KEY`). |
| `IMAGE_WIDTH` | `1024` | Final width of every section. |
| `SECTION_HEIGHT` | `3072` | Final height of every section (1:3 portrait). |
| `IMAGE_CONCURRENCY` | `3` | Parallel image-API calls. |
| `OUTPUT_DIR` | `./output` | Per-job artifacts land here. |
| `UPLOAD_DIR` | `./uploads` | Original uploads. |

## API

* `GET /` — upload form (Arabic RTL).
* `POST /generate` — multipart with field `image`. Returns `{job_id, status_url, view_url}`.
* `GET /jobs/{job_id}` — JSON status with URLs to the long PNG, the 8 sections, and `copy.json`.
* `GET /jobs/{job_id}/view` — HTML result page.
* `GET /healthz` — liveness check.

Per job, artifacts are written under `output/<job_id>/`:

```
brief.json        # ProductBrief from the analyzer
copy.json         # 8-section Arabic copy
prompts.json      # Image prompts used (for debugging)
section_1_hero.png … section_8_closing.png
landing_long.png  # the 1024×24576 deliverable
```

## Docker

```bash
docker compose up -d --build
```

The compose file binds the app to `127.0.0.1:8000` by default — pair it with the nginx config in `nginx/landing.conf` for public access.

## Deployment to the VPS (35.255.81.115)

1. **First-time bootstrap** on the VPS (`aichaguimaoune@35.255.81.115`):
   ```bash
   sudo REPO_URL=https://github.com/<owner>/<repo>.git \
        DOMAIN=landing.shopinzo.bond \
        ADMIN_EMAIL=you@example.com \
        bash scripts/vps_bootstrap.sh
   ```
   This installs Docker + nginx + certbot, clones the repo to `/opt/landing-generator`, drops a 0600 `.env`, and brings the stack up.
2. **Set GitHub Actions secrets** so `deploy.yml` can ship updates:
   * `VPS_HOST` — `35.255.81.115` (or the domain).
   * `VPS_USER` — `aichaguimaoune`.
   * `VPS_SSH_KEY` — the private deploy key (use a dedicated key, not the user's personal one).
   * `VPS_KNOWN_HOSTS` — output of `ssh-keyscan -H 35.255.81.115` (optional; the workflow will scan if blank).
   * `APP_DIR` — `/opt/landing-generator`.
3. **Push to `main`** — the deploy workflow SSHes into the VPS, runs `git pull && docker compose up -d --build`, and verifies `/healthz`.

### Security follow-ups (read before going live)

* Rotate the SSH password that was shared in chat, and disable password auth in `/etc/ssh/sshd_config` once the deploy key is installed (`PasswordAuthentication no`).
* Rotate the Yunwu API key after handover.
* Confirm DNS for `landing.shopinzo.bond` points to the VPS before running certbot.
* Treat `output/` and `uploads/` as user data — neither is committed (`.gitignore`) and both should be backed up out-of-band if you care about retention.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The unit suite covers:

* Yunwu client request/response shape and error handling (`tests/test_yunwu_client.py`).
* Analyzer JSON parsing and schema validation (`tests/test_analyzer.py`).
* Copy writer parsing + Arabic content sanity (`tests/test_copy_writer.py`).
* Image normalization to 1024×3072 (`tests/test_image_gen.py`).
* Stitcher dimensions and seam blending (`tests/test_stitcher.py`).

## Open items (carry-over from PLAN.md)

These were flagged in the plan and still want a human decision before shipping:

1. Confirm Yunwu base URL, chat model name, and image model name match production.
2. Decide whether the deliverable should also include a real HTML landing page (today: long PNG only).
3. Confirm desired final image width — 1024 px is the default (final image is 1024 × 24576).
4. Provide the GitHub repo `owner/repo` and target branch.
5. Confirm the VPS has Docker pre-installed or run `scripts/vps_bootstrap.sh` from a fresh image.
