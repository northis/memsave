"""Bump the single application version in the package and the extension."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "memsave" / "__init__.py"
MANIFEST = ROOT / "extension" / "manifest.json"
VERSION_RE = re.compile(r'(__version__\s*=\s*["\'])([^"\']+)(["\'])')
JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')


def read_version() -> str:
    match = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"could not find __version__ in {INIT}")
    return match.group(2)


def write_version(version: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    text, count = VERSION_RE.subn(lambda match: match.group(1) + version + match.group(3), text, count=1)
    if count != 1:
        raise SystemExit(f"could not replace __version__ in {INIT}")
    INIT.write_text(text, encoding="utf-8")

    manifest_text = MANIFEST.read_text(encoding="utf-8")
    manifest_text, count = JSON_VERSION_RE.subn(
        lambda match: match.group(1) + version + match.group(3), manifest_text, count=1
    )
    if count != 1:
        raise SystemExit(f"could not replace version in {MANIFEST}")
    MANIFEST.write_text(manifest_text, encoding="utf-8")


def check() -> int:
    version = read_version()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        print(
            f"version mismatch: memsave/__init__.py={version}, extension/manifest.json={manifest.get('version')}",
            file=sys.stderr,
        )
        return 1
    print(f"version ok: {version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="new version, for example 1.1.0")
    parser.add_argument("--check", action="store_true", help="only verify that both versions match")
    args = parser.parse_args()
    if args.check:
        return check()
    if not args.version:
        parser.print_help()
        return 1
    version = args.version.strip().lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?", version):
        print(f"invalid version: {version}", file=sys.stderr)
        return 1
    write_version(version)
    print(f"version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
