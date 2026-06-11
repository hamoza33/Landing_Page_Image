"""Ensure every section prompt round-trips cleanly through json.dumps and
contains explicit Arabic-text-rendering instructions.

Catches regressions where a template gets a stray double quote, em-dash,
curly quote, or backslash that would break naive JSON parsers.
"""

from __future__ import annotations

import json

import pytest

from app.schemas import (
    BeforeAfterCopy,
    ClosingCopy,
    EducationCopy,
    FaqCopy,
    FaqItem,
    FeatureItem,
    FeaturesCopy,
    HeroCopy,
    LandingCopy,
    LifestyleCopy,
    ProductBrief,
    SECTION_KEYS,
    Testimonial as _Testimonial,
    TestimonialsCopy as _TestimonialsCopy,
)
from app.services.image_gen import ImageGenerator, _jsafe

# Re-export under non-test-prefixed names so pytest doesn't try to collect
# the pydantic models as test classes.
Quote = _Testimonial
QuoteSection = _TestimonialsCopy


# Stress-test brief deliberately filled with characters the prompt must
# sanitize: double quotes, em-dash, curly quotes, ellipsis, backslash.
@pytest.fixture
def brief() -> ProductBrief:
    return ProductBrief(
        name='كريم "الدوالي" بالأعشاب',
        brand="ShopInzo \u2014 Premium",  # em dash
        category="عناية \u201cبالساقين\u201d",  # curly quotes
        sub_category="كريم \\ موضعي",  # backslash
        target_user="نساء 25-55",
        primary_use="بعد يوم وقوف طويل\u2026",  # ellipsis
        ingredients=["كستناء الحصان", "زيت اللوز"],
        visual_style_keywords=["editorial", "premium spa"],
        palette_hex=["#E9DCC4", "#C9A56B"],
    )


@pytest.fixture
def copy() -> LandingCopy:
    return LandingCopy(
        hero=HeroCopy(
            headline='ساقاكِ "تستحقّان" الراحة',
            subhead="كريم عشبي يخفّف الدوالي.",
            cta="اطلبي الآن",
        ),
        features=FeaturesCopy(
            headline="ثلاث ميزات تصنع الفرق",
            items=[
                FeatureItem(title="تركيبة عشبية", description="كستناء + زيت اللوز."),
                FeatureItem(title="امتصاص سريع", description="ملمس خفيف."),
            ],
        ),
        before_after=BeforeAfterCopy(
            headline="فرق ملحوظ",
            before="ثقل وأوردة بارزة",
            after="ساقان أخفّ",
        ),
        testimonials=QuoteSection(
            headline="آراء العملاء",
            items=[
                Quote(name="ندى", location="الرياض", quote="فرق من أول أسبوع."),
            ],
        ),
        faq=FaqCopy(
            headline="أسئلة شائعة",
            items=[
                FaqItem(question="هل التركيبة طبيعية؟", answer="نعم، 100%."),
            ],
        ),
        lifestyle=LifestyleCopy(headline="لحظتك", body="استرخي."),
        education=EducationCopy(headline="كيف يعمل؟", body="يحفّز الدورة."),
        closing=ClosingCopy(headline="ابدئي اليوم", body="عرض خاص.", cta="اطلبي الآن"),
    )


@pytest.mark.parametrize("section_index,key", list(enumerate(SECTION_KEYS)))
def test_prompt_is_json_safe_and_requests_arabic(
    section_index: int,
    key: str,
    brief: ProductBrief,
    copy: LandingCopy,
):
    gen = ImageGenerator()
    prompt = gen.build_prompt(
        key=key,
        index=section_index,
        brief=brief,
        copy=copy,
        seamless_top=section_index > 0,
    )

    # 1. No characters that commonly trip naive JSON parsers in the wild.
    forbidden = ['"', "\u2014", "\u2013", "\u201c", "\u201d", "\u2018", "\u2019", "\u2026", "\\", "\r", "\t"]
    for ch in forbidden:
        assert ch not in prompt, f"{key}: prompt contains forbidden char {ch!r}"

    # 2. JSON round-trip preserves it.
    payload = {
        "model": "gpt-image-2-all",
        "size": "1024x1536",
        "n": 1,
        "prompt": prompt,
        "image": ["https://landing.shopinzo.bond/files/x/upload.png"],
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    assert json.loads(encoded)["prompt"] == prompt

    # 3. Prompt explicitly asks the model to render Arabic text.
    lower = prompt.lower()
    assert "arabic" in lower
    assert "render" in lower or "must render" in lower

    # 4. The seamless-top instruction switches correctly.
    if section_index == 0:
        assert "section 1" in lower
    else:
        assert "reference image #2" in lower


def test_jsafe_replaces_known_problem_chars():
    src = 'a "quoted" \u2014 \u201cb\u201d \\\\ \u2026'
    safe = _jsafe(src)
    for ch in ['"', "\u2014", "\u201c", "\u201d", "\\", "\u2026"]:
        assert ch not in safe, f"jsafe leaked {ch!r}"


def test_jsafe_keeps_arabic_and_newlines():
    src = "ساقاكِ\nتستحقّان"
    assert _jsafe(src) == "ساقاكِ\nتستحقّان"


def test_jsafe_handles_none_and_int():
    assert _jsafe(None) == ""
    assert _jsafe(3) == "3"
