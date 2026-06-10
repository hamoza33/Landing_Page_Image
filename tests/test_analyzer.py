"""Analyzer tests — ensure JSON parsing is tolerant and schema validates."""

from __future__ import annotations

import json

import pytest

from app.schemas import ProductBrief
from app.services.analyzer import ProductAnalyzer, _parse_brief
from app.services.yunwu_client import YunwuError


class _FakeClient:
    def __init__(self, text: str):
        self._text = text

    async def respond(self, **_kwargs) -> str:  # noqa: D401, ANN001
        return self._text


def test_parse_brief_accepts_clean_json():
    raw = json.dumps(
        {
            "name": "Argan Oil Hair Serum",
            "category": "haircare",
            "materials": ["argan oil", "vitamin E"],
            "target_user": "GCC women 25-45",
            "primary_use": "deep hair conditioning",
            "benefits": ["smoother hair", "reduced frizz"],
            "visual_style_keywords": ["warm sand", "soft glow"],
        }
    )
    brief = _parse_brief(raw)
    assert isinstance(brief, ProductBrief)
    assert brief.name == "Argan Oil Hair Serum"


def test_parse_brief_strips_code_fences():
    raw = "```json\n" + json.dumps({"name": "X", "category": "c"}) + "\n```"
    brief = _parse_brief(raw)
    assert brief.name == "X"


def test_parse_brief_rejects_garbage():
    with pytest.raises(YunwuError):
        _parse_brief("definitely not json")


@pytest.mark.asyncio
async def test_analyzer_uses_yunwu_by_default(monkeypatch):
    fake = _FakeClient(
        json.dumps(
            {
                "name": "Coffee Mug",
                "category": "kitchenware",
                "materials": ["ceramic"],
                "target_user": "office workers",
                "primary_use": "drink coffee",
                "benefits": ["keeps drinks warm"],
                "visual_style_keywords": ["minimal", "warm"],
            }
        )
    )
    analyzer = ProductAnalyzer(client=fake)  # type: ignore[arg-type]
    brief = await analyzer.analyze(b"\x89PNG fake bytes", mime="image/png")
    assert brief.name == "Coffee Mug"
    assert "ceramic" in brief.materials
