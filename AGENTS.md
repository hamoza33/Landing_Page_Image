# AGENTS.md — Landing Page Generator

## Project overview

Python (3.11+) FastAPI service that turns one uploaded product photo into a
fully automated 8-section Arabic GCC landing page. Each section is a 1024×1536
portrait image with the real product visually integrated, the bottom of one
section flowing seamlessly into the top of the next, and Arabic copy rendered
on top of the image (PIL + arabic-reshaper + bidi).

The pipeline is fully automated:

1. **Research** — vision identifies the product; optional Tavily web search
   enriches with brand/category/marketing facts.
2. **Copy** — one LLM call returns all 8 Arabic sections as JSON.
3. **Images** — 8 sequential `gpt-image-2-all` calls; each call passes
   `[product_photo, previous_bottom_strip]` so visuals chain seamlessly.
4. **Seam blend** — PIL alpha-blend pass between adjacent sections.
5. **Text overlay** — Arabic headlines/CTAs drawn in PIL on top of each PNG.

## Key commands

- **Install deps:** `pip install -r requirements.txt`
- **Run locally:** `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- **Run tests:** `python -m pytest -q`
- **Docker:** `docker compose up -d --build`

## Architecture

```
app/
├── main.py              # FastAPI routes + background job runner
├── config.py            # Settings (single source of truth, all env-driven)
├── schemas.py           # Pydantic models (ProductBrief, LandingCopy, JobRecord)
├── pipeline.py          # Orchestrator: research → copy → seq images → overlay
├── store.py             # JSON-on-disk job persistence
├── prompts/
│   ├── copy_system_ar.txt        # Arabic copy system prompt
│   └── sections/                 # Per-section Jinja2 image prompts
│       ├── _shared.j2            #   shared visual contract + seam rules
│       ├── hero.j2 / features.j2 / before_after.j2 / testimonials.j2
│       └── faq.j2 / lifestyle.j2 / education.j2 / closing.j2
└── services/
    ├── yunwu_client.py           # Async httpx wrapper (chat + image-edit)
    ├── researcher.py             # Vision + Tavily → enriched ProductBrief
    ├── analyzer.py               # Backwards-compat facade over researcher
    ├── copy_writer.py            # ProductBrief → LandingCopy (8 sections)
    ├── image_gen.py              # gpt-image-2-all renderer + seam helpers
    └── text_overlay.py           # Arabic text overlay (PIL + reshaper + bidi)
assets/fonts/
    ├── NotoNaskhArabic-Regular.ttf
    └── Tajawal-Bold.ttf
docs/
    └── SETTINGS.md               # Every env var, every API, every prompt var
```

## Code conventions

- Python type hints everywhere.
- pydantic v2 for all structured data.
- httpx (async) for all external HTTP calls (not `requests`).
- tenacity for retry logic.
- All secrets in env vars — never in code.
- Tests in `tests/` with `pytest` + `pytest-asyncio`.

## Yunwu API notes

- Chat / vision: `POST {BASE}/v1/chat/completions` (OpenAI shape). Parse
  text from `choices[0].message.content`.
- Image edit (multi-reference): `POST {BASE}/v1/images/generations` with
  model `gpt-image-2-all` and `image: [base64, base64, …]` (up to 5 refs).
- Supported sizes for `gpt-image-2-all`: `1024x1024`, `1536x1024`,
  `1024x1536` only. **Do not** request `1024x3072` — it's rejected.
- Image-edit calls can take 4–8 minutes each. `HTTP_TIMEOUT` defaults to 600s.

## Settings

See `docs/SETTINGS.md` for the full reference of every environment
variable, every external API, and every prompt template variable.
