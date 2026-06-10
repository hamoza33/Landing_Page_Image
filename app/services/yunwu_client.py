"""Async client for the Yunwu OpenAI-compatible API.

Two endpoints are wrapped:

* ``POST {BASE}/openai-response/v1/responses`` — Responses API shape, used
  for both vision (image input) and pure-text generations. We parse
  ``output[0].content[0].text`` (also accepting ``output_text`` if present).
* ``POST {BASE}/v1/images/generations`` — image generation. We accept either
  a base64 ``b64_json`` payload or a remote ``url`` and normalize to bytes.

The image-edit endpoint (``gpt-image-2-all``) is exposed through
``image_edit`` for the hero / lifestyle scenes that use the user's actual
product photo as a visual anchor.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, settings as default_settings

log = logging.getLogger(__name__)

_RETRY_EXCEPTIONS = (httpx.HTTPError, httpx.TimeoutException)


class YunwuError(RuntimeError):
    """Raised when Yunwu returns a non-2xx response or unparseable payload."""


class YunwuClient:
    """Thin async wrapper around the Yunwu HTTP API."""

    def __init__(self, settings: Settings | None = None, *, timeout: float = 120.0):
        self.settings = settings or default_settings
        if not self.settings.yunwu_api_key:
            log.warning("YUNWU_API_KEY is not set; calls will fail.")
        self._timeout = timeout

    # ------------------------------------------------------------------ utils

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.yunwu_api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=20),
            retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers)
                if resp.status_code >= 400:
                    raise YunwuError(
                        f"Yunwu {resp.status_code} at {url}: {resp.text[:500]}"
                    )
                try:
                    return resp.json()
                except ValueError as exc:
                    raise YunwuError(f"Non-JSON response from Yunwu: {exc}") from exc
        raise YunwuError("Retry loop exited without response")  # pragma: no cover

    # --------------------------------------------------------- responses (chat)

    async def respond(
        self,
        *,
        instructions: str,
        user_content: list[dict[str, Any]] | str,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Call the Responses API and return the assistant's text output.

        ``user_content`` can be a plain string or a Responses-API content list
        (e.g. ``[{"type": "input_text", ...}, {"type": "input_image", ...}]``).
        """

        url = f"{self.settings.yunwu_base_url}/openai-response/v1/responses"
        if isinstance(user_content, str):
            user_content = [{"type": "input_text", "text": user_content}]

        payload: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": instructions}]},
                {"role": "user", "content": user_content},
            ],
        }
        if response_format is not None:
            # Responses API uses "text.format" for structured output.
            payload["text"] = {"format": response_format}
        if temperature is not None:
            payload["temperature"] = temperature

        data = await self._post(url, payload)
        return _extract_response_text(data)

    # ----------------------------------------------------------- image generate

    async def image(
        self,
        *,
        prompt: str,
        size: str,
        model: str | None = None,
        quality: str = "high",
        fmt: str = "png",
        n: int = 1,
    ) -> list[bytes]:
        """Generate ``n`` images for ``prompt`` and return raw bytes per image."""

        url = f"{self.settings.yunwu_base_url}/v1/images/generations"
        payload = {
            "model": model or self.settings.image_model,
            "prompt": prompt,
            "size": size,
            "n": n,
            "quality": quality,
            "response_format": "b64_json",
            "output_format": fmt,
        }
        data = await self._post(url, payload)
        return await _extract_images(data)

    async def image_edit(
        self,
        *,
        prompt: str,
        size: str,
        reference_images: list[bytes],
        model: str | None = None,
        quality: str = "high",
        fmt: str = "png",
        n: int = 1,
    ) -> list[bytes]:
        """Image edit / multi-image route (``gpt-image-2-all``).

        Reference images are sent as base64 strings under the ``image`` field,
        matching the Yunwu/OpenAI image-edit JSON shape.
        """

        url = f"{self.settings.yunwu_base_url}/v1/images/generations"
        encoded = [base64.b64encode(b).decode("ascii") for b in reference_images]
        payload = {
            "model": model or self.settings.image_edit_model,
            "prompt": prompt,
            "size": size,
            "n": n,
            "quality": quality,
            "image": encoded,
            "response_format": "b64_json",
            "output_format": fmt,
        }
        data = await self._post(url, payload)
        return await _extract_images(data)


# --------------------------------------------------------------------- helpers


def _extract_response_text(data: dict[str, Any]) -> str:
    """Pull assistant text out of a Responses-API payload.

    Tolerates two shapes:
      * ``output_text`` short-cut field.
      * ``output[*].content[*].text`` (preferred).
    """

    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]

    chunks: list[str] = []
    for item in data.get("output") or []:
        for c in item.get("content") or []:
            text = c.get("text") if isinstance(c, dict) else None
            if isinstance(text, str):
                chunks.append(text)
            elif isinstance(text, dict) and isinstance(text.get("value"), str):
                chunks.append(text["value"])
    if chunks:
        return "".join(chunks)

    # Older chat-completions fallback shape.
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content

    raise YunwuError(f"Could not parse response text from payload: keys={list(data)}")


async def _extract_images(data: dict[str, Any]) -> list[bytes]:
    items = data.get("data") or []
    if not items:
        raise YunwuError(f"No images in response: keys={list(data)}")
    out: list[bytes] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for item in items:
            if "b64_json" in item and item["b64_json"]:
                out.append(base64.b64decode(item["b64_json"]))
            elif "url" in item and item["url"]:
                resp = await client.get(item["url"])
                resp.raise_for_status()
                out.append(resp.content)
            else:
                raise YunwuError(f"Image entry missing both b64_json and url: {item}")
    return out
