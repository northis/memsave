from __future__ import annotations

import json
from pathlib import Path

from memsave import __version__


ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"


def test_manifest_version_matches_application():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == __version__
    assert "contextMenus" in manifest["permissions"]
    assert "storage" in manifest["permissions"]
    assert "http://*/*" in manifest["optional_host_permissions"]
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["options_page"] == "options.html"


def test_context_menu_is_registered_and_options_exist():
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert "contextMenus.create" in background
    assert "Отправить в memsave" in background
    assert "X-Memsave-Request" in background
    assert (EXTENSION / "options.html").is_file()
    assert (EXTENSION / "options.js").is_file()
    for name in ("icon16.png", "icon32.png", "icon48.png", "icon128.png"):
        assert (EXTENSION / name).is_file()


def test_pwa_manifest_has_files_share_target():
    manifest = json.loads(
        (ROOT / "memsave" / "server" / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
    )
    target = manifest["share_target"]
    assert target["action"] == "/share"
    assert target["method"] == "POST"
    assert target["params"]["files"][0]["name"] == "files"
    assert "image/*" in target["params"]["files"][0]["accept"]


def test_update_script_has_rollback_and_build_metadata():
    script = (ROOT / "deploy" / "windows" / "update.ps1").read_text(encoding="utf-8")
    assert "MEMSAVE_COMMIT" in script
    assert "memsave:previous" in script
    assert "Rollback" in script
