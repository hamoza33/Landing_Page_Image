"""Runtime configuration — single source of truth for tunable variables.

Every value here is either an env-driven override or a documented default.
Anything you might want to change later lives here, NOT scattered in code.
See ``docs/SETTINGS.md`` for the full glossary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # ------- Yunwu / LLM API -------
    yunwu_api_key: str
    yunwu_base_url: str
    chat_model: str            # vision-capable LLM, e.g. gpt-5.4
    image_model: str           # text→image, e.g. gpt-image-2
    image_edit_model: str      # multi-image / image-edit, e.g. gpt-image-2-all

    # ------- Optional web research (Tavily) -------
    tavily_api_key: str | None
    web_research_enabled: bool

    # ------- Optional Google Vision -------
    google_vision_api_key: str | None
    analyzer_backend: str       # "yunwu" | "google_vision"

    # ------- App -------
    app_host: str
    app_port: int
    output_dir: Path
    upload_dir: Path
    assets_dir: Path
    # Public base URL of THIS service (e.g. https://landing.shopinzo.bond).
    # Used to expose uploaded product photos and previously rendered sections
    # as URLs that gpt-image-2-all can fetch directly via the ``image: [...]``
    # array. Must be reachable from Yunwu's servers.
    public_base_url: str

    # ------- Image generation -------
    # Yunwu gpt-image-2-all officially supports: 1024x1024, 1536x1024, 1024x1536.
    # We always render portrait at 1024x1536, then stack — no oversized 1024x3072.
    image_size: str             # "1024x1536"
    image_width: int            # parsed from image_size
    image_height: int           # parsed from image_size
    image_concurrency: int      # parallel image API calls (only for non-seamed flow)
    image_quality: str          # "high" | "medium" | "low"
    image_format: str           # "png" | "jpeg"

    # ------- Seamless flow (visual continuity between sections) -------
    seamless_flow: bool          # True ⇒ sequential render with previous-section bottom strip as reference
    seam_strip_height: int       # px taken from bottom of section N for reference
    seam_blend_height: int       # px of PIL alpha-blend fade between section N bottom and N+1 top

    # ------- Text overlay -------
    overlay_text_enabled: bool
    font_arabic_regular: Path
    font_arabic_bold: Path
    overlay_headline_size: int
    overlay_subhead_size: int
    overlay_body_size: int
    overlay_text_color: str       # hex, e.g. "#1f2937"
    overlay_shadow_color: str     # hex, e.g. "#00000088"
    overlay_max_width_ratio: float  # 0..1, fraction of image width for text wrap
    overlay_padding_ratio: float    # 0..1, vertical padding inside the panel

    # ------- HTTP -------
    http_timeout: float

    @classmethod
    def load(cls) -> "Settings":
        root = Path(__file__).resolve().parent.parent
        output_dir = Path(_env("OUTPUT_DIR", "./output") or "./output").resolve()
        upload_dir = Path(_env("UPLOAD_DIR", "./uploads") or "./uploads").resolve()
        assets_dir = Path(_env("ASSETS_DIR", str(root / "assets"))).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "fonts").mkdir(parents=True, exist_ok=True)

        # Image size: parse "WxH"
        size = _env("IMAGE_SIZE", "1024x1536") or "1024x1536"
        try:
            w_str, h_str = size.lower().split("x", 1)
            img_w, img_h = int(w_str), int(h_str)
        except (ValueError, AttributeError):
            img_w, img_h = 1024, 1536

        return cls(
            # Yunwu
            yunwu_api_key=_env("YUNWU_API_KEY", "") or "",
            yunwu_base_url=(_env("YUNWU_BASE_URL", "https://yunwu.ai") or "https://yunwu.ai").rstrip("/"),
            chat_model=_env("YUNWU_CHAT_MODEL", "gpt-5.4") or "gpt-5.4",
            image_model=_env("YUNWU_IMAGE_MODEL", "gpt-image-2") or "gpt-image-2",
            image_edit_model=_env("YUNWU_IMAGE_EDIT_MODEL", "gpt-image-2-all") or "gpt-image-2-all",

            # Tavily web research
            tavily_api_key=_env("TAVILY_API_KEY"),
            web_research_enabled=_env_bool("WEB_RESEARCH_ENABLED", True),

            # Google Vision
            google_vision_api_key=_env("GOOGLE_VISION_API_KEY"),
            analyzer_backend=(_env("ANALYZER_BACKEND", "yunwu") or "yunwu").lower(),

            # App
            app_host=_env("APP_HOST", "0.0.0.0") or "0.0.0.0",
            app_port=_env_int("APP_PORT", 8000),
            output_dir=output_dir,
            upload_dir=upload_dir,
            assets_dir=assets_dir,
            public_base_url=(_env("PUBLIC_BASE_URL", "https://landing.shopinzo.bond")
                             or "https://landing.shopinzo.bond").rstrip("/"),

            # Image
            image_size=f"{img_w}x{img_h}",
            image_width=img_w,
            image_height=img_h,
            image_concurrency=_env_int("IMAGE_CONCURRENCY", 3),
            image_quality=_env("IMAGE_QUALITY", "high") or "high",
            image_format=_env("IMAGE_FORMAT", "png") or "png",

            # Seamless
            seamless_flow=_env_bool("SEAMLESS_FLOW", True),
            seam_strip_height=_env_int("SEAM_STRIP_HEIGHT", 256),
            seam_blend_height=_env_int("SEAM_BLEND_HEIGHT", 96),

            # Overlay (default OFF — gpt-image-2-all renders the Arabic text
            # directly in the image; see app/prompts/sections/*.j2).
            overlay_text_enabled=_env_bool("OVERLAY_TEXT_ENABLED", False),
            font_arabic_regular=Path(
                _env("FONT_ARABIC_REGULAR", str(assets_dir / "fonts" / "NotoNaskhArabic-Regular.ttf"))
            ),
            font_arabic_bold=Path(
                _env("FONT_ARABIC_BOLD", str(assets_dir / "fonts" / "Tajawal-Bold.ttf"))
            ),
            overlay_headline_size=_env_int("OVERLAY_HEADLINE_SIZE", 96),
            overlay_subhead_size=_env_int("OVERLAY_SUBHEAD_SIZE", 56),
            overlay_body_size=_env_int("OVERLAY_BODY_SIZE", 44),
            overlay_text_color=_env("OVERLAY_TEXT_COLOR", "#1f2937") or "#1f2937",
            overlay_shadow_color=_env("OVERLAY_SHADOW_COLOR", "#00000099") or "#00000099",
            overlay_max_width_ratio=_env_float("OVERLAY_MAX_WIDTH_RATIO", 0.86),
            overlay_padding_ratio=_env_float("OVERLAY_PADDING_RATIO", 0.06),

            # HTTP
            http_timeout=_env_float("HTTP_TIMEOUT", 600.0),
        )


settings = Settings.load()
