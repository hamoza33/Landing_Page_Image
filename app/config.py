"""Runtime configuration loaded from environment variables + dashboard settings.

Dashboard settings (stored in ./data/settings.json) take priority over .env
values. This allows the admin to change API keys, models, etc. from the
web dashboard without restarting the server.
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
    hero_height: int
    image_concurrency: int

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
            hero_height=_env_int("HERO_HEIGHT", 2048),
            image_concurrency=_env_int("IMAGE_CONCURRENCY", 3),
        )

    @classmethod
    def load_live(cls) -> "Settings":
        """Load settings with dashboard overrides (live, not cached).
        
        Dashboard settings take priority over .env values.
        """
        from app.settings_store import load_settings as _load_dashboard

        dashboard = _load_dashboard()
        api = dashboard.get("api", {})
        image = dashboard.get("image", {})

        output_dir = Path(_env("OUTPUT_DIR", "./output") or "./output").resolve()
        upload_dir = Path(_env("UPLOAD_DIR", "./uploads") or "./uploads").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Dashboard values override env, but only if non-empty
        def _dash_or_env(dash_val, env_key: str, default: str = "") -> str:
            if dash_val and str(dash_val).strip():
                return str(dash_val).strip()
            return _env(env_key, default) or default

        return cls(
            yunwu_api_key=_dash_or_env(api.get("yunwu_api_key"), "YUNWU_API_KEY", ""),
            yunwu_base_url=_dash_or_env(api.get("yunwu_base_url"), "YUNWU_BASE_URL", "https://yunwu.ai").rstrip("/"),
            chat_model=_dash_or_env(api.get("chat_model"), "YUNWU_CHAT_MODEL", "gpt-5.4"),
            image_model=_dash_or_env(api.get("image_model"), "YUNWU_IMAGE_MODEL", "gpt-image-2"),
            image_edit_model=_dash_or_env(api.get("image_edit_model"), "YUNWU_IMAGE_EDIT_MODEL", "gpt-image-2-all"),
            google_vision_api_key=_dash_or_env(api.get("google_vision_api_key"), "GOOGLE_VISION_API_KEY", "") or None,
            analyzer_backend=_dash_or_env(api.get("analyzer_backend"), "ANALYZER_BACKEND", "yunwu").lower(),
            app_host=_env("APP_HOST", "0.0.0.0") or "0.0.0.0",
            app_port=_env_int("APP_PORT", 8000),
            output_dir=output_dir,
            upload_dir=upload_dir,
            image_width=int(image.get("image_width") or _env_int("IMAGE_WIDTH", 1024)),
            section_height=int(image.get("section_height") or _env_int("SECTION_HEIGHT", 3072)),
            hero_height=int(image.get("hero_height") or _env_int("HERO_HEIGHT", 2048)),
            image_concurrency=int(image.get("image_concurrency") or _env_int("IMAGE_CONCURRENCY", 3)),
        )


# Static settings for app startup (mounting dirs, etc.)
settings = Settings.load()
