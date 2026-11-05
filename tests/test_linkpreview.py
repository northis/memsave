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


def test_tweet_reference_parses_photo_index():
    assert linkpreview._tweet_reference(
        "https://x.com/SanchoVoltov_V2/status/2118360223811613070/photo/2?a=1&s=19"
    ) == ("2118360223811613070", 2)
    assert linkpreview._tweet_reference(
        "https://twitter.com/user/status/12345"
    ) == ("12345", None)
    assert linkpreview._tweet_reference("https://example.com/post/1") is None


def test_x_syndication_media_parses_original_photos(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "text": "Высокая плотность тела https://t.co/IN5QcxLBqG",
                "user": {"name": "Автор"},
                "photos": [
                    {"url": "https://pbs.twimg.com/media/a.jpg"},
                    {"url": "https://pbs.twimg.com/media/b.jpg"},
                ],
                "mediaDetails": [
                    {
                        "type": "photo",
                        "media_url_https": "https://pbs.twimg.com/media/a.jpg",
                        "url": "https://t.co/IN5QcxLBqG",
                    }
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(linkpreview.httpx, "Client", FakeClient)
    urls, title = linkpreview._x_syndication_media("123")
    assert urls == [
        "https://pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com/media/b.jpg",
    ]
    assert title == "Автор: Высокая плотность тела"


def test_x_syndication_prefers_selected_original_photo(monkeypatch):
    monkeypatch.setattr(
        linkpreview,
        "_x_syndication_media",
        lambda tweet_id: (
            ["https://pbs.twimg.com/media/first.jpg", "https://pbs.twimg.com/media/second.jpg"],
            "Автор: подпись",
        ),
    )

    def fake_download(url, referer):
        return linkpreview.Resolved(
            data=b"image",
            media_type="image/jpeg",
            image_url=url,
            page_url=referer,
            title=None,
        )

    monkeypatch.setattr(linkpreview, "_download_image", fake_download)
    resolved = linkpreview.resolve(
        "https://x.com/SanchoVoltov_V2/status/2118360223811613070/photo/2"
    )
    assert resolved.image_url == "https://pbs.twimg.com/media/second.jpg"
    assert resolved.page_url == "https://x.com/SanchoVoltov_V2/status/2118360223811613070/photo/2"
    assert resolved.title == "Автор: подпись"


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
