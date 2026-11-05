from __future__ import annotations

import httpx
import pytest
from bs4 import BeautifulSoup

from memsave import config, linkpreview


HTML = """
<!doctype html>
<html><head>
  <title>Preview page</title>
  <meta property="og:image" content="/img/cat.jpg">
  <meta name="twitter:image" content="https://cdn.example.com/dog.jpg">
  <link rel="image_src" href="/img/third.jpg">
  <script type="application/ld+json">{"image": {"url": "/img/ld.jpg"}}</script>
</head><body>Hello</body></html>
"""


def test_extract_first_url():
    assert linkpreview.extract_first_url("look https://x.com/a/status/1, ok") == "https://x.com/a/status/1"
    assert linkpreview.extract_first_url("no link") is None


def test_image_candidates_order_and_absolute_urls():
    soup = BeautifulSoup(HTML, "html.parser")
    candidates = linkpreview._image_candidates(soup, "https://example.com/post/1")
    assert candidates[0] == "https://example.com/img/cat.jpg"
    assert "https://cdn.example.com/dog.jpg" in candidates
    assert "https://example.com/img/third.jpg" in candidates
    assert "https://example.com/img/ld.jpg" in candidates


def test_resolve_uses_preview_image(monkeypatch):
    page = linkpreview.Fetched(
        url="https://example.com/post/1",
        content_type="text/html",
        data=HTML.encode("utf-8"),
        headers=httpx.Headers(),
    )
    monkeypatch.setattr(linkpreview, "_fetch", lambda url, max_bytes, headers: page)
    monkeypatch.setattr(
        linkpreview,
        "_download_image",
        lambda url, referer: linkpreview.Resolved(
            data=b"image",
            media_type="image/jpeg",
            image_url=url,
            page_url=referer,
            title=None,
        ),
    )
    resolved = linkpreview.resolve("https://example.com/post/1")
    assert resolved.image_url == "https://example.com/img/cat.jpg"
    assert resolved.page_url == "https://example.com/post/1"
    assert resolved.title == "Preview page"


def test_private_addresses_are_rejected():
    with pytest.raises(linkpreview.LinkError):
        linkpreview._assert_public_url("http://127.0.0.1/secret")
    with pytest.raises(linkpreview.LinkError):
        linkpreview._assert_public_url("http://10.0.0.5/secret")
    with pytest.raises(linkpreview.LinkError):
        linkpreview._assert_public_url("ftp://example.com/file")
