"""SQLite storage: schema, image rows, tags, embeddings and the job queue."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import config
from .text import fold_text, tag_key

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    original_name TEXT,
    thumb_name TEXT,
    mime TEXT NOT NULL,
    extension TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'upload',
    source_url TEXT,
    image_url TEXT,
    title TEXT,
    saved_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_error TEXT,
    ai_attempts INTEGER NOT NULL DEFAULT 0,
    ai_model TEXT,
    prepared_at INTEGER,
    recognized_at INTEGER,
    search_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_images_saved_at ON images(saved_at DESC);
CREATE INDEX IF NOT EXISTS ix_images_status ON images(status);
CREATE INDEX IF NOT EXISTS ix_images_sha ON images(sha256);

CREATE TABLE IF NOT EXISTS ai_results (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    extracted_text TEXT,
    model TEXT,
    raw_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folded TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'tag',
    UNIQUE(folded, kind)
);

CREATE TABLE IF NOT EXISTS image_tags (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (image_id, tag_id)
);

CREATE INDEX IF NOT EXISTS ix_image_tags_tag ON image_tags(tag_id, image_id);

CREATE TABLE IF NOT EXISTS image_embeddings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    text_hash TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_embeddings_model ON image_embeddings(model);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    image_id INTEGER,
    payload TEXT,
    dedupe_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    result_image_id INTEGER,
    result_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs(kind, status, next_attempt_at, id);

CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
    description,
    extracted_text,
    tags,
    category,
    title,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def now() -> int:
    return int(time.time())


def connect(path: Any | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(path: Any | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Any | None = None) -> None:
    config.ensure_dirs()
    with session(path) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {version} is newer than this memsave")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


# ---------------------------------------------------------------------------
# Images


def get_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()


def get_image_by_sha(conn: sqlite3.Connection, sha: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM images WHERE sha256 = ?", (sha,)).fetchone()


def insert_image(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    display_name: str,
    original_name: str | None,
    mime: str,
    extension: str,
    width: int,
    height: int,
    size_bytes: int,
    source: str,
    source_url: str | None = None,
    image_url: str | None = None,
    title: str | None = None,
    saved_at: int | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO images (
            sha256, display_name, original_name, mime, extension, width, height,
            size_bytes, source, source_url, image_url, title, saved_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            sha256,
            display_name,
            original_name,
            mime,
            extension,
            width,
            height,
            size_bytes,
            source,
            source_url,
            image_url,
            title,
            saved_at or now(),
        ),
    )
    return int(cursor.lastrowid)


def update_prepared(
    conn: sqlite3.Connection,
    image_id: int,
    *,
    display_name: str,
    original_name: str | None,
    thumb_name: str,
    mime: str,
    extension: str,
    width: int,
    height: int,
) -> None:
    conn.execute(
        """
        UPDATE images
           SET display_name = ?, original_name = ?, thumb_name = ?, mime = ?, extension = ?,
               width = ?, height = ?, prepared_at = ?
         WHERE id = ?
        """,
        (
            display_name,
            original_name,
            thumb_name,
            mime,
            extension,
            width,
            height,
            now(),
            image_id,
        ),
    )


def set_status(
    conn: sqlite3.Connection,
    image_id: int,
    status: str,
    error: str | None = None,
    *,
    attempts: int | None = None,
) -> None:
    if attempts is None:
        conn.execute(
            "UPDATE images SET status = ?, ai_error = ? WHERE id = ?",
            (status, error, image_id),
        )
    else:
        conn.execute(
            "UPDATE images SET status = ?, ai_error = ?, ai_attempts = ? WHERE id = ?",
            (status, error, attempts, image_id),
        )


def mark_ready(conn: sqlite3.Connection, image_id: int, model: str) -> None:
    conn.execute(
        "UPDATE images SET status = 'ready', ai_error = NULL, ai_model = ?, recognized_at = ? WHERE id = ?",
        (model, now(), image_id),
    )


def save_ai_result(
    conn: sqlite3.Connection,
    image_id: int,
    *,
    category: str,
    description: str,
    extracted_text: str | None,
    model: str,
    raw_json: str,
    tags: list[tuple[str, str]],
) -> None:
    """Store the AI answer, replace tags and rebuild the FTS row."""
    conn.execute(
        """
        INSERT INTO ai_results (image_id, category, description, extracted_text, model, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_id) DO UPDATE SET
            category = excluded.category,
            description = excluded.description,
            extracted_text = excluded.extracted_text,
            model = excluded.model,
            raw_json = excluded.raw_json,
            created_at = excluded.created_at
        """,
        (image_id, category, description, extracted_text, model, raw_json, now()),
    )
    replace_tags(conn, image_id, tags)
    refresh_search(conn, image_id, category, description, extracted_text, tags)


def replace_tags(conn: sqlite3.Connection, image_id: int, tags: list[tuple[str, str]]) -> None:
    """tags: [(display name, kind)] in rank order."""
    conn.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
    seen: set[tuple[str, str]] = set()
    for rank, (name, kind) in enumerate(tags):
        folded = tag_key(name)
        if not folded:
            continue
        key = (folded, kind)
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            "INSERT INTO tags (name, folded, kind) VALUES (?, ?, ?) "
            "ON CONFLICT(folded, kind) DO UPDATE SET name = excluded.name",
            (name, folded, kind),
        )
        row = conn.execute(
            "SELECT id FROM tags WHERE folded = ? AND kind = ?", (folded, kind)
        ).fetchone()
        if row is not None:
            conn.execute(
                "INSERT OR REPLACE INTO image_tags (image_id, tag_id, rank) VALUES (?, ?, ?)",
                (image_id, int(row["id"]), rank),
            )


def refresh_search(
    conn: sqlite3.Connection,
    image_id: int,
    category: str,
    description: str,
    extracted_text: str | None,
    tags: list[tuple[str, str]],
) -> None:
    title_row = conn.execute("SELECT title, source_url FROM images WHERE id = ?", (image_id,)).fetchone()
    title = title_row["title"] if title_row else None
    tag_names = [name for name, _ in tags]
    search_text = fold_text(
        " ".join(
            part
            for part in (
                category,
                description,
                extracted_text or "",
                " ".join(tag_names),
                title or "",
            )
            if part
        )
    )
    conn.execute("UPDATE images SET search_text = ? WHERE id = ?", (search_text, image_id))
    conn.execute("DELETE FROM images_fts WHERE rowid = ?", (image_id,))
    conn.execute(
        "INSERT INTO images_fts (rowid, description, extracted_text, tags, category, title) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            image_id,
            description or "",
            extracted_text or "",
            " ".join(tag_names),
            category or "",
            title or "",
        ),
    )


def delete_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    row = get_image(conn, image_id)
    if row is None:
        return None
    conn.execute("DELETE FROM images_fts WHERE rowid = ?", (image_id,))
    conn.execute("DELETE FROM jobs WHERE image_id = ?", (image_id,))
    conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    return row


def images_by_ids(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT * FROM images WHERE id IN ({placeholders})", ids).fetchall()
    return {int(row["id"]): row for row in rows}


def tags_for_images(conn: sqlite3.Connection, ids: list[int]) -> dict[int, list[sqlite3.Row]]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT it.image_id, t.id, t.name, t.folded, t.kind, it.rank
          FROM image_tags it
          JOIN tags t ON t.id = it.tag_id
         WHERE it.image_id IN ({placeholders})
         ORDER BY it.image_id, it.rank, t.name
        """,
        ids,
    ).fetchall()
    result: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        result.setdefault(int(row["image_id"]), []).append(row)
    return result


