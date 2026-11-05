"""Hybrid search: FTS5/BM25 plus local multilingual embeddings and RRF."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from typing import Any

from . import config, db
from .text import fold_text, normalize_tag, tag_key

logger = logging.getLogger(__name__)


class SemanticUnavailable(RuntimeError):
    pass


_embedder = None
_embedder_lock = threading.Lock()
_matrix_lock = threading.Lock()
_matrix_cache: dict[str, Any] = {}


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise SemanticUnavailable("numpy is unavailable") from exc
    return np


def get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise SemanticUnavailable("fastembed is not installed") from exc
        try:
            _embedder = TextEmbedding(
                model_name=config.EMBEDDING_MODEL,
                cache_dir=str(config.MODELS_DIR),
            )
        except Exception as exc:
            raise SemanticUnavailable(f"embedding model is unavailable: {exc}") from exc
        return _embedder


def semantic_available() -> bool:
    """Whether the optional embedding stack is importable.

    Deliberately does not load or download the model: browsing the empty archive
    must not pull ~240 MB before the first search.
    """
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _normalize(vector) -> Any:
    np = _numpy()
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm == 0:
        return array
    return array / norm


def embed_texts(texts: list[str]) -> list[Any]:
    if not texts:
        return []
    try:
        vectors = list(get_embedder().passage_embed(texts, batch_size=32))
    except SemanticUnavailable:
        raise
    except Exception as exc:
        raise SemanticUnavailable(f"embedding failed: {exc}") from exc
    return [_normalize(vector) for vector in vectors]


def embed_query(text: str) -> Any:
    try:
        vector = next(iter(get_embedder().query_embed([text])))
    except SemanticUnavailable:
        raise
    except Exception as exc:
        raise SemanticUnavailable(f"query embedding failed: {exc}") from exc
    return _normalize(vector)


def _doc_text(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    ai = conn.execute("SELECT * FROM ai_results WHERE image_id = ?", (row["id"],)).fetchone()
    tags = db.tags_for_images(conn, [int(row["id"])]).get(int(row["id"]), [])
    parts = [
        row["title"] or "",
        ai["category"] if ai else "",
        ai["description"] if ai else "",
        ai["extracted_text"] if ai else "",
        " ".join(t["name"] for t in tags),
    ]
    return "\n".join(part for part in parts if part).strip()


def _text_hash(text: str) -> str:
    payload = f"{config.EMBEDDING_VERSION}|{config.EMBEDDING_MODEL}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embed_image(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    text = _doc_text(conn, row)
    if not text:
        return False
    digest = _text_hash(text)
    existing = conn.execute(
        "SELECT model, text_hash FROM image_embeddings WHERE image_id = ?", (row["id"],)
    ).fetchone()
    if existing is not None and existing["model"] == config.EMBEDDING_MODEL and existing["text_hash"] == digest:
        return False
    vector = embed_texts([text])[0]
    db.save_embedding(
        conn,
        int(row["id"]),
        model=config.EMBEDDING_MODEL,
        dim=len(vector),
        vector=vector.tobytes(),
        text_hash=digest,
    )
    return True


def reindex(conn: sqlite3.Connection, *, full: bool = False) -> dict[str, int]:
    rows = conn.execute(
        "SELECT * FROM images WHERE status = 'ready' ORDER BY id"
    ).fetchall()
    if full:
        conn.execute("DELETE FROM image_embeddings")
    updates = 0
    skipped = 0
    errors = 0
    for row in rows:
        try:
            if embed_image(conn, row):
                updates += 1
            else:
                skipped += 1
        except SemanticUnavailable as exc:
            errors += 1
            logger.warning("semantic reindex stopped: %s", exc)
            break
        except Exception:
            errors += 1
            logger.exception("could not embed image %s", row["id"])
    conn.commit()
    return {"updated": updates, "skipped": skipped, "errors": errors, "total": len(rows)}


def _tag_clause(tag: str | None) -> tuple[str, list[Any]]:
    if not tag:
        return "", []
    folded = tag_key(tag)
    if not folded:
        return "", []
    return (
        "EXISTS (SELECT 1 FROM image_tags it JOIN tags t ON t.id = it.tag_id "
        "WHERE it.image_id = images.id AND t.folded = ?)",
        [folded],
    )


def _allowed_ids(conn: sqlite3.Connection, tag: str | None) -> set[int] | None:
    if not tag:
        return None
    folded = tag_key(tag)
    if not folded:
        return set()
    rows = conn.execute(
        "SELECT it.image_id FROM image_tags it JOIN tags t ON t.id = it.tag_id WHERE t.folded = ?",
        (folded,),
    ).fetchall()
    return {int(row["image_id"]) for row in rows}


def _list_images(
    conn: sqlite3.Connection,
    *,
    tag: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> tuple[list[sqlite3.Row], int]:
    clause, params = _tag_clause(tag)
    where = f"WHERE {clause}" if clause else ""
    order = "ASC" if sort == "old" else "DESC"
    total = int(
        conn.execute(f"SELECT COUNT(*) FROM images {where}", params).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT * FROM images {where} ORDER BY saved_at {order}, id {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return rows, total


def _fts_ids(conn: sqlite3.Connection, query: str, allowed: set[int] | None) -> list[int]:
    tokens = [token for token in fold_text(query).split(" ") if token]
    if not tokens:
        return []
    # Exact tokens first; _like_ids is the partial-word fallback when FTS finds
    # nothing. Prefix matching here would make a short query such as "кот"
    # match unrelated words like "которого".
    fts_query = " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    try:
        rows = conn.execute(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH ? "
            "ORDER BY bm25(images_fts) LIMIT ?",
            (fts_query, config.SEMANTIC_CANDIDATES),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    result = [int(row["rowid"]) for row in rows]
    if allowed is not None:
        result = [image_id for image_id in result if image_id in allowed]
    return result


def _like_ids(conn: sqlite3.Connection, query: str, allowed: set[int] | None, limit: int) -> list[int]:
    tokens = [token for token in fold_text(query).split(" ") if token]
    if not tokens:
        return []
    clauses = ["LOWER(images.search_text) LIKE ?" for _ in tokens]
    params: list[Any] = [f"%{token}%" for token in tokens]
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT images.id FROM images WHERE {where} ORDER BY images.saved_at DESC LIMIT ?",
        [*params, max(limit, config.SEMANTIC_CANDIDATES)],
    ).fetchall()
    result = [int(row["id"]) for row in rows]
    if allowed is not None:
        result = [image_id for image_id in result if image_id in allowed]
    return result


def _vector_ids(conn: sqlite3.Connection, query: str, allowed: set[int] | None, limit: int) -> list[int]:
    np = _numpy()
    query_vector = embed_query(query)
    rows = db.all_embeddings(conn, config.EMBEDDING_MODEL)
    if not rows:
        return []
    image_ids: list[int] = []
    vectors: list[Any] = []
    for row in rows:
        image_id = int(row["image_id"])
        if allowed is not None and image_id not in allowed:
            continue
        vector = np.frombuffer(row["vector"], dtype=np.float32)
        if vector.shape[0] != query_vector.shape[0]:
            continue
        image_ids.append(image_id)
        vectors.append(vector)
    if not image_ids:
        return []
    matrix = np.vstack(vectors)
    scores = matrix @ query_vector
    order = np.argsort(-scores)[: max(1, limit)]
    best = float(scores[int(order[0])]) if len(order) else 0.0
    threshold = max(config.SEMANTIC_MIN_SCORE, best * config.SEMANTIC_RELATIVE_SCORE)
    return [
        image_ids[int(index)]
        for index in order
        if float(scores[int(index)]) >= threshold
    ]


def _rrf(text_ids: list[int], vector_ids: list[int]) -> list[int]:
    scores: dict[int, float] = {}
    text_rank = {image_id: rank for rank, image_id in enumerate(text_ids)}
    for rank, image_id in enumerate(text_ids):
        scores[image_id] = scores.get(image_id, 0.0) + 1.0 / (config.RRF_K + rank + 1)
    for rank, image_id in enumerate(vector_ids):
        scores[image_id] = scores.get(image_id, 0.0) + 1.0 / (config.RRF_K + rank + 1)
    return sorted(
        scores,
        key=lambda image_id: (
            -scores[image_id],
            text_rank.get(image_id, 10**9),
            -image_id,
        ),
    )


def search_images(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    tag: str | None = None,
    sort: str = "new",
    limit: int = 40,
    offset: int = 0,
) -> dict[str, Any]:
    query = (query or "").strip()
    sort = "old" if sort == "old" else "new"
    if not query:
        rows, total = _list_images(conn, tag=tag, sort=sort, limit=limit, offset=offset)
        return {
            "rows": rows,
            "total": total,
            "mode": "browse",
            "semantic_available": semantic_available(),
        }

    allowed = _allowed_ids(conn, tag)
    if allowed is not None and not allowed:
        return {"rows": [], "total": 0, "mode": "text", "semantic_available": semantic_available()}

    text_ids = _fts_ids(conn, query, allowed)
    mode = "text"
    semantic = semantic_available()
    vector_ids: list[int] = []
    has_embeddings = conn.execute(
        "SELECT 1 FROM image_embeddings WHERE model = ? LIMIT 1", (config.EMBEDDING_MODEL,)
    ).fetchone()
    if semantic and has_embeddings is not None:
        try:
            vector_ids = _vector_ids(conn, query, allowed, config.SEMANTIC_CANDIDATES)
            if vector_ids:
                mode = "hybrid"
        except SemanticUnavailable:
            semantic = False

    if not text_ids:
        text_ids = _like_ids(conn, query, allowed, config.SEMANTIC_CANDIDATES)
    if not text_ids and not vector_ids:
        return {"rows": [], "total": 0, "mode": mode, "semantic_available": semantic}

    fused = _rrf(text_ids, vector_ids) if vector_ids else text_ids
    total = len(fused)
    page = fused[offset : offset + limit]
    rows_by_id = db.images_by_ids(conn, page)
    rows = [rows_by_id[image_id] for image_id in page if image_id in rows_by_id]
    return {"rows": rows, "total": total, "mode": mode, "semantic_available": semantic}


def semantic_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    ready = int(
        conn.execute("SELECT COUNT(*) FROM images WHERE status = 'ready'").fetchone()[0]
    )
    embedded = int(
        conn.execute(
            "SELECT COUNT(*) FROM image_embeddings WHERE model = ?", (config.EMBEDDING_MODEL,)
        ).fetchone()[0]
    )
    return {
        "available": semantic_available(),
        "model": config.EMBEDDING_MODEL,
        "dim": config.EMBEDDING_DIM,
        "embedded": embedded,
        "stale": max(0, ready - embedded),
    }
