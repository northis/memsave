"""FastAPI app: fast ingest, gallery API, link jobs, PWA and files."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, buildinfo, config, db, jobs, linkpreview, media, search, storage
from ..text import normalize_tag
from ..jobs import JobRunner

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
SAFE_SOURCES = {"paste", "upload", "share", "link", "extension"}


class LinkRequest(BaseModel):
    url: str = ""
    title: str | None = None
    text: str | None = None


class UploadMeta(BaseModel):
    source: str = "upload"
    page_url: str | None = None
    image_url: str | None = None
    filename: str | None = None
    title: str | None = None


def _template(name: str) -> str:
    path = STATIC_DIR / name
    html = path.read_text(encoding="utf-8")
    return html.replace("{{VERSION}}", __version__)


def _effective_host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if "," in host:
        host = host.split(",", 1)[0].strip()
    return host.split(":", 1)[0].lower()


def require_csrf(request: Request) -> None:
    if request.headers.get("x-memsave-request") != "1":
        raise HTTPException(status_code=403, detail="missing X-Memsave-Request header")
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return
    if origin.startswith(("chrome-extension://", "moz-extension://", "safari-web-extension://")):
        return
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https"):
        return
    host = (parsed.hostname or "").lower()
    if host and host != _effective_host(request):
        raise HTTPException(status_code=403, detail="foreign origin")


async def read_limited_body(request: Request, limit: int) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > limit:
                raise HTTPException(status_code=413, detail="file is too large")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _external_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value[:4000]
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


def _upload_meta(request: Request) -> UploadMeta:
    def header(name: str) -> str | None:
        value = request.headers.get(name)
        return unquote(value) if value else None

    source = (header("x-memsave-source") or "upload").lower()
    if source not in SAFE_SOURCES:
        source = "upload"
    title = (header("x-memsave-title") or "").strip()[:500] or None
    return UploadMeta(
        source=source,
        page_url=_external_url(header("x-memsave-page-url")),
        image_url=_external_url(header("x-memsave-image-url")),
        filename=header("x-memsave-filename"),
        title=title,
    )


def _image_dict(conn: sqlite3.Connection, row: sqlite3.Row, *, detail: bool = False) -> dict[str, Any]:
    return db.image_payload(conn, row, detail=detail)


def create_app(*, start_jobs: bool | None = None) -> FastAPI:
    jobs_enabled = config.JOBS_ENABLED if start_jobs is None else start_jobs

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        runner: JobRunner | None = None
        if jobs_enabled:
            runner = JobRunner()
            runner.start()
        app.state.runner = runner
        try:
            yield
        finally:
            if runner is not None:
                runner.stop()

    app = FastAPI(title="memsave", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def extension_cors(request: Request, call_next):
        origin = request.headers.get("origin") or ""
        is_extension = origin.startswith(
            ("chrome-extension://", "moz-extension://", "safari-web-extension://")
        )
        if is_extension and request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            response = await call_next(request)
        if is_extension:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, X-Memsave-Request, X-Memsave-Source, X-Memsave-Page-URL, "
                "X-Memsave-Image-URL, X-Memsave-Filename, X-Memsave-Title"
            )
            response.headers["Access-Control-Max-Age"] = "86400"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_template("index.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/share", response_class=HTMLResponse)
    def share_page() -> HTMLResponse:
        return HTMLResponse(_template("share.html"), headers={"Cache-Control": "no-store"})

    @app.post("/share")
    def share_fallback() -> RedirectResponse:
        return RedirectResponse("/share?error=sw", status_code=303)

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/favicon.ico")
    def favicon() -> RedirectResponse:
        return RedirectResponse("/static/favicon.svg", status_code=307)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/version")
    def version() -> dict[str, object]:
        return buildinfo.build_info()

    @app.get("/api/images")
    def list_images(
        q: str = "",
        tag: str | None = None,
        sort: Literal["new", "old"] = "new",
        offset: int = Query(0, ge=0),
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        with db.session() as conn:
            result = search.search_images(
                conn, query=q, tag=normalize_tag(tag) if tag else None, sort=sort, limit=limit, offset=offset
            )
            items = [_image_dict(conn, row) for row in result["rows"]]
            total = int(result["total"])
            meta = search.semantic_meta(conn) if q else {"available": None}
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sort": sort,
            "q": q,
            "tag": tag,
            "mode": result["mode"],
            "semantic_available": result["semantic_available"],
            "semantic": meta,
            "has_more": offset + len(items) < total,
        }

    @app.post("/api/images", dependencies=[Depends(require_csrf)])
    async def upload_image(request: Request) -> JSONResponse:
        data = await read_limited_body(request, config.MAX_UPLOAD_BYTES)
        if not data:
            raise HTTPException(status_code=400, detail="empty body")
        meta = _upload_meta(request)
        try:
            result = jobs.ingest_image(
                data,
                source=meta.source,
                source_url=meta.page_url,
                image_url=meta.image_url,
                title=meta.title or meta.filename,
            )
        except media.UnsupportedImage as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with db.session() as conn:
            row = db.get_image(conn, result.image_id)
            if row is None:
                raise HTTPException(status_code=500, detail="image row disappeared")
            payload = _image_dict(conn, row, detail=True)
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": "duplicate" if result.duplicate else "pending",
                "duplicate": result.duplicate,
                "image": payload,
            },
        )

    @app.get("/api/images/{image_id}")
    def get_image(image_id: int) -> dict[str, Any]:
        with db.session() as conn:
            row = db.get_image(conn, image_id)
            if row is None:
                raise HTTPException(status_code=404, detail="image not found")
            return _image_dict(conn, row, detail=True)

    @app.delete("/api/images/{image_id}", dependencies=[Depends(require_csrf)])
    def delete_image(image_id: int) -> dict[str, bool]:
        with db.session() as conn:
            row = db.delete_image(conn, image_id)
            if row is None:
                raise HTTPException(status_code=404, detail="image not found")
            storage.remove_image_files(row["display_name"], row["original_name"], row["thumb_name"])
        return {"deleted": True}

    @app.post("/api/images/{image_id}/retry", dependencies=[Depends(require_csrf)])
    def retry_image(image_id: int) -> dict[str, bool]:
        if not jobs.retry_image(image_id):
            raise HTTPException(status_code=404, detail="image not found")
        return {"queued": True}

    @app.post("/api/links", dependencies=[Depends(require_csrf)])
    def save_link(payload: LinkRequest) -> JSONResponse:
        url = (payload.url.strip() or (linkpreview.extract_first_url(payload.text or "") or ""))[:4000]
        if not url:
            raise HTTPException(status_code=400, detail="link is required")
        # SSRF validation and the actual fetch happen in the worker: accepting a
        # share must return immediately even when DNS for the target is slow.
        with db.session() as conn:
            job_id = db.enqueue_job(
                conn,
                "fetch_link",
                payload={"url": url, "title": payload.title},
            )
        return JSONResponse(status_code=202, content={"status": "queued", "job_id": job_id})

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: int) -> dict[str, Any]:
        with db.session() as conn:
            row = db.get_job(conn, job_id)
            if row is None:
                raise HTTPException(status_code=404, detail="job not found")
            result = db.job_payload(row)
            image_id = row["result_image_id"]
            if image_id:
                image = db.get_image(conn, int(image_id))
                if image is not None:
                    result["image"] = _image_dict(conn, image, detail=True)
            return result

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        with db.session() as conn:
            result = db.stats(conn)
            result["semantic"] = search.semantic_meta(conn)
            return result

    def _serve_file(directory: Path, name: str, *, content_type: str | None = None) -> FileResponse:
        if not storage.safe_name(name):
            raise HTTPException(status_code=404, detail="not found")
        extension = Path(name).suffix.lstrip(".").lower()
        if directory == config.IMAGES_DIR and extension not in config.MEDIA_EXTENSIONS:
            raise HTTPException(status_code=404, detail="not found")
        if directory == config.THUMBS_DIR and extension != "webp":
            raise HTTPException(status_code=404, detail="not found")
        path = directory / name
        try:
            path = path.resolve()
            path.relative_to(directory.resolve())
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="not found")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(
            path,
            media_type=content_type or config.media_type(extension),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/images/{name}")
    def image_file(name: str) -> FileResponse:
        return _serve_file(config.IMAGES_DIR, name)

    @app.get("/thumbs/{name}")
    def thumb_file(name: str) -> FileResponse:
        return _serve_file(config.THUMBS_DIR, name, content_type="image/webp")

    return app


app = create_app()
