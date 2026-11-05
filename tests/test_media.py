from __future__ import annotations

import io

import pytest
from PIL import Image

from memsave import config, media


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (20, 140, 80)).save(buffer, "JPEG")
    return buffer.getvalue()


def test_inspect_and_prepare_jpeg(paths):
    data = _jpeg()
    info = media.inspect_image(data)
    assert info.extension == "jpg"
    assert info.mime == "image/jpeg"
    assert info.width == 640
    assert info.height == 480

    src = config.IMAGES_DIR / "abc.jpg"
    src.write_bytes(data)
    prepared = media.prepare_image(src, "abc", "abc.jpg", "jpg")
    assert prepared.display_name == "abc.jpg"
    assert prepared.thumb_name == "abc.webp"
    assert (config.THUMBS_DIR / prepared.thumb_name).is_file()
    with Image.open(config.THUMBS_DIR / prepared.thumb_name) as thumb:
        assert max(thumb.size) <= 720


def test_inspect_rejects_svg(paths):
    with pytest.raises(media.UnsupportedImage):
        media.inspect_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")


def test_ai_derivative_is_small_jpeg(paths):
    src = config.IMAGES_DIR / "big.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 1200), (200, 100, 50)).save(buffer, "JPEG")
    src.write_bytes(buffer.getvalue())
    derivative, mime = media.ai_derivative(src, max_side=800)
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(derivative)) as image:
        assert max(image.size) <= 800
