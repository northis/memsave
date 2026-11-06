"""Resolve a shared page to its preview image (OpenGraph/Twitter Cards).

This is the same mechanism Slack, Telegram and X use for link previews: fetch the
page as a browser/bot, read the preview metadata, then fetch the image itself.
For X/Twitter the embed syndication payload is tried first, because it exposes
the original photo URLs (and the selected /photo/N); OG metadata is the fallback.
No image found means no record is created.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import config, media

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
BOT_UA = "Twitterbot/1.0"
REFERER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8,ru;q=0.6",
}
IMAGE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5",
}
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
PIC_TWITTER_RE = re.compile(r"https?://pic\.twitter\.com/[A-Za-z0-9]+", re.IGNORECASE)
TWITTER_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:[^/]+/status|i/web/status)/(\d+)",
    re.IGNORECASE,
)
TWITTER_PHOTO_RE = re.compile(r"/photo/(\d+)", re.IGNORECASE)
X_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
FX_TWITTER_URL = "https://api.fxtwitter.com/status/{tweet_id}"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif"}


class LinkError(RuntimeError):
    """A link could not be fetched safely."""


class NoImage(LinkError):
    """The page has no usable preview image."""


@dataclass(frozen=True)
class Resolved:
    data: bytes
    media_type: str
    image_url: str
    page_url: str
    title: str | None


@dataclass(frozen=True)
class Fetched:
    url: str
    content_type: str
    data: bytes
    headers: httpx.Headers


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,);]}>\"'")


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise LinkError("only http(s) links are supported")
    host = (parsed.hostname or "").lower()
    if not host:
        raise LinkError("link has no host")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise LinkError("private host is not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise LinkError("invalid port") from exc
    if port not in config.LINK_ALLOWED_PORTS:
        raise LinkError("port is not allowed")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LinkError("host does not resolve") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise LinkError("private network address is not allowed")


def _fetch(url: str, *, max_bytes: int, headers: dict[str, str]) -> Fetched:
    current = url
    with httpx.Client(timeout=config.LINK_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(config.LINK_MAX_REDIRECTS + 1):
            _assert_public_url(current)
            with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise LinkError(f"redirect without location ({response.status_code})")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise LinkError(f"http {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise LinkError("response is too large")
                    chunks.append(chunk)
                return Fetched(
                    url=current,
                    content_type=response.headers.get("content-type", "").split(";")[0].lower(),
                    data=b"".join(chunks),
                    headers=response.headers,
                )
    raise LinkError("too many redirects")


def _meta_values(soup: BeautifulSoup) -> dict[str, str]:
    values: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name") or tag.get("itemprop")
        content = tag.get("content")
        if key and content:
            values[str(key).strip().lower()] = str(content).strip()
    return values


def _json_ld_images(soup: BeautifulSoup) -> list[str]:
    result: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            image = value.get("image")
            if isinstance(image, str):
                result.append(image)
            elif isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        result.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("url"), str):
                        result.append(item["url"])
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                result.append(image["url"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            walk(json.loads(text))
        except (TypeError, ValueError):
            continue
    return result


def _image_candidates(soup: BeautifulSoup, page_url: str) -> list[str]:
    meta = _meta_values(soup)
    candidates: list[str] = []
    for key in (
        "og:image:secure_url",
        "og:image:url",
        "og:image",
        "twitter:image",
        "twitter:image:src",
        "image",
    ):
        value = meta.get(key)
        if value:
            candidates.append(value)
    for tag in soup.find_all("link", rel=lambda value: value and "image_src" in value):
        href = tag.get("href")
        if href:
            candidates.append(str(href))
    candidates.extend(_json_ld_images(soup))

    seen: set[str] = set()
    result: list[str] = []
    for raw in candidates:
        absolute = urljoin(page_url, raw.strip())
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        result.append(absolute)
    return result


def _download_image(url: str, referer: str) -> Resolved | None:
    headers = dict(IMAGE_HEADERS)
    headers["Referer"] = referer
    try:
        fetched = _fetch(url, max_bytes=config.LINK_MAX_IMAGE_MB * 1024 * 1024, headers=headers)
    except (LinkError, httpx.HTTPError):
        return None
    if fetched.content_type and not fetched.content_type.startswith("image/"):
        return None
    try:
        info = media.inspect_image(fetched.data)
    except media.UnsupportedImage:
        return None
    return Resolved(
        data=fetched.data,
        media_type=info.mime,
        image_url=fetched.url,
        page_url=referer,
        title=None,
    )


def _page_title(soup: BeautifulSoup) -> str | None:
    meta = _meta_values(soup)
    title = meta.get("og:title") or meta.get("twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string
    if not title:
        return None
    return " ".join(str(title).split())[:300]


def _parse_page(html: bytes, page_url: str, title_hint: str | None = None) -> tuple[list[str], str | None]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8", exclude_encodings=["windows-1251"])
    return _image_candidates(soup, page_url), (_page_title(soup) or title_hint)


def _twitter_oembed(page_url: str) -> list[str]:
    endpoint = "https://publish.twitter.com/oembed"
    try:
        with httpx.Client(timeout=config.LINK_TIMEOUT, headers={"User-Agent": BROWSER_UA}) as client:
            response = client.get(endpoint, params={"url": page_url, "omit_script": "1"})
        if response.status_code != 200:
            return []
        payload = response.json()
        html = payload.get("html") or ""
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    return list(dict.fromkeys(PIC_TWITTER_RE.findall(html)))


def _try_candidates(candidates: Iterable[str], page_url: str) -> Resolved | None:
    for candidate in candidates:
        resolved = _download_image(candidate, page_url)
        if resolved is not None:
            return resolved
    return None


def _tweet_reference(url: str) -> tuple[str, int | None] | None:
    match = TWITTER_STATUS_RE.search(url)
    if not match:
        return None
    photo = TWITTER_PHOTO_RE.search(url)
    return match.group(1), int(photo.group(1)) if photo else None


def _x_syndication_media(tweet_id: str) -> tuple[list[str], str | None]:
    """X's public embed payload carries original photo URLs.

    The regular page may be a login wall, and its og:image is a resized WebP.
    This endpoint is used by X embeds and returns the actual media URL.
    """
    try:
        with httpx.Client(timeout=config.LINK_TIMEOUT, headers={"User-Agent": BROWSER_UA}) as client:
            response = client.get(
                X_SYNDICATION_URL,
                params={"id": tweet_id, "lang": "en", "token": "memsave"},
            )
        if response.status_code != 200:
            return [], None
        data = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return [], None
    if not isinstance(data, dict):
        return [], None

    urls: list[str] = []
    for item in data.get("photos") or []:
        if not isinstance(item, dict):
            continue
        for key in ("url", "media_url_https"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
    for item in data.get("mediaDetails") or []:
        if not isinstance(item, dict) or item.get("type") != "photo":
            continue
        value = item.get("media_url_https")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)

    unique: list[str] = []
    for value in urls:
        if value not in unique:
            unique.append(value)

    title = data.get("text")
    if isinstance(title, str):
        title = " ".join(URL_RE.sub("", title).split())[:300] or None
        user = data.get("user")
        if title and isinstance(user, dict) and isinstance(user.get("name"), str):
            title = f"{user['name']}: {title}"[:300]
    else:
        title = None
    return unique, title


def _fxtwitter_media(tweet_id: str) -> tuple[list[str], str | None]:
    """Last-resort X fallback through the public fxtwitter API."""
    if not config.X_FALLBACK:
        return [], None
    try:
        with httpx.Client(timeout=config.LINK_TIMEOUT, headers={"User-Agent": BROWSER_UA}) as client:
            response = client.get(FX_TWITTER_URL.format(tweet_id=tweet_id))
        if response.status_code != 200:
            return [], None
        data = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    tweet = data.get("tweet")
    if not isinstance(tweet, dict):
        return [], None
    media = tweet.get("media")
    if not isinstance(media, dict):
        return [], None

    urls: list[str] = []
    for item in media.get("photos") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(item["url"])
    for item in media.get("all") or []:
        if (
            isinstance(item, dict)
            and item.get("type") == "photo"
            and isinstance(item.get("url"), str)
        ):
            urls.append(item["url"])
    unique: list[str] = []
    for value in urls:
        if value.startswith(("http://", "https://")) and value not in unique:
            unique.append(value)

    title = tweet.get("text")
    if isinstance(title, str):
        title = " ".join(URL_RE.sub("", title).split())[:300] or None
        author = tweet.get("author")
        if title and isinstance(author, dict) and isinstance(author.get("name"), str):
            title = f"{author['name']}: {title}"[:300]
    else:
        title = None
    return unique, title


def resolve(url: str, *, title_hint: str | None = None) -> Resolved:
    url = url.strip()
    if not url:
        raise NoImage("empty link")

    tweet = _tweet_reference(url)
    if tweet is not None:
        tweet_id, photo_index = tweet
        photos, tweet_title = _x_syndication_media(tweet_id)
        if not photos:
            photos, fallback_title = _fxtwitter_media(tweet_id)
            tweet_title = tweet_title or fallback_title
        if photos:
            if photo_index and 1 <= photo_index <= len(photos):
                photos = [photos[photo_index - 1]] + [
                    value for index, value in enumerate(photos, 1) if index != photo_index
                ]
            for candidate in photos:
                resolved = _download_image(candidate, url)
                if resolved is not None:
                    return Resolved(
                        data=resolved.data,
                        media_type=resolved.media_type,
                        image_url=resolved.image_url,
                        page_url=url,
                        title=tweet_title or title_hint,
                    )

    try:
        # A shared URL may point straight at a large image, so the first fetch
        # allows the image cap; ordinary HTML pages are still parsed with the
        # same body limit and a page that big simply has no preview for us.
        page = _fetch(
            url,
            max_bytes=max(
                config.LINK_MAX_HTML_BYTES,
                config.LINK_MAX_IMAGE_MB * 1024 * 1024,
            ),
            headers=REFERER_HEADERS,
        )
    except (LinkError, httpx.HTTPError) as exc:
        raise NoImage(str(exc)) from exc

    looks_html = "html" in page.content_type or page.data.lstrip().startswith(b"<")
    if page.content_type.startswith("image/") or not looks_html:
        try:
            info = media.inspect_image(page.data)
        except media.UnsupportedImage as exc:
            if not looks_html:
                raise NoImage("link is not a page or an image") from exc
        else:
            return Resolved(
                data=page.data,
                media_type=info.mime,
                image_url=page.url,
                page_url=page.url,
                title=title_hint,
            )

    if not looks_html:
        raise NoImage("link is not a page or an image")

    candidates, title = _parse_page(page.data, page.url, title_hint)
    resolved = _try_candidates(candidates, page.url)
    if resolved is not None:
        return Resolved(
            data=resolved.data,
            media_type=resolved.media_type,
            image_url=resolved.image_url,
            page_url=page.url,
            title=title,
        )

    # Some sites only serve previews to crawler user agents.
    try:
        bot_page = _fetch(
            page.url,
            max_bytes=config.LINK_MAX_HTML_BYTES,
            headers={**REFERER_HEADERS, "User-Agent": BOT_UA},
        )
        bot_candidates, bot_title = _parse_page(bot_page.data, bot_page.url, title)
        resolved = _try_candidates(bot_candidates, bot_page.url)
        if resolved is not None:
            return Resolved(
                data=resolved.data,
                media_type=resolved.media_type,
                image_url=resolved.image_url,
                page_url=bot_page.url,
                title=bot_title or title,
            )
    except (LinkError, httpx.HTTPError):
        pass

    host = (urlparse(page.url).hostname or "").lower()
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
        for candidate in _twitter_oembed(page.url):
            resolved = _download_image(candidate, page.url)
            if resolved is not None:
                return Resolved(
                    data=resolved.data,
                    media_type=resolved.media_type,
                    image_url=resolved.image_url,
                    page_url=page.url,
                    title=title,
                )

    raise NoImage("no preview image found")
