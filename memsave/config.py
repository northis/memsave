"""Runtime configuration, read from the environment once at import time."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


_data_raw = Path(os.environ.get("MEMSAVE_DATA_DIR") or "data").expanduser()
DATA_DIR = (_data_raw if _data_raw.is_absolute() else BASE_DIR / _data_raw).resolve()
DB_PATH = DATA_DIR / "memsave.db"
IMAGES_DIR = DATA_DIR / "images"
THUMBS_DIR = DATA_DIR / "thumbs"
BACKUPS_DIR = DATA_DIR / "backups"
MODELS_DIR = DATA_DIR / "models"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _int("PORT", 8780)

# Vision recognition: OpenAI-compatible chat completions endpoint.
VISION_API_KEY = os.environ.get("MEMSAVE_VISION_API_KEY", "")
VISION_MODEL = os.environ.get("MEMSAVE_VISION_MODEL", "deepseek/deepseek-v4.1-flash")
VISION_BASE_URL = os.environ.get("MEMSAVE_VISION_BASE_URL", "https://openrouter.ai/api/v1")
VISION_TIMEOUT = _int("MEMSAVE_VISION_TIMEOUT", 60)
VISION_MAX_MB = _int("MEMSAVE_VISION_MAX_MB", 10)
VISION_RETRIES = _int("MEMSAVE_VISION_RETRIES", 5)

# Upload limits.
MAX_UPLOAD_MB = _int("MEMSAVE_MAX_UPLOAD_MB", 20)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_PIXELS = _int("MEMSAVE_MAX_PIXELS", 40_000_000)

# Link preview limits.
LINK_TIMEOUT = _int("MEMSAVE_LINK_TIMEOUT", 15)
LINK_MAX_HTML_BYTES = _int("MEMSAVE_LINK_MAX_HTML_MB", 3) * 1024 * 1024
LINK_MAX_REDIRECTS = _int("MEMSAVE_LINK_MAX_REDIRECTS", 5)
LINK_MAX_IMAGE_MB = _int("MEMSAVE_LINK_MAX_IMAGE_MB", 20)
LINK_ALLOWED_PORTS = {80, 443}
# X/Twitter fallback: the public fxtwitter API is used only when X's own
# endpoints do not return media. Disable with MEMSAVE_X_FALLBACK=0.
X_FALLBACK = _bool("MEMSAVE_X_FALLBACK", True)

# Background jobs.
JOBS_ENABLED = _bool("MEMSAVE_JOBS", True)
PREPARE_WORKERS = _int("MEMSAVE_PREPARE_WORKERS", 2)
RECOGNIZE_WORKERS = _int("MEMSAVE_RECOGNIZE_WORKERS", 1)
EMBED_WORKERS = _int("MEMSAVE_EMBED_WORKERS", 1)
LINK_WORKERS = _int("MEMSAVE_LINK_WORKERS", 1)
JOB_POLL_SECONDS = float(os.environ.get("MEMSAVE_JOB_POLL_SECONDS", "1.0"))

# Semantic search.
EMBEDDING_MODEL = os.environ.get(
    "MEMSAVE_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBEDDING_DIM = _int("MEMSAVE_EMBEDDING_DIM", 384)
EMBEDDING_VERSION = _int("MEMSAVE_EMBEDDING_VERSION", 1)
SEMANTIC_CANDIDATES = _int("MEMSAVE_SEMANTIC_CANDIDATES", 400)
SEMANTIC_MIN_SCORE = float(os.environ.get("MEMSAVE_SEMANTIC_MIN_SCORE", "0.18"))
SEMANTIC_RELATIVE_SCORE = float(os.environ.get("MEMSAVE_SEMANTIC_RELATIVE_SCORE", "0.65"))
RRF_K = _int("MEMSAVE_RRF_K", 60)

# AI answer limits.
MAX_TEXT = _int("MEMSAVE_MAX_TEXT", 6000)
MAX_TAGS = _int("MEMSAVE_MAX_TAGS", 6)
MAX_TAG_LEN = 48
MAX_DESCRIPTION = 600

CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "heic": "image/heic",
    "heif": "image/heif",
    "avif": "image/avif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

MEDIA_EXTENSIONS = set(CONTENT_TYPES)


def ensure_dirs() -> None:
    """Create every directory the service writes to."""
    for directory in (DATA_DIR, IMAGES_DIR, THUMBS_DIR, BACKUPS_DIR, MODELS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def media_type(extension: str) -> str:
    return CONTENT_TYPES.get(extension.lower().lstrip("."), "application/octet-stream")
