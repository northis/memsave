from __future__ import annotations

import numpy as np
import pytest

from memsave import db, search


class FakeEmbedder:
    def vector(self, text: str):
        value = text.lower()
        if "кот" in value or "cat" in value:
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        if "собак" in value or "dog" in value:
            return np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
        return np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)

    def passage_embed(self, texts, batch_size=32):
        return [self.vector(text) for text in texts]

    def query_embed(self, texts):
        return [self.vector(text) for text in texts]


@pytest.fixture
def fake_embedder(monkeypatch):
    embedder = FakeEmbedder()
    monkeypatch.setattr(search, "get_embedder", lambda: embedder)
    return embedder


def _add_image(conn, *, sha, title, description, tags, saved_at):
    image_id = db.insert_image(
        conn,
        sha256=sha,
        display_name=f"{sha}.jpg",
        original_name=None,
        mime="image/jpeg",
        extension="jpg",
        width=100,
        height=100,
        size_bytes=10,
        source="upload",
        title=title,
        saved_at=saved_at,
    )
    db.save_ai_result(
        conn,
        image_id,
        category="meme",
        description=description,
        extracted_text=description,
        model="fake",
        raw_json="{}",
        tags=[("Мем", "category"), *[(tag, "tag") for tag in tags]],
    )
    db.mark_ready(conn, image_id, "fake")
    return image_id


def test_fts_and_semantic_search(paths, fake_embedder):
    with db.session() as conn:
        cat = _add_image(
            conn,
            sha="a" * 64,
            title="Кот",
            description="Кот смотрит на торт",
            tags=["кот", "торт"],
            saved_at=100,
        )
        dog = _add_image(
            conn,
            sha="b" * 64,
            title="Собака",
            description="Собака в парке",
            tags=["собака", "парк"],
            saved_at=200,
        )
        result = search.reindex(conn)
        assert result["updated"] == 2

    with db.session() as conn:
        found = search.search_images(conn, query="кот")
        assert [row["id"] for row in found["rows"]] == [cat]
        assert found["mode"] == "hybrid"

        found = search.search_images(conn, query="собак")
        assert [row["id"] for row in found["rows"]] == [dog]

        tagged = search.search_images(conn, tag="кот")
        assert [row["id"] for row in tagged["rows"]] == [cat]

        browsed = search.search_images(conn, sort="old")
        assert [row["id"] for row in browsed["rows"]] == [cat, dog]


def test_search_filters_by_tag_and_falls_back_to_like(paths, fake_embedder):
    with db.session() as conn:
        cat = _add_image(
            conn,
            sha="c" * 64,
            title="Кот",
            description="Кот в коробке",
            tags=["кот"],
            saved_at=100,
        )
        _add_image(
            conn,
            sha="d" * 64,
            title="Пёс",
            description="Пёс на диване",
            tags=["собака"],
            saved_at=200,
        )

    with db.session() as conn:
        # A partial substring that FTS may not split the same way still works via LIKE.
        found = search.search_images(conn, query="короб")
        assert [row["id"] for row in found["rows"]] == [cat]
        none = search.search_images(conn, tag="нет-такого")
        assert none["total"] == 0
