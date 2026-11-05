"""File-system operations for originals, display copies and thumbnails."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import config


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


def image_path(name: str) -> Path:
    if not safe_name(name):
        raise ValueError("unsafe image name")
    return config.IMAGES_DIR / name


def thumb_path(name: str) -> Path:
    if not safe_name(name):
        raise ValueError("unsafe thumb name")
    return config.THUMBS_DIR / name


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(data)
    os.replace(temp, path)


def save_original(data: bytes, sha: str, extension: str) -> tuple[Path, str]:
    name = f"{sha}.{extension}"
    path = config.IMAGES_DIR / name
    if not path.exists():
        write_bytes_atomic(path, data)
    return path, name


def remove_image_files(display_name: str | None, original_name: str | None, thumb_name: str | None) -> None:
    for directory, name in (
        (config.IMAGES_DIR, display_name),
        (config.IMAGES_DIR, original_name),
        (config.THUMBS_DIR, thumb_name),
    ):
        if not name:
            continue
        try:
            path = directory / name
            if path.is_file():
                path.unlink()
        except OSError:
            pass
