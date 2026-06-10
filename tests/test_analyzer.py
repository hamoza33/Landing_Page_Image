"""Researcher tests — ensure JSON parsing is tolerant and brief validates."""

from __future__ import annotations

import json

import pytest

from app.schemas import ProductBrief
from app.services.researcher import ProductResearcher, _coerce_json
from app.services.analyzer import ProductAnalyzer
from app.services.yunwu_client import YunwuError


class _ScriptedClient:
    """Returns a pre-baked sequence of responses across calls."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls = 0

    async def respond(self, **_kwargs) -> str:  # noqa: ANN001, D401
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def test_coerce_json_accepts_clean():
    assert _coerce_json('{"a":1}') == {"a": 1}


def test_coerce_json_strips_fences():
    assert _coerce_json('```json\n{"a":1}\n```') == {"a": 1}


def test_coerce_json_extracts_inline():
    assert _coerce_json('blah {"a":2} trailing') == {"a": 2}


def test_coerce_json_rejects_garbage():
    with pytest.raises(YunwuError):
        _coerce_json("definitely not json")


@pytest.mark.asyncio
async def test_researcher_two_call_flow(monkeypatch):
    """Vision call then consolidate call produce a valid ProductBrief."""

    vision_response = json.dumps(
        {
            "name": "Argan Oil Serum",
            "brand": "Some Brand",
            "category": "haircare",
            "sub_category": "hair serum",
            "country_of_origin": "Morocco",
            "visible_claims": ["100% natural"],
            "visible_text": ["ARGAN"],
            "ingredients_visible": ["argan oil"],
            "search_keywords": ["argan oil hair serum"],
            "palette_hex": ["#c8a567", "#f5ecdc"],
            "visual_style_keywords": ["warm", "premium"],
        }
    )
    consolidated_response = json.dumps(
        {
            "name": "Argan Oil Serum",
            "brand": "Some Brand",
            "category": "haircare",
            "sub_category": "hair serum",
            "country_of_origin": "Morocco",
            "materials": ["argan oil", "vitamin E"],
            "ingredients": ["argan oil", "vitamin E"],
            "target_user": "GCC women 25-45",
            "primary_use": "smooth, shiny hair",
            "primary_problem_solved": "frizz",
            "benefits": ["frizz control", "shine"],
            "unique_selling_points": ["cold-pressed", "no parabens"],
            "competitive_angles": ["premium for less"],
            "web_research_summary": "Popular natural haircare ingredient...",
            "web_research_sources": [],
            "visual_style_keywords": ["warm", "premium"],
            "palette_hex": ["#c8a567", "#f5ecdc"],
        }
    )
    fake = _ScriptedClient(vision_response, consolidated_response)

    # Disable Tavily so only the two LLM calls are made.
    monkeypatch.setenv("WEB_RESEARCH_ENABLED", "false")
    from app.config import Settings

    settings = Settings.load()
    researcher = ProductResearcher(client=fake, settings=settings)  # type: ignore[arg-type]

    brief = await researcher.research(b"\x89PNG fake", mime="image/png")
    assert isinstance(brief, ProductBrief)
    assert brief.name == "Argan Oil Serum"
    assert brief.brand == "Some Brand"
    assert "argan oil" in brief.ingredients
    assert fake.calls == 2  # vision + consolidate, no web search


@pytest.mark.asyncio
async def test_analyzer_facade_delegates(monkeypatch):
    """The legacy ProductAnalyzer name still works and uses the researcher."""

    monkeypatch.setenv("WEB_RESEARCH_ENABLED", "false")
    from app.config import Settings

    settings = Settings.load()

    vision_response = json.dumps(
        {
            "name": "Coffee Mug",
            "brand": "",
            "category": "kitchenware",
            "sub_category": "ceramic mug",
            "country_of_origin": "",
            "visible_claims": [],
            "visible_text": [],
            "ingredients_visible": [],
            "search_keywords": ["ceramic coffee mug"],
            "palette_hex": [],
            "visual_style_keywords": ["minimal"],
        }
    )
    consolidated = json.dumps(
        {
            "name": "Coffee Mug",
            "brand": "",
            "category": "kitchenware",
            "sub_category": "ceramic mug",
            "country_of_origin": "",
            "materials": ["ceramic"],
            "ingredients": [],
            "target_user": "office workers",
            "primary_use": "drink coffee",
            "primary_problem_solved": "boring mugs",
            "benefits": ["keeps drinks warm"],
            "unique_selling_points": [],
            "competitive_angles": [],
            "web_research_summary": "",
            "web_research_sources": [],
            "visual_style_keywords": ["minimal"],
            "palette_hex": [],
        }
    )
    fake = _ScriptedClient(vision_response, consolidated)
    analyzer = ProductAnalyzer(client=fake, settings=settings)  # type: ignore[arg-type]
    brief = await analyzer.analyze(b"\x89PNG fake", mime="image/png")
    assert brief.name == "Coffee Mug"
    assert "ceramic" in brief.materials
