"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure project root on sys.path.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure required env vars exist before app modules load.
os.environ.setdefault("YUNWU_API_KEY", "test-key")
os.environ.setdefault("YUNWU_BASE_URL", "https://example.invalid")


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Return a Settings instance whose paths point to a temp dir."""
    from app.config import Settings  # local import after env is set

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    return Settings.load()