def image_payload(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    tags = tags_for_images(conn, [int(row["id"])]).get(int(row["id"]), [])
    ai = conn.execute("SELECT * FROM ai_results WHERE image_id = ?", (row["id"],)).fetchone()
    payload: dict[str, Any] = {
        "id": int(row["id"]),
        "sha256": row["sha256"],
        "width": row["width"],
        "height": row["height"],
        "saved_at": int(row["saved_at"]),
        "status": row["status"],
        "source": row["source"],
        "source_url": row["source_url"],
        "image_url": row["image_url"],
        "url": f"/images/{row['display_name']}" if row["display_name"] else None,
        "thumb_url": f"/thumbs/{row['thumb_name']}" if row["thumb_name"] else None,
        "mime": row["mime"],
        "size_bytes": int(row["size_bytes"]),
        "category": ai["category"] if ai is not None else None,
        "description": "",
        "text": None,
        "tags": [
            {"name": t["name"], "kind": t["kind"], "rank": int(t["rank"])} for t in tags
        ],
        "ai_error": row["ai_error"],
    }
    if detail:
        if ai is not None:
            payload["description"] = ai["description"]
            payload["text"] = ai["extracted_text"]
        payload["title"] = row["title"]
        payload["original_name"] = row["original_name"]
        payload["ai_model"] = row["ai_model"]
        payload["recognized_at"] = row["recognized_at"]
    return payload


# ---------------------------------------------------------------------------
# Jobs


def enqueue_job(
    conn: sqlite3.Connection,
    kind: str,
    *,
    image_id: int | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    delay: int = 0,
) -> int:
    if dedupe_key:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE kind = ? AND dedupe_key = ? AND status IN ('queued', 'processing')",
            (kind, dedupe_key),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
    ts = now()
    cursor = conn.execute(
        """
        INSERT INTO jobs (kind, image_id, payload, dedupe_key, status, next_attempt_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
        """,
        (
            kind,
            image_id,
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            dedupe_key,
            ts + max(0, delay),
            ts,
            ts,
        ),
    )
    return int(cursor.lastrowid)


def claim_job(kind: str) -> sqlite3.Row | None:
    ts = now()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
             WHERE kind = ? AND status = 'queued' AND next_attempt_at <= ?
             ORDER BY next_attempt_at, id
             LIMIT 1
            """,
            (kind, ts),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET status = 'processing', updated_at = ? WHERE id = ?",
            (ts, row["id"]),
        )
        conn.commit()
        return row
    finally:
        conn.close()


def finish_job(job_id: int, *, result_image_id: int | None = None) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', updated_at = ?, result_image_id = COALESCE(?, result_image_id) WHERE id = ?",
            (now(), result_image_id, job_id),
        )


def fail_job(
    job_id: int,
    error: str,
    *,
    retryable: bool,
    max_attempts: int,
    result_error: str | None = None,
) -> None:
    ts = now()
    with session() as conn:
        row = conn.execute("SELECT attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        attempts = int(row["attempts"]) + 1
        if retryable and attempts < max_attempts:
            delay = min(300, 5 * (2 ** (attempts - 1)))
            conn.execute(
                """
                UPDATE jobs
                   SET status = 'queued', attempts = ?, next_attempt_at = ?, last_error = ?,
                       result_error = ?, updated_at = ?
                 WHERE id = ?
                """,
                (attempts, ts + delay, error[:1000], result_error, ts, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                   SET status = 'failed', attempts = ?, last_error = ?, result_error = ?, updated_at = ?
                 WHERE id = ?
                """,
                (attempts, error[:1000], result_error or error[:1000], ts, job_id),
            )


