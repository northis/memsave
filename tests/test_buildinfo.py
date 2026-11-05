from __future__ import annotations

from memsave import buildinfo


def test_build_info_shape():
    info = buildinfo.build_info()
    assert info["version"] == "1.0.1"
    assert info["tag"] == "v1.0.1"
    assert str(info["tag_url"]).endswith("/releases/tag/v1.0.1")


def test_repo_url_normalization():
    assert buildinfo.normalize_repo_url("git@github.com:northis/memsave.git") == "https://github.com/northis/memsave"
    assert buildinfo.normalize_repo_url("ssh://git@github.com/northis/memsave.git") == "https://github.com/northis/memsave"
    assert buildinfo.normalize_repo_url(None).startswith("https://")


