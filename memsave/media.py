"""Image validation, HEIC/legacy conversion and thumbnails."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

try:  # HEIC/HEIF support is optional at import time, but pillow-heif is a runtime dep.
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - the dependency is present in production
    pillow_heif = None  # type: ignore[assignment]

Image.MAX_IMAGE_PIXELS = config.MAX_PIXELS

EXTENSIONS = {
    "JPEG": ("jpg", "image/jpeg", False),
    "MPO": ("jpg", "image/jpeg", False),
    "PNG": ("png", "image/png", False),
    "WEBP": ("webp", "image/webp", False),
    "GIF": ("gif", "image/gif", False),
    "HEIF": ("heic", "image/heic", True),
    "HEIC": ("heic", "image/heic", True),
    "AVIF": ("avif", "image/avif", False),
    "BMP": ("bmp", "image/bmp", True),
    "TIFF": ("tif", "image/tiff", True),
}

CONVERT_TO_JPEG = {"heic", "heif", "bmp", "tif", "tiff"}


class UnsupportedImage(ValueError):
    """The upload is not a supported bitmap image."""


@dataclass(frozen=True)
class ImageInfo:
    extension: str
    mime: str
    width: int
    height: int
    animated: bool
    needs_conversion: bool


@dataclass(frozen=True)
class Prepared:
    display_name: str
    original_name: str | None
    thumb_name: str
    extension: str
    mime: str
    width: int
    height: int


def _open_header(data: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise UnsupportedImage("not a supported image") from exc


def inspect_image(data: bytes) -> ImageInfo:
    image = _open_header(data)
    try:
        fmt = (image.format or "").upper()
        if fmt not in EXTENSIONS:
            raise UnsupportedImage(f"unsupported format: {fmt or 'unknown'}")
        extension, mime, needs_conversion = EXTENSIONS[fmt]
        if image.width * image.height > config.MAX_PIXELS:
            raise UnsupportedImage("image is too large in pixels")
        try:
            image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise UnsupportedImage("image could not be decoded") from exc
        animated = bool(getattr(image, "is_animated", False))
        return ImageInfo(
            extension=extension,
            mime=mime,
            width=int(image.width),
            height=int(image.height),
            animated=animated,
            needs_conversion=needs_conversion,
        )
    finally:
        image.close()


def _flatten(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode not in ("RGB", "L"):
        return image.convert("RGB")
    return image


def _first_frame(image: Image.Image) -> Image.Image:
    if getattr(image, "is_animated", False):
        image.seek(0)
    return image.copy()


def prepare_image(src: Path, sha: str, original_name: str, original_extension: str) -> Prepared:
    """Convert an image for web display and build its thumbnail.

    The original file stays on disk. HEIC/HEIF/BMP/TIFF get a JPEG display copy;
    everything browser-readable is served under the original extension.
    """
    with Image.open(src) as raw:
        raw.load()
        frame = _first_frame(raw)
        try:
            frame = ImageOps.exif_transpose(frame)
        except Exception:
            pass
        width, height = frame.size
        if original_extension in CONVERT_TO_JPEG:
            display_ext = "jpg"
            display_mime = "image/jpeg"
            display_name = f"{sha}.jpg"
            display_path = config.IMAGES_DIR / display_name
            if display_path != src:
                converted = _flatten(frame)
                converted.save(display_path, "JPEG", quality=92, optimize=True)
            original_name = src.name
        else:
            display_ext = original_extension
            display_mime = config.media_type(original_extension)
            display_name = original_name
            display_path = src

        thumb = _flatten(frame if original_extension not in ("gif",) else _first_frame(raw))
        if thumb.mode not in ("RGB", "RGBA"):
            thumb = thumb.convert("RGB")
        thumb.thumbnail((720, 720), Image.Resampling.LANCZOS)
        thumb_name = f"{sha}.webp"
        thumb.save(config.THUMBS_DIR / thumb_name, "WEBP", quality=82, method=6)

    return Prepared(
        display_name=display_name,
        original_name=src.name if display_name != src.name else None,
        thumb_name=thumb_name,
        extension=display_ext,
        mime=display_mime,
        width=int(width),
        height=int(height),
    )


def ai_derivative(src: Path, *, max_side: int = 1536) -> tuple[bytes, str]:
    """A small JPEG used for the vision request; never stored permanently."""
    with Image.open(src) as raw:
        raw.load()
        frame = _first_frame(raw)
        try:
            frame = ImageOps.exif_transpose(frame)
        except Exception:
            pass
        frame = _flatten(frame)
        frame.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        frame.save(buffer, "JPEG", quality=85, optimize=True)
        return buffer.getvalue(), "image/jpeg"


def image_dimensions(src: Path) -> tuple[int, int]:
    with Image.open(src) as image:
        return int(image.width), int(image.height)
