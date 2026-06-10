# Settings Overview

Every tunable variable in the project lives in `app/config.py` (see
`Settings`). Each is overridable via environment variable (and `.env` is
loaded automatically). This document lists every variable, every external
API and tool used, and every prompt-template variable, so you can adjust
the generator without reading code.

---

## 1. External APIs and Tools

### 1.1 Yunwu (LLM + image generation)

Base URL: `YUNWU_BASE_URL` (default `https://yunwu.ai`).

| Endpoint | Purpose | Model env var | Default |
| --- | --- | --- | --- |
| `POST /v1/chat/completions` | Vision identification + Arabic copy generation. OpenAI chat-completions shape with `image_url` parts. | `YUNWU_CHAT_MODEL` | `gpt-5.4` |
| `POST /v1/images/generations` (text→image) | Reserved (not currently used in seamless flow). | `YUNWU_IMAGE_MODEL` | `gpt-image-2` |
| `POST /v1/images/generations` (image-edit, multi-reference) | All 8 section images. Sends up to 5 reference images in the `image` array. | `YUNWU_IMAGE_EDIT_MODEL` | `gpt-image-2-all` |

Auth: `YUNWU_API_KEY` (Bearer header).

Image-edit request shape:
```json
{
  "model": "gpt-image-2-all",
  "prompt": "...",
  "size": "1024x1536",
  "image": ["<base64>", "<base64>"],
  "response_format": "b64_json",
  "output_format": "png",
  "quality": "high",
  "n": 1
}
```

Officially supported sizes for `gpt-image-2-all` are `1024x1024`,
`1536x1024`, `1024x1536`. We default to `1024x1536` (portrait). Yunwu
does NOT accept `1024x3072`.

### 1.2 Tavily (optional web research)

Base URL: `https://api.tavily.com`.

| Endpoint | Purpose |
| --- | --- |
| `POST /search` | Looks up a product by keywords; returns `answer` + `results[].content` snippets that get folded into the brief. |

Auth: `TAVILY_API_KEY` (in body).

Toggle with `WEB_RESEARCH_ENABLED=true|false`. If the key is missing or
the call fails, the pipeline still runs — it just lacks the web summary.

### 1.3 Local libraries

* `Pillow` — image normalization, seam blending, text overlay rasterization.
* `arabic-reshaper` + `python-bidi` — correct Arabic glyph reshaping and
  RTL bidirectional ordering before drawing into PIL.
* `Jinja2` — per-section prompt templates in `app/prompts/sections/`.
* `httpx` + `tenacity` — async HTTP with exponential-backoff retries.
* `FastAPI` + `Uvicorn` — web layer.
* `Pydantic` — typed schemas (`ProductBrief`, `LandingCopy`, `JobRecord`).

---

## 2. Environment Variables

All defaults shown apply when the variable is unset.

### 2.1 Yunwu / LLM

| Var | Default | Description |
| --- | --- | --- |
| `YUNWU_API_KEY` | *(required)* | Bearer token for Yunwu. |
| `YUNWU_BASE_URL` | `https://yunwu.ai` | Yunwu base URL. |
| `YUNWU_CHAT_MODEL` | `gpt-5.4` | Vision + text model. |
| `YUNWU_IMAGE_MODEL` | `gpt-image-2` | Plain text→image (unused in seamless flow). |
| `YUNWU_IMAGE_EDIT_MODEL` | `gpt-image-2-all` | Multi-reference image-edit model. |

### 2.2 Web Research (Tavily)

| Var | Default | Description |
| --- | --- | --- |
| `TAVILY_API_KEY` | *(unset)* | Enables real web search if set. |
| `WEB_RESEARCH_ENABLED` | `true` | Master switch. Setting `false` skips Tavily even if a key is set. |

### 2.3 Image Generation

| Var | Default | Description |
| --- | --- | --- |
| `IMAGE_SIZE` | `1024x1536` | Per-section render size. Must be one of Yunwu's supported portrait sizes. |
| `IMAGE_CONCURRENCY` | `3` | Parallel image API calls. **Note:** seamless flow (default) overrides this and runs sequentially so previous bottom strips can chain. |
| `IMAGE_QUALITY` | `high` | `low` / `medium` / `high`. |
| `IMAGE_FORMAT` | `png` | Output container. |

### 2.4 Seamless Visual Continuity

| Var | Default | Description |
| --- | --- | --- |
| `SEAMLESS_FLOW` | `true` | If true, sections render sequentially and each one is fed the previous bottom strip as a reference image. |
| `SEAM_STRIP_HEIGHT` | `256` | Pixels taken from the bottom of section N to seed section N+1. |
| `SEAM_BLEND_HEIGHT` | `96` | Pixels of PIL alpha-blend at the join (belt-and-suspenders pass after the model render). Set to `0` to disable. |

### 2.5 Text Overlay (Arabic on image)

