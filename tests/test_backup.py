from __future__ import annotations

import zipfile

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
