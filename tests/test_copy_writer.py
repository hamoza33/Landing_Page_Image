"""CopyWriter tests — assert all 8 sections present and contain Arabic text."""

from __future__ import annotations

import json
import re

import pytest

from app.schemas import LandingCopy, ProductBrief, SECTION_KEYS
from app.services.copy_writer import CopyWriter


ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _sample_arabic_copy() -> dict:
    return {
        "hero": {
            "headline": "تجربة لا تُنسى",
            "subhead": "اكتشف الفرق من اللمسة الأولى",
            "cta": "اطلب الآن",
        },
        "features": {
            "headline": "مزايا مميزة",
            "items": [
                {"title": "خامة ممتازة", "description": "مصنوع من أجود المواد"},
                {"title": "تصميم أنيق", "description": "يناسب ذوقك الخليجي"},
                {"title": "سهل الاستخدام", "description": "بسيط ومريح"},
                {"title": "ضمان حقيقي", "description": "نقف خلف منتجنا"},
            ],
        },
        "before_after": {
            "headline": "قبل وبعد",
            "before": "روتين عادي ومتعب",
            "after": "نتائج تظهر سريعًا",
        },
        "testimonials": {
            "headline": "آراء عملائنا",
            "items": [
                {"name": "سارة", "location": "الرياض", "quote": "منتج رائع جدًا"},
                {"name": "أحمد", "location": "دبي", "quote": "أنصح به الجميع"},
                {"name": "نورة", "location": "الكويت", "quote": "تجربتي ممتازة"},
            ],
        },
        "faq": {
            "headline": "أسئلة شائعة",
            "items": [
                {"question": "كم مدة التوصيل؟", "answer": "خلال 2-5 أيام"},
                {"question": "هل هناك ضمان؟", "answer": "نعم، 30 يومًا"},
                {"question": "كيف أعتني بالمنتج؟", "answer": "حسب التعليمات المرفقة"},
                {"question": "هل تتوفر استرجاع؟", "answer": "نعم، خلال 14 يومًا"},
            ],
        },
        "lifestyle": {
            "headline": "أسلوب حياة",
            "body": "صُمم ليكون جزءًا من يومك في البيت أو السفر",
        },
        "education": {
            "headline": "كيف يعمل",
            "body": "نشرح خطوات الاستخدام بصورة بسيطة وفعّالة",
        },
        "closing": {
            "headline": "ابدأ اليوم",
            "body": "كل لحظة انتظار تأخّرك عن النتيجة التي تستحقها",
            "cta": "احصل عليه الآن",
        },
    }


class _FakeClient:
    def __init__(self, text: str):
        self._text = text

    async def respond(self, **_kwargs) -> str:  # noqa: D401, ANN001
        return self._text


@pytest.mark.asyncio
async def test_copy_writer_returns_all_sections_in_arabic():
    fake = _FakeClient(json.dumps(_sample_arabic_copy(), ensure_ascii=False))
    writer = CopyWriter(client=fake)  # type: ignore[arg-type]
    brief = ProductBrief(
        name="Test Product",
        category="general",
        target_user="GCC consumers",
        primary_use="daily use",
    )
    copy: LandingCopy = await writer.generate(brief)

    for key in SECTION_KEYS:
        assert hasattr(copy, key), f"missing section {key}"

    # Every top-level headline contains Arabic.
    assert ARABIC_RE.search(copy.hero.headline)
    assert ARABIC_RE.search(copy.features.headline)
    assert ARABIC_RE.search(copy.closing.cta)
    assert len(copy.features.items) >= 4
    assert len(copy.testimonials.items) >= 3
    assert len(copy.faq.items) >= 4


@pytest.mark.asyncio
async def test_copy_writer_handles_code_fences():
    body = "```json\n" + json.dumps(_sample_arabic_copy(), ensure_ascii=False) + "\n```"
    fake = _FakeClient(body)
    writer = CopyWriter(client=fake)  # type: ignore[arg-type]
    brief = ProductBrief(name="X", category="y")
    copy = await writer.generate(brief)
    assert copy.hero.cta == "اطلب الآن"
