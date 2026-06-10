"""Image → ProductBrief.

Default backend uses the Yunwu Responses API with an image input part.
Google Vision is available as an opt-in fallback (set ``ANALYZER_BACKEND=google_vision``)
but is intentionally minimal — it returns the strongest web/label hits and lets
the copy generator fill in the gaps.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.config import Settings, settings as default_settings
from app.schemas import ProductBrief
from app.services.yunwu_client import YunwuClient, YunwuError

log = logging.getLogger(__name__)


ANALYZER_INSTRUCTIONS = """You are a product-marketing analyst.

You will see one product photo. Identify the product and produce a strict JSON
object with these keys:

- name: short product name in English (3-6 words).
- category: broad retail category (e.g. "kitchenware", "skincare").
- materials: list of likely materials/ingredients (English).
- target_user: short description of the primary buyer (English).
- primary_use: one short sentence on the main use case (English).
- benefits: list of 4-6 concrete benefits relevant to a GCC consumer.
- visual_style_keywords: list of 6-10 visual mood words (palette, textures,
  lighting, cultural cues that fit the GCC market — e.g. "warm sand tones",
  "majlis interior", "soft golden hour").

Output ONLY the JSON object, with no prose, no code fences, no commentary.
"""


class ProductAnalyzer:
    """Analyze a product image and return a structured brief."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)

    async def analyze(self, image_bytes: bytes, *, mime: str = "image/jpeg") -> ProductBrief:
        backend = self.settings.analyzer_backend
        if backend == "google_vision":
            try:
                return await self._google_vision(image_bytes)
            except Exception as exc:  # noqa: BLE001 — fallback to Yunwu vision
                log.warning("Google Vision failed (%s); falling back to Yunwu.", exc)
        return await self._yunwu_vision(image_bytes, mime=mime)

    # --------------------------------------------------------------- yunwu

    async def _yunwu_vision(self, image_bytes: bytes, *, mime: str) -> ProductBrief:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        # Chat-completions vision content parts.
        user_content = [
            {"type": "text", "text": "Analyze this product image."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        text = await self.client.respond(
            instructions=ANALYZER_INSTRUCTIONS,
            user_content=user_content,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return _parse_brief(text)

    # ------------------------------------------------------- google vision

    async def _google_vision(self, image_bytes: bytes) -> ProductBrief:
        if not self.settings.google_vision_api_key:
            raise RuntimeError("GOOGLE_VISION_API_KEY not configured")
        url = (
            "https://vision.googleapis.com/v1/images:annotate"
            f"?key={self.settings.google_vision_api_key}"
        )
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "requests": [
                {
                    "image": {"content": b64},
                    "features": [
                        {"type": "LABEL_DETECTION", "maxResults": 10},
                        {"type": "WEB_DETECTION", "maxResults": 10},
                    ],
                }
            ]
        }
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        ann = (body.get("responses") or [{}])[0]

        labels = [l["description"] for l in ann.get("labelAnnotations", []) if l.get("description")]
        web = ann.get("webDetection", {}) or {}
        web_entities = [e["description"] for e in web.get("webEntities", []) if e.get("description")]
        best_guess = (web.get("bestGuessLabels") or [{}])[0].get("label", "")

        name = best_guess or (web_entities[0] if web_entities else (labels[0] if labels else "Product"))
        return ProductBrief(
            name=name[:80],
            category=labels[0] if labels else "general",
            materials=[],
            target_user="GCC consumer",
            primary_use="",
            benefits=[],
            visual_style_keywords=labels[:6] + web_entities[:4],
        )


# --------------------------------------------------------------------- helpers


def _parse_brief(text: str) -> ProductBrief:
    """Best-effort JSON extraction with a tolerant fallback."""

    cleaned = text.strip()
    # Strip code fences if the model added them despite instructions.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # Drop optional language hint on first line.
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if first.lower().strip() in {"json", "json5"}:
                cleaned = rest
    # Find the first JSON object braces.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise YunwuError(f"Analyzer returned non-JSON: {text[:300]}") from exc
    try:
        return ProductBrief.model_validate(data)
    except ValidationError as exc:
        raise YunwuError(f"Analyzer JSON failed schema: {exc}") from exc


def load_image_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()
