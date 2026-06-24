"""Image generator tests - verify normalization, crop_bottom, and sequential flow."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.services.image_gen import (
    GeneratedSection,
    ImageGenerator,
    PRODUCT_REF_SECTIONS,
    _crop_bottom,
    _normalize_to_size,
)
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
    Testimonial,
    TestimonialsCopy,
)


def _png(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (180, 140, 90))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- normalize tests


def test_normalize_pads_when_too_short():
    src = _png(1024, 1536)  # 2:3, half the target height
    out = _normalize_to_size(src, target_w=1024, target_h=3072)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 3072)


def test_normalize_crops_when_too_tall():
    src = _png(1024, 4096)
    out = _normalize_to_size(src, target_w=1024, target_h=3072)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 3072)


def test_normalize_resizes_width_first():
    src = _png(2048, 6144)  # already 1:3 but bigger
    out = _normalize_to_size(src, target_w=1024, target_h=3072)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 3072)


@pytest.mark.parametrize("dims", [(800, 800), (1024, 1024), (3000, 1000)])
def test_normalize_handles_arbitrary_dims(dims):
    src = _png(*dims)
    out = _normalize_to_size(src, target_w=1024, target_h=3072)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 3072)


# ---------------------------------------------------------------- crop_bottom tests


def test_crop_bottom_returns_correct_height():
    src = _png(1024, 3072)
    cropped = _crop_bottom(src, height=200)
    with Image.open(io.BytesIO(cropped)) as im:
        assert im.size == (1024, 200)


def test_crop_bottom_clamps_to_image_height():
    src = _png(100, 50)
    cropped = _crop_bottom(src, height=200)
    with Image.open(io.BytesIO(cropped)) as im:
        # Should crop the full image since height < requested crop
        assert im.size == (100, 50)


def test_crop_bottom_default_height():
    src = _png(512, 1024)
    cropped = _crop_bottom(src)
    with Image.open(io.BytesIO(cropped)) as im:
        assert im.size == (512, 200)


# ---------------------------------------------------------------- sequential flow tests


def _make_brief() -> ProductBrief:
    return ProductBrief(
        name="Test Product",
        category="Skincare",
        materials=["organic oils"],
        target_user="Women 25-40",
        primary_use="Daily moisturizer",
        benefits=["hydration", "anti-aging"],
        visual_style_keywords=["warm", "golden", "luxurious"],
    )


def _make_copy() -> LandingCopy:
    return LandingCopy(
        hero=HeroCopy(headline="عنوان رئيسي", subhead="عنوان فرعي", cta="اشتري الآن"),
        features=FeaturesCopy(
            headline="المميزات",
            items=[FeatureItem(title="ترطيب", description="ترطيب عميق للبشرة")],
        ),
        before_after=BeforeAfterCopy(
            headline="قبل وبعد", before="بشرة جافة", after="بشرة مشرقة"
        ),
        testimonials=TestimonialsCopy(
            headline="آراء العملاء",
            items=[Testimonial(name="سارة", location="الرياض", quote="منتج رائع")],
        ),
        faq=FaqCopy(
            headline="أسئلة شائعة",
            items=[FaqItem(question="كيف أستخدمه؟", answer="يومياً صباحاً ومساءً")],
        ),
        lifestyle=LifestyleCopy(headline="أسلوب الحياة", body="جزء من روتينك اليومي"),
        education=EducationCopy(headline="كيف يعمل", body="مكونات طبيعية فعالة"),
        closing=ClosingCopy(headline="ابدأ الآن", body="لا تفوت الفرصة", cta="اطلب الآن"),
    )


@pytest.mark.asyncio
async def test_generate_all_sequential_calls_image_edit():
    """Verify sequential generation calls image_edit with reference images."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])
    mock_client.image = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    brief = _make_brief()
    copy = _make_copy()

    results = await gen.generate_all(brief, copy, product_image=product_image)

    assert len(results) == 8
    # Should have called image_edit for all 8 sections (since product_image is provided)
    assert mock_client.image_edit.call_count == 8
    # Should NOT have called the plain image method
    assert mock_client.image.call_count == 0


@pytest.mark.asyncio
async def test_generate_all_sequential_order():
    """Verify sections are generated in the correct order."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    results = await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    keys_in_order = [r.key for r in results]
    assert keys_in_order == list(SECTION_KEYS)
    assert [r.index for r in results] == list(range(8))


@pytest.mark.asyncio
async def test_generate_all_hero_gets_product_only():
    """Hero section should only receive product image as reference (no prev crop)."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    # First call is hero - should have exactly 1 reference image (product)
    first_call = mock_client.image_edit.call_args_list[0]
    refs = first_call.kwargs["reference_images"]
    assert len(refs) == 1
    assert refs[0] == product_image


@pytest.mark.asyncio
async def test_generate_all_subsequent_product_sections_get_two_refs():
    """Sections with product ref after hero should get [product, prev_crop]."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    # features is index 1 and in PRODUCT_REF_SECTIONS
    features_call = mock_client.image_edit.call_args_list[1]
    refs = features_call.kwargs["reference_images"]
    assert len(refs) == 2
    assert refs[0] == product_image
    # Second ref is the bottom crop of hero


@pytest.mark.asyncio
async def test_generate_all_non_product_sections_get_one_ref():
    """Sections without product ref should get only [prev_crop]."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    # testimonials is index 3, NOT in PRODUCT_REF_SECTIONS
    testimonials_call = mock_client.image_edit.call_args_list[3]
    refs = testimonials_call.kwargs["reference_images"]
    assert len(refs) == 1
    # The single ref is the prev_bottom_crop (not the product)
    assert refs[0] != product_image


@pytest.mark.asyncio
async def test_generate_all_prompts_contain_arabic_text():
    """All prompts should contain Arabic text from the copy."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    results = await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    # Hero prompt should contain the Arabic headline
    assert "عنوان رئيسي" in results[0].prompt
    # Features prompt should contain the features headline
    assert "المميزات" in results[1].prompt
    # Closing prompt should contain the CTA
    assert "اطلب الآن" in results[7].prompt


@pytest.mark.asyncio
async def test_generate_all_prompts_no_double_quotes():
    """Prompts must not contain double quotes."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    gen = ImageGenerator(client=mock_client)
    results = await gen.generate_all(_make_brief(), _make_copy(), product_image=product_image)

    for section in results:
        assert '"' not in section.prompt, (
            f"Double quote found in {section.key} prompt: {section.prompt[:200]}"
        )


@pytest.mark.asyncio
async def test_generate_all_progress_callback():
    """Progress callback should be called for each section."""
    fake_image = _png(1024, 3072)
    product_image = _png(500, 500)

    mock_client = AsyncMock()
    mock_client.image_edit = AsyncMock(return_value=[fake_image])

    progress_calls: list[str] = []

    async def mock_progress(msg: str) -> None:
        progress_calls.append(msg)

    gen = ImageGenerator(client=mock_client)
    await gen.generate_all(
        _make_brief(), _make_copy(), product_image=product_image, progress=mock_progress
    )

    assert len(progress_calls) == 8
    assert "generating section 1 of 8: hero" in progress_calls[0]
    assert "generating section 8 of 8: closing" in progress_calls[7]
