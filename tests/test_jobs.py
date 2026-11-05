from __future__ import annotations

from memsave import ai, config, db, jobs, prompt


CSRF = {"X-Memsave-Request": "1"}


def _upload(client, jpeg_bytes):
    return client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={**CSRF, "Content-Type": "image/jpeg", "X-Memsave-Source": "paste"},
    ).json()["image"]


def test_prepare_creates_thumbnail_and_recognize_job(client, jpeg_bytes):
    image = _upload(client, jpeg_bytes)
    job = db.claim_job("prepare")
    assert job is not None
    jobs.prepare_job(job)
    db.finish_job(int(job["id"]))

    with db.session() as conn:
        row = db.get_image(conn, image["id"])
        assert row is not None
        assert row["thumb_name"]
        assert (config.THUMBS_DIR / row["thumb_name"]).is_file()
        assert db.claim_job("recognize") is not None


def test_recognize_stores_tags_and_queues_embedding(client, jpeg_bytes, monkeypatch):
    image = _upload(client, jpeg_bytes)
    prepare = db.claim_job("prepare")
    jobs.prepare_job(prepare)
    db.finish_job(int(prepare["id"]))

    answer = prompt.parse_answer(
        '{"category":"meme","description":"Кот с тортом","text":"Happy birthday","tags":["кот","торт","кот"]}',
        "fake-model",
    )
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "recognize_bytes", lambda data, media_type="image/jpeg": answer)

    recognize = db.claim_job("recognize")
    assert recognize is not None
    jobs.recognize_job(recognize)
    db.finish_job(int(recognize["id"]))

    with db.session() as conn:
        row = db.get_image(conn, image["id"])
        assert row is not None
        assert row["status"] == "ready"
        result = conn.execute("SELECT * FROM ai_results WHERE image_id = ?", (image["id"],)).fetchone()
        assert result["category"] == "meme"
        assert result["extracted_text"] == "Happy birthday"
        tags = db.tags_for_images(conn, [image["id"]])[image["id"]]
        assert [tag["name"] for tag in tags] == ["Мем", "кот", "торт"]
        assert db.claim_job("embed") is not None


def test_recognize_without_key_marks_failed(client, jpeg_bytes):
    image = _upload(client, jpeg_bytes)
    prepare = db.claim_job("prepare")
    jobs.prepare_job(prepare)
    db.finish_job(int(prepare["id"]))

    recognize = db.claim_job("recognize")
    assert recognize is not None
    try:
        jobs.recognize_job(recognize)
        raise AssertionError("expected JobPermanent")
    except jobs.JobPermanent:
        db.fail_job(int(recognize["id"]), "no key", retryable=False, max_attempts=2)

    with db.session() as conn:
        row = db.get_image(conn, image["id"])
        assert row is not None
        assert row["status"] == "failed"
        assert "AI" in row["ai_error"]
