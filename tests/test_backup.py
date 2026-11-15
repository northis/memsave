from __future__ import annotations

import os
import zipfile
from datetime import datetime, timedelta, timezone

from memsave import backup, config, db


def test_backup_contains_db_images_and_thumbs(paths):
    with db.session() as conn:
        db.insert_image(
            conn,
            sha256="e" * 64,
            display_name="e.jpg",
            original_name=None,
            mime="image/jpeg",
            extension="jpg",
            width=10,
            height=10,
            size_bytes=3,
            source="upload",
        )
    (config.IMAGES_DIR / "e.jpg").write_bytes(b"jpg")
    (config.THUMBS_DIR / "e.webp").write_bytes(b"webp")

    first = backup.run_backup()
    assert first["name"].startswith("memsave_backup_")
    with zipfile.ZipFile(first["path"]) as archive:
        names = set(archive.namelist())
    assert "memsave.db" in names
    assert "images/e.jpg" in names
    assert "thumbs/e.webp" in names

    (config.IMAGES_DIR / "new.jpg").write_bytes(b"new")
    second = backup.run_backup()
    with zipfile.ZipFile(second["path"]) as archive:
        names = set(archive.namelist())
    assert "images/new.jpg" in names
    assert backup.list_backups()[0]["name"] == second["name"]

    full = backup.run_backup(full=True)
    with zipfile.ZipFile(full["path"]) as archive:
        assert "images/e.jpg" in set(archive.namelist())


def test_backup_skips_when_nothing_changed(paths):
    image = config.IMAGES_DIR / "e.jpg"
    image.write_bytes(b"jpg")
    # Older than the backup, so the state's second of slack does not count them as changes.
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    for path in (image, config.DB_PATH):
        os.utime(path, (old, old))

    first = backup.run_backup()
    state = config.BACKUPS_DIR / "backup_state.json"
    saved_state = state.read_text(encoding="utf-8")

    second = backup.run_backup()

    assert first["skipped"] is False
    assert second["skipped"] is True
    assert second["path"] is None
    assert len(list(config.BACKUPS_DIR.glob("*.zip"))) == 1
    assert state.read_text(encoding="utf-8") == saved_state
