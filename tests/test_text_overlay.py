"""Text overlay smoke tests."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.config import Settings
from app.schemas import LandingCopy, SECTION_KEYS
from app.services.text_overlay import TextOverlay, _shape_ar


def _png(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (200, 180, 150))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sample_copy() -> LandingCopy:
    data = {
        "hero": {"headline": "تجربة لا تُنسى", "subhead": "الفرق من اللمسة الأولى", "cta": "اطلب الآن"},
        "features": {
            "headline": "مزايا مميزة",
            "items": [
                {"title": "خامة", "description": "أجود المواد"},
                {"title": "تصميم", "description": "أنيق"},
                {"title": "سهل", "description": "بسيط ومريح"},
                {"title": "ضمان", "description": "نقف خلف منتجنا"},
            ],
        },
        "before_after": {"headline": "قبل وبعد", "before": "روتين عادي", "after": "نتائج سريعة"},
        "testimonials": {
            "headline": "آراء عملائنا",
            "items": [
                {"name": "سارة", "location": "الرياض", "quote": "رائع"},
                {"name": "أحمد", "location": "دبي", "quote": "ممتاز"},
                {"name": "نورة", "location": "الكويت", "quote": "أحببته"},
            ],
        },
        "faq": {
            "headline": "أسئلة شائعة",
            "items": [
                {"question": "كم مدة التوصيل؟", "answer": "2-5 أيام"},
                {"question": "هل هناك ضمان؟", "answer": "نعم"},
                {"question": "كيف أعتني به؟", "answer": "حسب التعليمات"},
                {"question": "استرجاع؟", "answer": "خلال 14 يومًا"},
            ],
        },
        "lifestyle": {"headline": "أسلوب حياة", "body": "صُمم ليكون جزءًا من يومك"},
        "education": {"headline": "كيف يعمل", "body": "خطوات الاستخدام البسيطة"},
        "closing": {"headline": "ابدأ اليوم", "body": "كل لحظة انتظار", "cta": "احصل عليه"},
    }
    return LandingCopy.model_validate(data)


def test_shape_ar_handles_empty():
    assert _shape_ar("") == ""


def test_shape_ar_changes_letters():
    """Reshaped Arabic differs from raw input (letters get joined)."""

    raw = "الفرق"
    shaped = _shape_ar(raw)
    assert shaped != raw  # presentation forms differ from base letters


@pytest.mark.parametrize("section_key", SECTION_KEYS)
def test_overlay_apply_returns_valid_png(monkeypatch, section_key):
    """Every section can be overlaid without raising and returns a valid PNG."""

    monkeypatch.setenv("OVERLAY_TEXT_ENABLED", "true")
    settings = Settings.load()
    overlay = TextOverlay(settings=settings)

    src = _png(settings.image_width, settings.image_height)
    out = overlay.apply(section_key=section_key, png_bytes=src, copy=_sample_copy())

    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (settings.image_width, settings.image_height)
        assert im.format == "PNG"


def test_overlay_disabled_is_passthrough(monkeypatch):
    monkeypatch.setenv("OVERLAY_TEXT_ENABLED", "false")
    settings = Settings.load()
    overlay = TextOverlay(settings=settings)

    src = _png(settings.image_width, settings.image_height)
    out = overlay.apply(section_key="hero", png_bytes=src, copy=_sample_copy())
    assert out == src
