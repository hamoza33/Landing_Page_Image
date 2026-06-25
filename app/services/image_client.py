"""Multi-key image generation client with retry and fallback.

Supports multiple API keys for the same provider, plus fallback to a
secondary provider (e.g., Yunwu → DuckCoding). Each key gets 3 attempts
before moving to the next key, then falls back to the secondary provider.

Configuration is read from the settings store at call time so dashboard
changes take effect immediately.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ImageGenError(RuntimeError):
    """Raised when all API keys and providers have been exhausted."""


class ImageClient:
    """Generate images with automatic retry across multiple keys and providers."""

    def __init__(self, *, timeout: float = 180.0):
        self._timeout = timeout

    async def generate_image(
        self,
        *,
        prompt: str,
        size: str,
        reference_images: list[bytes] | None = None,
        model: str = "gpt-image-2",
    ) -> bytes:
        """Generate a single image, trying all configured keys and providers.

        Order:
        1. Try each primary (Yunwu) key up to 3 attempts each.
        2. If all primary keys fail, try each secondary (DuckCoding) key up to 3 attempts each.

        Returns raw image bytes on success.
        """
        from app.settings_store import load_settings

        settings = load_settings()
        image_settings = settings.get("image_apis", {})

        # Determine primary and fallback provider
        default_provider = image_settings.get("default_provider", "yunwu")

        yunwu_keys = image_settings.get("yunwu_keys", [])
        yunwu_base = image_settings.get("yunwu_base_url", "https://yunwu.ai")

        duck_keys = image_settings.get("duckcoding_keys", [])
        duck_base = image_settings.get("duckcoding_base_url", "https://api.duckcoding.ai")

        # Fallback: read main API key if no multi-keys configured
        if not yunwu_keys:
            main_key = settings.get("api", {}).get("yunwu_api_key", "")
            if main_key:
                yunwu_keys = [main_key]

        # Build provider list based on default
        if default_provider == "duckcoding":
            providers = [
                ("DuckCoding", duck_base, duck_keys),
                ("Yunwu", yunwu_base, yunwu_keys),
            ]
        else:
            providers = [
                ("Yunwu", yunwu_base, yunwu_keys),
                ("DuckCoding", duck_base, duck_keys),
            ]

        all_errors: list[str] = []

        for provider_name, base_url, keys in providers:
            if not keys:
                continue
            for key_idx, api_key in enumerate(keys):
                for attempt in range(3):
                    try:
                        result = await self._call_api(
                            base_url=base_url,
                            api_key=api_key,
                            prompt=prompt,
                            size=size,
                            model=model,
                            reference_images=reference_images,
                        )
                        log.info(
                            "Image generated via %s key %d (attempt %d)",
                            provider_name, key_idx + 1, attempt + 1,
                        )
                        return result
                    except Exception as exc:
                        err_msg = f"{provider_name} key {key_idx+1} attempt {attempt+1}: {exc}"
                        log.warning("Image gen failed: %s", err_msg)
                        all_errors.append(err_msg)
                        # On 429 (rate limit) or 5xx, try next attempt/key
                        continue

        raise ImageGenError(
            f"All image API keys exhausted. Errors:\n" +
            "\n".join(all_errors[-6:])  # Show last 6 errors
        )

    async def _call_api(
        self,
        *,
        base_url: str,
        api_key: str,
        prompt: str,
        size: str,
        model: str,
        reference_images: list[bytes] | None,
    ) -> bytes:
        """Make a single API call to generate an image."""
        url = f"{base_url.rstrip('/')}/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "quality": "high",
            "response_format": "b64_json",
            "output_format": "png",
        }

        # Add reference images if provided
        if reference_images:
            encoded = [base64.b64encode(b).decode("ascii") for b in reference_images]
            payload["image"] = encoded

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code >= 400:
            raise ImageGenError(
                f"{resp.status_code} at {url}: {resp.text[:300]}"
            )

        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype.lower():
            raise ImageGenError(f"Non-JSON response from {url}: {ctype}")

        data = resp.json()
        return await _extract_single_image(data)


async def _extract_single_image(data: dict[str, Any]) -> bytes:
    """Extract the first image from an API response."""
    items = data.get("data") or []
    if not items:
        raise ImageGenError(f"No images in response: keys={list(data)}")

    item = items[0]
    if "b64_json" in item and item["b64_json"]:
        return base64.b64decode(item["b64_json"])
    elif "url" in item and item["url"]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(item["url"])
            resp.raise_for_status()
            return resp.content
    else:
        raise ImageGenError(f"Image entry missing both b64_json and url: {item}")
