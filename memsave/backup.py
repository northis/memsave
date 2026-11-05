"""Incremental zip backups of the database and image folders."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

STATE_NAME = "backup_state.json"


def _paths() -> dict[str, Path]:
    return {
        "data": config.DATA_DIR,
        "db": config.DB_PATH,
        "images": config.IMAGES_DIR,
        "thumbs": config.THUMBS_DIR,
        "backups": config.BACKUPS_DIR,
        "state": config.BACKUPS_DIR / STATE_NAME,
    }


def _read_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _snapshot_db(db_path: Path, destination: Path) -> None:
    source = sqlite3.connect(str(db_path), timeout=10)
    try:
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _files_since(directory: Path, last_ts: float, *, full: bool) -> list[Path]:
    if not directory.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.endswith(".part"):
            continue
        try:
            if full or path.stat().st_mtime > last_ts:
                result.append(path)
        except OSError:
            continue
    return result


def _backup_name(full: bool) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "memsave_full" if full else "memsave_backup"
    candidate = f"{prefix}_{stamp}.zip"
    path = config.BACKUPS_DIR / candidate
    index = 2
    while path.exists():
        candidate = f"{prefix}_{stamp}-{index}.zip"
        path = config.BACKUPS_DIR / candidate
        index += 1
    return candidate


def run_backup(*, full: bool = False) -> dict[str, Any]:
    paths = _paths()
    config.ensure_dirs()
    state = _read_state(paths["state"])
    last_backup_utc = state.get("last_backup_utc")
    last_ts = 0.0
    if last_backup_utc and not full:
        try:
            last_ts = datetime.fromisoformat(str(last_backup_utc)).timestamp()
        except ValueError:
            last_ts = 0.0

    name = _backup_name(full)
    target = paths["backups"] / name
    part = target.with_name(target.name + ".part")
    images = _files_since(paths["images"], last_ts, full=full)
    thumbs = _files_since(paths["thumbs"], last_ts, full=full)

    snapshot_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="memsave_db_", suffix=".sqlite", dir=paths["backups"], delete=False
        ) as handle:
            snapshot_path = Path(handle.name)
        _snapshot_db(paths["db"], snapshot_path)

        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.write(snapshot_path, arcname="memsave.db")
            for path in images:
                archive.write(path, arcname=f"images/{path.name}", compress_type=zipfile.ZIP_STORED)
            for path in thumbs:
                archive.write(path, arcname=f"thumbs/{path.name}", compress_type=zipfile.ZIP_STORED)
        os.replace(part, target)
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if snapshot_path is not None:
            try:
                snapshot_path.unlink(missing_ok=True)
            except OSError:
                pass

    finished = _iso_now()
    _write_state(
        paths["state"],
        {
            "last_backup_utc": finished,
            "last_backup_zip": name,
            "last_full": full,
        },
    )
    return {
        "path": str(target),
        "name": name,
        "full": full,
        "images": len(images),
        "thumbs": len(thumbs),
        "previous_backup_utc": last_backup_utc,
        "backup_utc": finished,
        "size_bytes": target.stat().st_size,
    }


def list_backups() -> list[dict[str, Any]]:
    config.ensure_dirs()
    result: list[dict[str, Any]] = []
    for path in sorted(config.BACKUPS_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        result.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            }
        )
    return result


def main() -> None:
    result = run_backup()
    print(f"backup: {result['path']}")
    print(f"images: {result['images']}, thumbs: {result['thumbs']}, size: {result['size_bytes']} bytes")
    print(f"previous: {result['previous_backup_utc'] or 'never'}")
    print(f"now: {result['backup_utc']}")


if __name__ == "__main__":
    main()