def reset_processing() -> int:
    with session() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'processing'",
            (now(),),
        )
        return int(cursor.rowcount)


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def job_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload: Any = None
    if row["payload"]:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = None
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "status": row["status"],
        "attempts": int(row["attempts"]),
        "error": row["result_error"] or row["last_error"],
        "image_id": row["image_id"],
        "result_image_id": row["result_image_id"],
        "payload": payload,
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


# ---------------------------------------------------------------------------
# Embeddings


def save_embedding(
    conn: sqlite3.Connection, image_id: int, *, model: str, dim: int, vector: bytes, text_hash: str
) -> None:
    conn.execute(
        """
        INSERT INTO image_embeddings (image_id, model, dim, vector, text_hash, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_id) DO UPDATE SET
            model = excluded.model, dim = excluded.dim, vector = excluded.vector,
            text_hash = excluded.text_hash, updated_at = excluded.updated_at
        """,
        (image_id, model, dim, vector, text_hash, now()),
    )


def all_embeddings(conn: sqlite3.Connection, model: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT image_id, vector, dim FROM image_embeddings WHERE model = ?", (model,)
    ).fetchall()


def embeddings_meta(conn: sqlite3.Connection, model: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), 0) AS m FROM image_embeddings WHERE model = ?",
        (model,),
    ).fetchone()
    return int(row["n"]), int(row["m"])


# ---------------------------------------------------------------------------
# Stats


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    images = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS total FROM images"
    ).fetchone()
    by_status = {
        str(row["status"]): int(row["n"])
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM images GROUP BY status")
    }
    tags = int(conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
    embeddings = int(conn.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0])
    return {
        "images": int(images["n"]),
        "bytes": int(images["total"]),
        "by_status": by_status,
        "tags": tags,
        "embeddings": embeddings,
    }
