"""Product researcher: photo → enriched ProductBrief.

Three steps, each isolated so you can disable web search via env if you want:

  1. **Vision identification** — Yunwu /v1/chat/completions with the product
     image and a strict JSON-only system prompt. Returns initial guesses:
     brand, name, category, visible claims, palette hints.

  2. **Web research** (optional, on by default if TAVILY_API_KEY is set) —
     fire Tavily searches with the top brand+name keywords and fold the
     snippets back into the brief via a follow-up LLM call.

  3. **Brief consolidation** — final LLM pass that merges vision + web hits
     into the strict ProductBrief JSON schema.

Outputs a fully populated ``ProductBrief`` (see ``app.schemas``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, settings as default_settings
from app.schemas import ProductBrief
from app.services.yunwu_client import YunwuClient, YunwuError

log = logging.getLogger(__name__)


VISION_INSTRUCTIONS = """You are a product-identification analyst.

You will see ONE product photo. Output ONLY a strict JSON object identifying
the product as precisely as possible. If you can read the brand/name from
the packaging, use it verbatim. If not, give your best inference.

Schema:
{
  "name": str,                  // product name as it appears or best inference
  "brand": str,                 // brand name or "" if unknown
  "category": str,              // broad category: "skincare", "haircare", "supplement", "cream", "kitchenware", ...
  "sub_category": str,          // narrower: "varicose veins cream", "argan oil serum", ...
  "country_of_origin": str,     // if visible on the package, else ""
  "visible_claims": [str],      // claims printed on the package
  "visible_text": [str],        // any text strings visible on the package (verbatim)
  "ingredients_visible": [str], // ingredients/materials printed on the package
  "search_keywords": [str],     // 3-6 short web-search phrases to learn more about this product
  "palette_hex": [str],         // 4-6 dominant colors as hex codes pulled from the photo
  "visual_style_keywords": [str]// 6-10 mood words for image generation
}

Output ONLY the JSON object, no prose, no code fences, no commentary.
"""


CONSOLIDATE_INSTRUCTIONS = """You are a senior product researcher writing a marketing brief.

You will receive:
  1. A vision analysis of the product photo (JSON).
  2. (Optional) Snippets gathered from web search about the same / similar product.

Synthesize them into ONE strict JSON brief used downstream by a copy writer
and an image generator. Be specific. Make benefits concrete. Avoid medical
or therapeutic claims unless the snippets explicitly support them. Prefer
emotional + practical benefits suitable for a GCC audience.

Schema:
{
  "name": str,
  "brand": str,
  "category": str,
  "sub_category": str,
  "country_of_origin": str,
  "materials": [str],                // for non-cosmetics: materials. for cosmetics: same as ingredients.
  "ingredients": [str],              // top 4-8 key ingredients (cosmetics/skincare/supplements). [] otherwise.
  "target_user": str,                // 1 sentence describing the buyer
  "primary_use": str,                // 1 sentence describing the use case
  "primary_problem_solved": str,     // 1 sentence describing the pain point
  "benefits": [str],                 // 5-7 concrete benefits, 1 sentence each
  "unique_selling_points": [str],    // 3-5 USPs vs alternatives
  "competitive_angles": [str],       // 2-3 angles to differentiate the marketing
  "web_research_summary": str,       // 2-3 sentence summary of the web findings (or "" if none)
  "web_research_sources": [str],     // URLs from the snippets (or [])
  "visual_style_keywords": [str],    // 6-10 mood/style words
  "palette_hex": [str]               // 4-6 hex colors guiding the visual identity
}

Output ONLY the JSON object, no prose, no code fences.
"""


_RETRY_EXCEPTIONS = (httpx.HTTPError, httpx.TimeoutException)


class ProductResearcher:
    """Photo → enriched ProductBrief."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)

    async def research(self, image_bytes: bytes, *, mime: str = "image/jpeg") -> ProductBrief:
        # 1. Vision identification.
        vision = await self._vision_identify(image_bytes, mime=mime)

        # 2. Web research (optional).
        web_snippets: list[dict[str, str]] = []
        if (
            self.settings.web_research_enabled
            and self.settings.tavily_api_key
            and vision.get("search_keywords")
        ):
            try:
                web_snippets = await self._tavily_search(vision["search_keywords"])
            except Exception as exc:  # noqa: BLE001 — research is best-effort
                log.warning("Tavily search failed (%s); continuing without web context.", exc)

        # 3. Consolidate into final brief.
        return await self._consolidate(vision, web_snippets)

    # --------------------------------------------------------- vision

    async def _vision_identify(self, image_bytes: bytes, *, mime: str) -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        user_content = [
            {"type": "text", "text": "Identify this product as precisely as you can."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        text = await self.client.respond(
            instructions=VISION_INSTRUCTIONS,
            user_content=user_content,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return _coerce_json(text)

    # --------------------------------------------------------- tavily

    async def _tavily_search(self, keywords: list[str]) -> list[dict[str, str]]:
        """Fire one Tavily request per top keyword (up to 3), collect snippets."""

        if not self.settings.tavily_api_key:
            return []
        top = [k for k in keywords if k][:3]
        results: list[dict[str, str]] = []

        async def search_one(q: str) -> list[dict[str, str]]:
            payload = {
                "api_key": self.settings.tavily_api_key,
                "query": q,
                "search_depth": "basic",
                "max_results": 4,
                "include_answer": True,
            }
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(2),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    async with httpx.AsyncClient(timeout=30) as c:
                        r = await c.post("https://api.tavily.com/search", json=payload)
                    if r.status_code >= 400:
                        log.warning("Tavily %s for %r: %s", r.status_code, q, r.text[:200])
                        return []
                    data = r.json()
                    out: list[dict[str, str]] = []
                    if data.get("answer"):
                        out.append({"url": "tavily://answer", "content": data["answer"]})
                    for it in data.get("results", [])[:4]:
                        out.append(
                            {
                                "url": it.get("url", ""),
                                "content": (it.get("content") or "")[:600],
                            }
                        )
                    return out
            return []  # pragma: no cover

        per_query = await asyncio.gather(*[search_one(q) for q in top], return_exceptions=True)
        for got in per_query:
            if isinstance(got, list):
                results.extend(got)
        return results

    # --------------------------------------------------------- consolidate

    async def _consolidate(
        self,
        vision: dict[str, Any],
        snippets: list[dict[str, str]],
    ) -> ProductBrief:
        if snippets:
            snippet_block = "\n\n".join(
                f"[{i + 1}] ({s.get('url', '')})\n{s.get('content', '')}"
                for i, s in enumerate(snippets)
            )
            user_text = (
                "Vision analysis:\n"
                + json.dumps(vision, ensure_ascii=False, indent=2)
                + "\n\nWeb research snippets:\n"
                + snippet_block
            )
        else:
            user_text = (
                "Vision analysis:\n"
                + json.dumps(vision, ensure_ascii=False, indent=2)
                + "\n\nNo web research available."
            )

        text = await self.client.respond(
            instructions=CONSOLIDATE_INSTRUCTIONS,
            user_content=user_text,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = _coerce_json(text)
        try:
            return ProductBrief.model_validate(raw)
        except ValidationError as exc:
            raise YunwuError(f"Brief failed schema: {exc}") from exc


# --------------------------------------------------------------------- helpers


def _coerce_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if first.lower().strip() in {"json", "json5"}:
                cleaned = rest
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise YunwuError(f"Researcher returned non-JSON: {text[:300]}") from exc
