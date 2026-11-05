"""Version, commit and release metadata used by /api/version and the header."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import __version__

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPO_URL = "https://github.com/northis/memsave"


def _run_git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _git_dir() -> Path | None:
    dot_git = BASE_DIR / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text.startswith("gitdir:"):
            target = text.split(":", 1)[1].strip()
            path = Path(target)
            if not path.is_absolute():
                path = (BASE_DIR / path).resolve()
            return path if path.is_dir() else None
    return None


def _read_ref(git_dir: Path, ref: str) -> str | None:
    path = git_dir / ref
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        except OSError:
            return None
    return None


def head_commit() -> str | None:
    commit = _run_git("rev-parse", "HEAD")
    if commit:
        return commit
    git_dir = _git_dir()
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        return _read_ref(git_dir, head.split(":", 1)[1].strip())
    return head or None


def commit_date(commit: str | None) -> str | None:
    if not commit:
        return None
    override = os.environ.get("MEMSAVE_COMMIT_DATE")
    if override:
        return override
    return _run_git("show", "-s", "--format=%cI", commit)


def normalize_repo_url(url: str | None) -> str:
    if not url:
        return DEFAULT_REPO_URL
    value = url.strip()
    if value.startswith("git@") and ":" in value:
        host, path = value.split(":", 1)
        host = host.split("@", 1)[-1]
        value = f"https://{host}/{path}"
    elif value.startswith("ssh://"):
        rest = value[len("ssh://") :]
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[1]
        value = f"https://{rest}"
    value = value.removesuffix(".git").rstrip("/")
    if value.startswith("https://"):
        return value
    if value.startswith("http://"):
        return "https://" + value[len("http://") :]
    return DEFAULT_REPO_URL


def repo_url() -> str:
    override = os.environ.get("MEMSAVE_REPO_URL")
    if override:
        return normalize_repo_url(override)
    remote = _run_git("remote", "get-url", "origin")
    return normalize_repo_url(remote)


def dirty() -> bool:
    status = _run_git("status", "--porcelain")
    return bool(status)


def build_info() -> dict[str, object]:
    commit = os.environ.get("MEMSAVE_COMMIT") or head_commit()
    short = commit[:7] if commit else None
    repo = repo_url()
    tag = f"v{__version__}"
    return {
        "version": __version__,
        "commit": commit,
        "short": short,
        "commit_url": f"{repo}/commit/{commit}" if commit else None,
        "commit_date": commit_date(commit),
        "dirty": dirty() if commit else False,
        "repo_url": repo,
        "tag": tag,
        "tag_url": f"{repo}/releases/tag/{tag}",
    }
