from __future__ import annotations

import json

from memsave import config, db, linkpreview, jobs


CSRF = {"X-Memsave-Request": "1"}


def test_home_version_and_pwa_assets(client):
    root = client.get("/")
    assert root.status_code == 200
    assert "memsave" in root.text
    assert "app.js?v=" in root.text
    assert client.get("/health").json() == {"status": "ok"}
    version = client.get("/api/version").json()
    assert version["version"] == "1.0.1"
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert "application/manifest+json" in manifest.headers["content-type"]
    assert client.get("/sw.js").status_code == 200
    assert client.get("/favicon.ico", follow_redirects=False).status_code == 307
    assert client.get("/share").status_code == 200
    assert client.post("/share", follow_redirects=False).status_code == 303


def test_upload_requires_csrf_and_deduplicates(client, jpeg_bytes):
    missing = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={"Content-Type": "image/jpeg"},
    )
    assert missing.status_code == 403

    first = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={**CSRF, "Content-Type": "image/jpeg", "X-Memsave-Source": "paste"},
    )
    assert first.status_code == 201
    payload = first.json()
    assert payload["status"] == "pending"
    image = payload["image"]
    assert image["source"] == "paste"
    assert image["url"].startswith("/images/")
    assert client.get(image["url"]).status_code == 200

    second = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={**CSRF, "Content-Type": "image/jpeg"},
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["image"]["id"] == image["id"]

    listed = client.get("/api/images").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == image["id"]


def test_upload_rejects_non_image(client):
    response = client.post(
        "/api/images",
        content=b"<svg></svg>",
        headers={**CSRF, "Content-Type": "image/svg+xml"},
    )
    assert response.status_code == 415


def test_tag_filter_and_detail(client, jpeg_bytes):
    uploaded = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={**CSRF, "Content-Type": "image/jpeg"},
    ).json()["image"]
    with db.session() as conn:
        db.save_ai_result(
            conn,
            uploaded["id"],
            category="meme",
            description="Кот смотрит на торт",
            extracted_text="Happy birthday",
            model="fake",
            raw_json="{}",
            tags=[("Мем", "category"), ("кот", "tag"), ("торт", "tag")],
        )
        db.mark_ready(conn, uploaded["id"], "fake")

    detail = client.get(f"/api/images/{uploaded['id']}").json()
    assert detail["category"] == "meme"
    assert detail["text"] == "Happy birthday"
    assert [tag["name"] for tag in detail["tags"]][:3] == ["Мем", "кот", "торт"]

    filtered = client.get("/api/images?tag=кот").json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == uploaded["id"]
    assert client.get("/api/images?tag=собака").json()["total"] == 0


def test_delete_removes_row_and_file(client, jpeg_bytes):
    image = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={**CSRF, "Content-Type": "image/jpeg"},
    ).json()["image"]
    assert client.delete(f"/api/images/{image['id']}").status_code == 403
    assert client.delete(f"/api/images/{image['id']}", headers=CSRF).status_code == 200
    assert client.get(f"/api/images/{image['id']}").status_code == 404
    assert client.get(image["url"]).status_code == 404


def test_link_job_fetches_and_ingests(client, jpeg_bytes, monkeypatch):
    monkeypatch.setattr(linkpreview, "_assert_public_url", lambda url: None)
    monkeypatch.setattr(
        linkpreview,
        "resolve",
        lambda url, title_hint=None: linkpreview.Resolved(
            data=jpeg_bytes,
            media_type="image/jpeg",
            image_url="https://cdn.example.com/cat.jpg",
            page_url="https://example.com/post/1",
            title="A post",
        ),
    )
    response = client.post(
        "/api/links",
        json={"url": "https://example.com/post/1", "title": "shared"},
        headers=CSRF,
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    job = db.claim_job("fetch_link")
    assert job is not None
    image_id = jobs.fetch_link_job(job)
    db.finish_job(int(job["id"]), result_image_id=image_id)

    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "done"
    assert status["image"]["source"] == "link"
    assert status["image"]["source_url"] == "https://example.com/post/1"
    assert status["image"]["image_url"] == "https://cdn.example.com/cat.jpg"


def test_link_without_image_adds_nothing(client, monkeypatch):
    monkeypatch.setattr(linkpreview, "_assert_public_url", lambda url: None)
    monkeypatch.setattr(
        linkpreview,
        "resolve",
        lambda url, title_hint=None: (_ for _ in ()).throw(linkpreview.NoImage("none")),
    )
    response = client.post("/api/links", json={"url": "https://example.com/none"}, headers=CSRF)
    assert response.status_code == 202
    job = db.claim_job("fetch_link")
    assert job is not None
    try:
        jobs.fetch_link_job(job)
        raise AssertionError("expected NoImage")
    except jobs.JobPermanent as exc:
        db.fail_job(int(job["id"]), str(exc), retryable=False, max_attempts=2)
    status = client.get(f"/api/jobs/{response.json()['job_id']}").json()
    assert status["status"] == "failed"
    assert "no image" in status["error"]
    assert client.get("/api/images").json()["total"] == 0


def test_foreign_origin_is_rejected(client, jpeg_bytes):
    response = client.post(
        "/api/images",
        content=jpeg_bytes,
        headers={
            **CSRF,
            "Content-Type": "image/jpeg",
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403


def test_path_traversal_is_not_served(client):
    assert client.get("/images/../memsave.db").status_code == 404
    assert client.get("/thumbs/not-a-thumb.webp").status_code == 404


