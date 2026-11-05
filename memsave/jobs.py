"""Durable background jobs: prepare, recognize, embed and link resolution."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import ai, config, db, linkpreview, media, search, storage

logger = logging.getLogger(__name__)


class JobRetry(RuntimeError):
    pass


class JobPermanent(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestResult:
    image_id: int
    duplicate: bool
    row: sqlite3.Row


def ingest_image(
    data: bytes,
    *,
    source: str,
    source_url: str | None = None,
    image_url: str | None = None,
    title: str | None = None,
) -> IngestResult:
    """Store bytes, deduplicate by SHA and enqueue preparation.

    The caller has already validated the bytes where needed; this function validates
    again so the link worker and the HTTP endpoint share one path.
    """
    config.ensure_dirs()
    info = media.inspect_image(data)
    sha = storage.sha256_bytes(data)
    with db.session() as conn:
        existing = db.get_image_by_sha(conn, sha)
        if existing is not None:
            return IngestResult(int(existing["id"]), True, existing)

        path, original_name = storage.save_original(data, sha, info.extension)
        try:
            image_id = db.insert_image(
                conn,
                sha256=sha,
                display_name=original_name,
                original_name=None,
                mime=info.mime,
                extension=info.extension,
                width=info.width,
                height=info.height,
                size_bytes=len(data),
                source=source[:32],
                source_url=source_url,
                image_url=image_url,
                title=(title or "")[:500] or None,
            )
            db.enqueue_job(conn, "prepare", image_id=image_id, dedupe_key=f"image:{image_id}")
            row = db.get_image(conn, image_id)
            assert row is not None
            return IngestResult(image_id, False, row)
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = db.get_image_by_sha(conn, sha)
            if existing is None:
                raise
            return IngestResult(int(existing["id"]), True, existing)


def _image(conn: sqlite3.Connection, job: sqlite3.Row) -> sqlite3.Row:
    image_id = job["image_id"]
    if image_id is None:
        raise JobPermanent("job has no image")
    row = db.get_image(conn, int(image_id))
    if row is None:
        raise JobPermanent("image row is gone")
    return row


def _source_path(row: sqlite3.Row) -> Path:
    for name in (row["original_name"], row["display_name"]):
        if not name:
            continue
        path = config.IMAGES_DIR / name
        if path.is_file():
            return path
    raise JobPermanent(f"image file is missing: {row['display_name']}")


def prepare_job(job: sqlite3.Row) -> int | None:
    with db.session() as conn:
        row = _image(conn, job)
        if row["prepared_at"]:
            db.enqueue_job(conn, "recognize", image_id=int(row["id"]), dedupe_key=f"image:{row['id']}")
            return int(row["id"])
        src = _source_path(row)
        try:
            prepared = media.prepare_image(
                src,
                str(row["sha256"]),
                original_name=src.name,
                original_extension=str(row["extension"]),
            )
        except Exception as exc:
            db.set_status(conn, int(row["id"]), "failed", f"prepare: {exc}")
            conn.commit()
            raise JobPermanent(f"prepare failed: {exc}") from exc
        db.update_prepared(
            conn,
            int(row["id"]),
            display_name=prepared.display_name,
            original_name=prepared.original_name,
            thumb_name=prepared.thumb_name,
            mime=prepared.mime,
            extension=prepared.extension,
            width=prepared.width,
            height=prepared.height,
        )
        db.enqueue_job(conn, "recognize", image_id=int(row["id"]), dedupe_key=f"image:{row['id']}")
        return int(row["id"])


def recognize_job(job: sqlite3.Row) -> int | None:
    with db.session() as conn:
        row = _image(conn, job)
        if row["status"] == "ready" and row["recognized_at"]:
            return int(row["id"])
        if not ai.available():
            db.set_status(conn, int(row["id"]), "failed", "AI не настроен: нет MEMSAVE_VISION_API_KEY")
            conn.commit()
            raise JobPermanent("vision API key is not configured")
        src = _source_path(row)
        try:
            derivative, media_type = media.ai_derivative(src)
        except Exception as exc:
            db.set_status(conn, int(row["id"]), "failed", f"image: {exc}")
            conn.commit()
            raise JobPermanent(f"image derivative failed: {exc}") from exc
        try:
            answer = ai.recognize_bytes(derivative, media_type)
        except ai.AiError as exc:
            attempts = int(row["ai_attempts"]) + 1
            db.set_status(
                conn,
                int(row["id"]),
                "pending" if exc.retryable else "failed",
                str(exc),
                attempts=attempts,
            )
            conn.commit()
            if exc.retryable:
                raise JobRetry(str(exc)) from exc
            raise JobPermanent(str(exc)) from exc

        db.save_ai_result(
            conn,
            int(row["id"]),
            category=answer.category,
            description=answer.description,
            extracted_text=answer.text,
            model=answer.model,
            raw_json=ai.raw_json(answer),
            tags=answer.tag_rows(),
        )
        db.mark_ready(conn, int(row["id"]), answer.model)
        db.enqueue_job(conn, "embed", image_id=int(row["id"]), dedupe_key=f"image:{row['id']}")
        return int(row["id"])


def embed_job(job: sqlite3.Row) -> int | None:
    with db.session() as conn:
        row = _image(conn, job)
        try:
            search.embed_image(conn, row)
        except search.SemanticUnavailable as exc:
            raise JobPermanent(str(exc)) from exc
        return int(row["id"])


def fetch_link_job(job: sqlite3.Row) -> int | None:
    payload: dict[str, Any] = {}
    try:
        import json

        payload = json.loads(job["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    url = str(payload.get("url") or "").strip()
    if not url:
        raise JobPermanent("link job has no url")
    try:
        resolved = linkpreview.resolve(url, title_hint=payload.get("title"))
    except linkpreview.NoImage as exc:
        raise JobPermanent(f"no image: {exc}") from exc
    result = ingest_image(
        resolved.data,
        source="link",
        source_url=resolved.page_url,
        image_url=resolved.image_url,
        title=resolved.title,
    )
    return result.image_id


class Worker(threading.Thread):
    def __init__(
        self,
        kind: str,
        handler: Callable[[sqlite3.Row], int | None],
        stop_event: threading.Event,
        *,
        max_attempts: int,
    ) -> None:
        super().__init__(name=f"memsave-{kind}", daemon=True)
        self.kind = kind
        self.handler = handler
        self.stop_event = stop_event
        self.max_attempts = max_attempts

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = db.claim_job(self.kind)
            except Exception:  # pragma: no cover - transient SQLite lock
                logger.exception("could not claim a %s job", self.kind)
                self.stop_event.wait(config.JOB_POLL_SECONDS)
                continue
            if job is None:
                self.stop_event.wait(config.JOB_POLL_SECONDS)
                continue
            try:
                result = self.handler(job)
            except JobRetry as exc:
                try:
                    db.fail_job(
                        int(job["id"]),
                        str(exc),
                        retryable=True,
                        max_attempts=self.max_attempts,
                    )
                except Exception:  # pragma: no cover - keep the worker alive
                    logger.exception("could not requeue job %s/%s", self.kind, job["id"])
            except JobPermanent as exc:
                try:
                    db.fail_job(
                        int(job["id"]),
                        str(exc),
                        retryable=False,
                        max_attempts=self.max_attempts,
                        result_error=str(exc),
                    )
                except Exception:  # pragma: no cover
                    logger.exception("could not mark job %s/%s failed", self.kind, job["id"])
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("job %s/%s failed", self.kind, job["id"])
                try:
                    db.fail_job(
                        int(job["id"]),
                        str(exc),
                        retryable=True,
                        max_attempts=self.max_attempts,
                    )
                except Exception:
                    logger.exception("could not requeue job %s/%s", self.kind, job["id"])
            else:
                try:
                    db.finish_job(int(job["id"]), result_image_id=result)
                except Exception:  # pragma: no cover
                    logger.exception("could not finish job %s/%s", self.kind, job["id"])


class JobRunner:
    """Starts one or more polling workers for each job kind."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.workers: list[Worker] = []

    def start(self) -> None:
        if self.workers:
            return
        self.stop_event.clear()
        db.reset_processing()
        specs = [
            ("prepare", prepare_job, config.PREPARE_WORKERS, 3),
            ("recognize", recognize_job, config.RECOGNIZE_WORKERS, config.VISION_RETRIES),
            ("embed", embed_job, config.EMBED_WORKERS, 2),
            ("fetch_link", fetch_link_job, config.LINK_WORKERS, 2),
        ]
        for kind, handler, count, attempts in specs:
            for _ in range(max(0, count)):
                worker = Worker(kind, handler, self.stop_event, max_attempts=attempts)
                worker.start()
                self.workers.append(worker)

    def stop(self) -> None:
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=5)
        self.workers.clear()


def retry_image(image_id: int) -> bool:
    with db.session() as conn:
        row = db.get_image(conn, image_id)
        if row is None:
            return False
        db.set_status(conn, image_id, "pending", None)
        kind = "recognize" if row["prepared_at"] else "prepare"
        db.enqueue_job(conn, kind, image_id=image_id, dedupe_key=f"image:{image_id}")
        return True


def retry_failed() -> int:
    with db.session() as conn:
        rows = conn.execute("SELECT id, prepared_at FROM images WHERE status = 'failed'").fetchall()
        for row in rows:
            image_id = int(row["id"])
            db.set_status(conn, image_id, "pending", None)
            kind = "recognize" if row["prepared_at"] else "prepare"
            db.enqueue_job(conn, kind, image_id=image_id, dedupe_key=f"image:{image_id}")
        return len(rows)