| Var | Default | Description |
| --- | --- | --- |
| `OVERLAY_TEXT_ENABLED` | `true` | Master switch. If false, raw model output is saved without text. |
| `FONT_ARABIC_REGULAR` | `assets/fonts/NotoNaskhArabic-Regular.ttf` | Body / regular weight. |
| `FONT_ARABIC_BOLD` | `assets/fonts/Tajawal-Bold.ttf` | Headlines / CTAs. |
| `OVERLAY_HEADLINE_SIZE` | `96` | px. Used for `headline` and `cta` blocks. |
| `OVERLAY_SUBHEAD_SIZE` | `56` | px. |
| `OVERLAY_BODY_SIZE` | `44` | px. Used for `body` and `list` blocks. |
| `OVERLAY_TEXT_COLOR` | `#1f2937` | Hex (RGB or RGBA). |
| `OVERLAY_SHADOW_COLOR` | `#00000099` | Hex (RGBA). Used only on panel-less text. |
| `OVERLAY_MAX_WIDTH_RATIO` | `0.86` | Fraction of image width available to text wrap. |
| `OVERLAY_PADDING_RATIO` | `0.06` | Horizontal padding inside the panel. |

### 2.6 Analyzer Backend

| Var | Default | Description |
| --- | --- | --- |
| `ANALYZER_BACKEND` | `yunwu` | Currently only `yunwu` is wired up. Reserved for plug-in backends. |
| `GOOGLE_VISION_API_KEY` | *(unset)* | Reserved. |

### 2.7 App / Runtime

| Var | Default | Description |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | |
| `APP_PORT` | `8000` | |
| `OUTPUT_DIR` | `./output` | Per-job folder root. |
| `UPLOAD_DIR` | `./uploads` | Original product photos. |
| `ASSETS_DIR` | `./assets` | Fonts and other static assets. |
| `HTTP_TIMEOUT` | `600` | Seconds. Image-edit calls can routinely take 4–8 minutes per section. |

---

## 3. Prompt Templates

Per-section prompt templates live in `app/prompts/sections/` as Jinja2
files (`.j2`). Edit them to change the visual direction of a section
without touching Python.

| File | Section |
| --- | --- |
| `_shared.j2` | Common visual contract (palette, mood, seam rules). Included at the top of every section template. |
| `hero.j2` | Section 1 — Hero. |
| `features.j2` | Section 2 — Features. |
| `before_after.j2` | Section 3 — Before / After. |
| `testimonials.j2` | Section 4 — Testimonials. |
| `faq.j2` | Section 5 — FAQ. |
| `lifestyle.j2` | Section 6 — Lifestyle. |
| `education.j2` | Section 7 — Education. |
| `closing.j2` | Section 8 — Closing / CTA. |

Variables available to every template:

* `section_number` — 1..8.
* `section_role` — short human description of the section's job.
* `brief` — the full `ProductBrief`. Common fields: `brief.name`,
  `brief.brand`, `brief.category`, `brief.sub_category`,
  `brief.target_user`, `brief.primary_use`, `brief.benefits`,
  `brief.ingredients`, `brief.materials`, `brief.country_of_origin`,
  `brief.unique_selling_points`, `brief.competitive_angles`,
  `brief.web_research_summary`, `brief.visual_style_keywords`,
  `brief.palette_hex`.
* `copy` — the full `LandingCopy`. e.g. `copy.hero.headline`,
  `copy.features.items[*].title`, `copy.faq.items[*].question`, etc.
* `palette_str` — comma-joined palette hex.
* `style_keywords` — comma-joined visual style keywords.
* `seamless_top` — `True` if this section has a previous-section bottom
  strip to continue from (used to switch the seam instructions on/off).

The Arabic copy template is `app/prompts/copy_system_ar.txt`. Edit that
to change tone, length, or schema constraints.

---

## 4. Variables in the Pydantic Schemas

Editing `app/schemas.py` lets you add or rename fields. The current
schemas are:

* `ProductBrief` — output of the researcher; consumed by both the copy
  writer and every image prompt.
* `LandingCopy` — output of the copy writer; structured into 8 sections.
* `JobRecord` / `JobSection` — runtime job state, persisted to
  `output/<id>/job.json` so jobs survive container restarts.

---

## 5. Job Artifacts

Per-job outputs land in `output/<job_id>/`:

```
output/<job_id>/
  brief.json          # ProductBrief
  copy.json           # LandingCopy
  prompts.json        # Per-section prompt actually sent
  job.json            # JobRecord (status, section list, paths)
  section_1_hero.png
  section_2_features.png
  ...
  section_8_closing.png
  upload.<ext>        # The user's original product image
```

---

## 6. Quick Tweaks

* **Make text bigger:** bump `OVERLAY_HEADLINE_SIZE` to `120`, `OVERLAY_BODY_SIZE` to `52`.
* **Disable text on images:** `OVERLAY_TEXT_ENABLED=false`.
* **Disable web research:** `WEB_RESEARCH_ENABLED=false`.
* **Use square images instead of portrait:** `IMAGE_SIZE=1024x1024`. (You
  may also want to drop `OVERLAY_HEADLINE_SIZE` to ~72.)
* **Tighter or looser seam:** `SEAM_BLEND_HEIGHT=160` (smoother) or
  `SEAM_BLEND_HEIGHT=0` (let the model handle it alone).
* **Change a section's prompt direction without touching Python:** edit
  the matching `.j2` file in `app/prompts/sections/`.
