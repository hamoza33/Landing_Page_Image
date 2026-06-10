"""Stitcher tests — feed 8 dummy colored images, assert size and seam blend."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.config import Settings
from app.services.stitcher import SEAM_PX, Stitcher


def _solid_png(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def small_settings(tmp_path, monkeypatch):
    """Override settings to keep the test cheap (smaller images)."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("IMAGE_WIDTH", "64")
    monkeypatch.setenv("SECTION_HEIGHT", "192")  # 1:3 ratio
    return Settings.load()


def test_stitch_produces_correct_dimensions(tmp_path, small_settings):
    colors = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (0, 255, 255), (255, 0, 255), (128, 128, 128), (40, 40, 40),
    ]
    imgs = [_solid_png(64, 192, c) for c in colors]

    stitcher = Stitcher(small_settings)
    out_path = tmp_path / "long.png"
    png = stitcher.stitch(imgs, output_path=out_path)

    with Image.open(io.BytesIO(png)) as result:
        assert result.size == (64, 192 * 8)
    assert out_path.exists()


def test_seam_is_blended_not_hard(small_settings):
    """At a seam, the middle pixel should be a mix of the two adjacent colors,
    not equal to either solid color."""
    red = _solid_png(64, 192, (255, 0, 0))
    blue = _solid_png(64, 192, (0, 0, 255))
    imgs = [red, blue, red, blue, red, blue, red, blue]

    stitcher = Stitcher(small_settings)
    png = stitcher.stitch(imgs)

    with Image.open(io.BytesIO(png)) as result:
        # Seam between section 0 (red) and section 1 (blue) is centered at y=192.
        join_y = 192
        # Middle of the seam should be roughly half-and-half.
        mid_pixel = result.getpixel((32, join_y))
        r, g, b = mid_pixel
        assert r < 240, f"top red bleed too strong at seam: {mid_pixel}"
        assert b > 15, f"bottom blue not present at seam: {mid_pixel}"

        # Outside the seam zone, the section should retain its original color.
        deep_red = result.getpixel((32, 50))
        assert deep_red[0] > 200 and deep_red[2] < 50

        deep_blue = result.getpixel((32, 192 + SEAM_PX + 10))
        assert deep_blue[2] > 200 and deep_blue[0] < 50
