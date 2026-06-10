"""Image generator tests — verify normalization to target dimensions."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.image_gen import _normalize_to_size


def _png(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (180, 140, 90))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
