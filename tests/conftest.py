from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from memsave import config, db, search
from memsave.server.app import create_app


@pytest.fixture
def paths(tmp_path, monkeypatch):
    data = tmp_path
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "memsave.db")
    monkeypatch.setattr(config, "IMAGES_DIR", data / "images")
    monkeypatch.setattr(config, "THUMBS_DIR", data / "thumbs")
    monkeypatch.setattr(config, "BACKUPS_DIR", data / "backups")
    monkeypatch.setattr(config, "MODELS_DIR", data / "models")
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(search, "_embedder", None)
    config.ensure_dirs()
    db.init_db()
    yield data
    search._embedder = None


@pytest.fixture
def client(paths):
    app = create_app(start_jobs=False)
    with TestClient(app) as test_client:
        yield test_client


def make_image(
    size: tuple[int, int] = (320, 240),
    color: tuple[int, int, int] = (90, 180, 100),
    fmt: str = "JPEG",
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def jpeg_bytes():
    return make_image()
