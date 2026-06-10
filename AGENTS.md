# AGENTS.md — Landing Page Generator

## Project overview

Python (3.11+) FastAPI service that converts a product photo into an Arabic GCC landing page as a single tall PNG. Uses Yunwu's OpenAI-compatible API for vision analysis, Arabic copy generation, and section image generation.

## Key commands

- **Install deps:** `pip install -r requirements.txt`
- **Install dev deps:** `pip install -r requirements-dev.txt`
- **Run locally:** `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- **Run tests:** `python -m pytest -q`
- **Docker:** `docker compose up -d --build`

## Architecture

```
app/
├── main.py           # FastAPI routes + background job runner
├── config.py         # Settings from env vars via python-dotenv
├── schemas.py        # Pydantic models (ProductBrief, LandingCopy, etc.)
├── pipeline.py       # High-level orchestrator: analyze → copy → images → stitch
├── prompts/
│   └── copy_system_ar.txt   # Arabic system prompt for copy generation
├── services/
│   ├── yunwu_client.py      # Async httpx wrapper for Yunwu chat + image APIs
│   ├── analyzer.py          # Vision → ProductBrief
│   ├── copy_writer.py       # ProductBrief → LandingCopy (8 Arabic sections)
│   ├── image_gen.py         # 8 parallel portrait image generations + normalization
│   └── stitcher.py          # Vertical concat with seam alpha-blend
└── templates/               # Jinja2 templates (Arabic RTL upload + result page)
```

## Code conventions

- Python type hints everywhere.
- pydantic v2 for all structured data.
- httpx (async) for all external HTTP calls (not `requests`).
- tenacity for retry logic.
- All secrets in env vars — never in code.
- Tests in `tests/` with `pytest` + `pytest-asyncio`.

## Yunwu API notes

- Chat: `POST {BASE}/openai-response/v1/responses` (Responses API shape). Parse text from `output[0].content[0].text` or `output_text`.
- Image gen: `POST {BASE}/v1/images/generations` with model `gpt-image-2`. Max aspect 3:1, so 1024×3072 is the limit.
- Image edit: same endpoint with model `gpt-image-2-all` and `image: [base64…]`.
