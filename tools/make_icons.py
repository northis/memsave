"""Render the committed PNG icons from tools/icon.svg.

Run by hand on a machine with Chrome installed:

    python tools/make_icons.py

The generated PNGs are committed; the Docker image never needs this script.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "icon.svg"
STATIC = ROOT / "memsave" / "server" / "static"
ICONS = STATIC / "icons"
EXTENSION = ROOT / "extension"

PNG_TARGETS = [
    (ICONS / "icon-192.png", 192, False),
    (ICONS / "icon-512.png", 512, False),
    (ICONS / "maskable-512.png", 512, True),
    (ICONS / "apple-touch-icon-180.png", 180, False),
    (EXTENSION / "icon16.png", 16, False),
    (EXTENSION / "icon32.png", 32, False),
    (EXTENSION / "icon48.png", 48, False),
    (EXTENSION / "icon128.png", 128, False),
]


def html_for(svg: str, size: int, maskable: bool) -> str:
    if maskable:
        svg = svg.replace('rx="230"', 'rx="0"', 1)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;background:transparent}"
        f"svg{{display:block;width:{size}px;height:{size}px}}</style></head>"
        f"<body>{svg}</body></html>"
    )


def render(page, path: Path, size: int, svg: str, maskable: bool) -> None:
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(html_for(svg, size, maskable))
    # Locator screenshots composite a white page behind transparent SVGs. A page
    # screenshot with omit_background keeps the alpha channel of the source.
    page.screenshot(path=str(path), omit_background=True)
    print(f"{path.relative_to(ROOT)} {size}x{size}")


def main() -> None:
    svg = SOURCE.read_text(encoding="utf-8")
    ICONS.mkdir(parents=True, exist_ok=True)
    EXTENSION.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, STATIC / "favicon.svg")
    shutil.copyfile(SOURCE, EXTENSION / "icon.svg")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(device_scale_factor=1)
        for path, size, maskable in PNG_TARGETS:
            render(page, path, size, svg, maskable)
        browser.close()


if __name__ == "__main__":
    main()
