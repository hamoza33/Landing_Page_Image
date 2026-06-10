"""Image-gen helpers: normalize, seam strip, and seam blend."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.image_gen import (
    _normalize_png,
    blend_top_into_previous,
    extract_bottom_strip,
)


def _png(w: int, h: int, color: tuple[int, int, int] = (180, 140, 90)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "dims",
    [(800, 800), (1024, 1024), (1024, 1536), (1536, 1024), (1024, 4096)],
)
def test_normalize_to_target(dims):
    src = _png(*dims)
    out = _normalize_png(src, target_w=1024, target_h=1536)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 1536)


def test_extract_bottom_strip_dimensions():
    src = _png(1024, 1536)
    strip = extract_bottom_strip(src, strip_height=256)
    with Image.open(io.BytesIO(strip)) as im:
        assert im.size == (1024, 256)


def test_blend_top_into_previous_changes_top_pixels():
    """Blending should leave the bottom alone but smooth the top into prev."""

    prev = _png(1024, 1536, color=(20, 200, 20))   # green
    cur = _png(1024, 1536, color=(220, 30, 30))    # red
    blended = blend_top_into_previous(cur, prev, blend_height=128)
    with Image.open(io.BytesIO(blended)) as im:
        # Top row should be ~prev color (green-ish).
        top_pixel = im.getpixel((512, 0))
        # Bottom row should be ~current color (red).
        bottom_pixel = im.getpixel((512, im.height - 1))
        assert top_pixel[1] > top_pixel[0], f"top pixel not green-leaning: {top_pixel}"
        assert bottom_pixel[0] > bottom_pixel[1], f"bottom pixel not red-leaning: {bottom_pixel}"


def test_blend_with_zero_height_is_noop():
    prev = _png(1024, 1536, color=(0, 200, 0))
    cur = _png(1024, 1536, color=(200, 0, 0))
    out = blend_top_into_previous(cur, prev, blend_height=0)
    assert out == cur
