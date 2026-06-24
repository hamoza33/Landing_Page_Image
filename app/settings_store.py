"""Persistent settings storage.

Manages runtime-editable settings in a JSON file. Falls back to env/defaults
when a setting hasn't been explicitly overridden by the admin.
"""

from __future__ import annotations

import json
import logging
import hashlib
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

_SETTINGS_FILE = Path("./data/settings.json")
_LOCK = Lock()

# Default admin credentials (hashed)
_DEFAULT_ADMIN_USER = "admin"
_DEFAULT_ADMIN_PASS_HASH = hashlib.sha256("admin123".encode()).hexdigest()


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _ensure_file() -> Path:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _SETTINGS_FILE.exists():
        default = _default_settings()
        _SETTINGS_FILE.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
    return _SETTINGS_FILE


def _default_settings() -> dict[str, Any]:
    return {
        "auth": {
            "username": _DEFAULT_ADMIN_USER,
            "password_hash": _DEFAULT_ADMIN_PASS_HASH,
        },
        "api": {
            "yunwu_api_key": "",
            "yunwu_base_url": "https://yunwu.ai",
            "chat_model": "gpt-5.4",
            "image_model": "gpt-image-2",
            "image_edit_model": "gpt-image-2-all",
            "google_vision_api_key": "",
            "analyzer_backend": "yunwu",
        },
        "image": {
            "image_width": 1024,
            "section_height": 3072,
            "image_concurrency": 3,
        },
        "app": {
            "app_host": "0.0.0.0",
            "app_port": 8000,
        },
        "prompts": {
            "copy_system_ar": "",
            "analyzer_instructions": "",
        },
        "ui": {
            "show_api_icons": True,
            "api_icons_config": {
                "yunwu": {"visible": True, "label": "Yunwu AI"},
                "google_vision": {"visible": True, "label": "Google Vision"},
                "openai": {"visible": False, "label": "OpenAI"},
            },
        },
    }


def load_settings() -> dict[str, Any]:
    """Load all settings from disk."""
    with _LOCK:
        path = _ensure_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read settings file: %s — using defaults", exc)
            data = _default_settings()
        # Merge with defaults to ensure all keys exist
        defaults = _default_settings()
        return _deep_merge(defaults, data)


def save_settings(data: dict[str, Any]) -> None:
    """Save settings to disk."""
    with _LOCK:
        _ensure_file()
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_setting(section: str, key: str | None = None) -> Any:
    """Get a specific setting section or key."""
    settings = load_settings()
    if section not in settings:
        return None
    if key is None:
        return settings[section]
    return settings[section].get(key)


def update_setting(section: str, key: str, value: Any) -> None:
    """Update a single setting."""
    settings = load_settings()
    if section not in settings:
        settings[section] = {}
    settings[section][key] = value
    save_settings(settings)


def update_section(section: str, data: dict[str, Any]) -> None:
    """Update an entire settings section."""
    settings = load_settings()
    if section not in settings:
        settings[section] = {}
    settings[section].update(data)
    save_settings(settings)


def verify_credentials(username: str, password: str) -> bool:
    """Verify admin login credentials."""
    settings = load_settings()
    auth = settings.get("auth", {})
    stored_user = auth.get("username", _DEFAULT_ADMIN_USER)
    stored_hash = auth.get("password_hash", _DEFAULT_ADMIN_PASS_HASH)
    return username == stored_user and _hash_password(password) == stored_hash


def change_credentials(new_username: str | None = None, new_password: str | None = None) -> None:
    """Change admin credentials."""
    settings = load_settings()
    if "auth" not in settings:
        settings["auth"] = {}
    if new_username:
        settings["auth"]["username"] = new_username
    if new_password:
        settings["auth"]["password_hash"] = _hash_password(new_password)
    save_settings(settings)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, keeping base keys as defaults."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
