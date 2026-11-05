from __future__ import annotations

from pathlib import Path


def test_update_script_passes_docker_arguments_explicitly():
    script = (Path(__file__).resolve().parent.parent / "deploy" / "windows" / "update.ps1").read_text(
        encoding="utf-8"
    )
    # Windows PowerShell 5.1 flattens a @(...) invocation of a
    # ValueFromRemainingArguments parameter into one string, so `docker tag`
    # becomes `docker "tag image:latest image:previous"`. Keep explicit arrays.
    assert "ValueFromRemainingArguments" not in script
    assert "Invoke-Docker -DockerArgs" in script
    assert "Compose-Exec -DockerArgs" in script
    assert "Invoke-Docker @(" not in script
