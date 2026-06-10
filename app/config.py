"""Runtime configuration loaded from environment variables.

All secrets stay in env vars / .env (never committed). Defaults here are safe
fallbacks for local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    yunwu_api_key: str
    yunwu_base_url: str
    chat_model: str
    image_model: str
    image_edit_model: str

    google_vision_api_key: str | None
    analyzer_backend: str  # "yunwu" | "google_vision"

    app_host: str
    app_port: int
    output_dir: Path
    upload_dir: Path

    image_width: int
    section_height: int
    image_concurrency: int
    http_timeout: float

    @classmethod
    def load(cls) -> "Settings":
        output_dir = Path(_env("OUTPUT_DIR", "./output") or "./output").resolve()
        upload_dir = Path(_env("UPLOAD_DIR", "./uploads") or "./uploads").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            yunwu_api_key=_env("YUNWU_API_KEY", "") or "",
            yunwu_base_url=(_env("YUNWU_BASE_URL", "https://yunwu.ai") or "https://yunwu.ai").rstrip("/"),
            chat_model=_env("YUNWU_CHAT_MODEL", "gpt-5.4") or "gpt-5.4",
            image_model=_env("YUNWU_IMAGE_MODEL", "gpt-image-2") or "gpt-image-2",
            image_edit_model=_env("YUNWU_IMAGE_EDIT_MODEL", "gpt-image-2-all") or "gpt-image-2-all",
            google_vision_api_key=_env("GOOGLE_VISION_API_KEY"),
            analyzer_backend=(_env("ANALYZER_BACKEND", "yunwu") or "yunwu").lower(),
            app_host=_env("APP_HOST", "0.0.0.0") or "0.0.0.0",
            app_port=_env_int("APP_PORT", 8000),
            output_dir=output_dir,
            upload_dir=upload_dir,
            image_width=_env_int("IMAGE_WIDTH", 1024),
            section_height=_env_int("SECTION_HEIGHT", 3072),
            image_concurrency=_env_int("IMAGE_CONCURRENCY", 3),
            http_timeout=float(_env("HTTP_TIMEOUT", "600") or "600"),
        )


settings = Settings.load()
