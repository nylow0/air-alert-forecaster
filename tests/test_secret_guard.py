"""Exercise the staged privacy gate with synthetic values and real Git index reads."""

import importlib.util
import os
import subprocess
import sys
import zlib
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "pre_commit_secret_guard.py"
SPEC = importlib.util.spec_from_file_location("secret_guard", GUARD)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_private_categories_keep_public_examples() -> None:
    """Real-value shapes are caught without blocking URLs or runtime PID code."""
    private = [
        b"C:" + b"/" + b"Users/" + b"fictional/.config",
        b"/home/" + b"fictional/project",
        b"~" + b"/.config",
        b"PID" + b" = 456789",
        b"process" + b" 456789 remained",
        b"Finished server process" + b" [456789]",
        b"ProcessId" + b": 456789",
        b"PIDs" + b" [456789, 567890]",
        b"person@" + b"gmail.com",
        b"sk-" + b"x" * 28,
    ]
    for value in private:
        assert guard.find_private_content(value)
    assert not guard.find_private_content(
        b"https://api.alerts.in.ua/api/v3; airalert@example.invalid; "
        b"process.pid; PID = child.pid; ProcessId = child.pid; os.getpid(); "
        b"process exited with code 1; 2026-10-12; 168 h; "
        b"GIT_AUTHOR_EMAIL; ALERTS_IN_UA_TOKEN=; /api/raion/kyiv-city/history"
    )
    assert guard.check_identity(b"Contributor <airalert@example.invalid> 1 +0000")
    assert guard.check_identity(b"Contributor <123+contributor@users.noreply.github.com> 1 +0000")
    assert not guard.check_identity(b"Contributor <person@" + b"gmail.com> 1 +0000")
    assert not guard.find_private_content(b"\x89PNG\0\0" + b"~" + b"/\x80\x81\x82\x83")
    assert guard.find_private_content(b"\x89PNG\0\0" + b"sk-" + b"x" * 28 + b"\0")


def test_guard_reads_staged_blob_and_never_prints_private_values(tmp_path: Path) -> None:
    """An unstaged edit cannot hide a staged secret, and rejection logs stay private."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    value = b"sk-" + b"x" * 28
    path = tmp_path / "sample.txt"
    path.write_bytes(value)
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    path.write_text("safe unstaged content", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Contributor",
        "GIT_COMMITTER_NAME": "Contributor",
        "GIT_AUTHOR_EMAIL": "airalert@example.invalid",
        "GIT_COMMITTER_EMAIL": "airalert@example.invalid",
    }
    blocked = subprocess.run(
        [sys.executable, str(GUARD)], cwd=tmp_path, env=env, capture_output=True, check=False
    )
    assert blocked.returncode == 1
    assert b"OpenAI-style key" in blocked.stderr
    assert value not in blocked.stdout + blocked.stderr
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    allowed = subprocess.run(
        [sys.executable, str(GUARD)], cwd=tmp_path, env=env, capture_output=True, check=False
    )
    assert allowed.returncode == 0
    # The real map contains compressed byte sequences that resemble short home paths.
    image = (GUARD.parents[1] / "docs" / "geo_map.png").read_bytes()
    figure = tmp_path / "figure.png"
    figure.write_bytes(image)
    subprocess.run(["git", "add", "figure.png"], cwd=tmp_path, check=True)
    safe_image = subprocess.run(
        [sys.executable, str(GUARD)], cwd=tmp_path, env=env, capture_output=True, check=False
    )
    assert safe_image.returncode == 0
    text = b"Comment\0" + value
    chunk = b"tEXt" + text
    metadata_chunk = len(text).to_bytes(4) + chunk + zlib.crc32(chunk).to_bytes(4)
    figure.write_bytes(image[:-12] + metadata_chunk + image[-12:])
    subprocess.run(["git", "add", "figure.png"], cwd=tmp_path, check=True)
    private_image = subprocess.run(
        [sys.executable, str(GUARD)], cwd=tmp_path, env=env, capture_output=True, check=False
    )
    assert private_image.returncode == 1
    assert b"OpenAI-style key" in private_image.stderr
    assert value not in private_image.stdout + private_image.stderr
    figure.write_bytes(image)
    subprocess.run(["git", "add", "figure.png"], cwd=tmp_path, check=True)
    env["GIT_AUTHOR_EMAIL"] = "person@" + "gmail.com"
    metadata = subprocess.run(
        [sys.executable, str(GUARD)], cwd=tmp_path, env=env, capture_output=True, check=False
    )
    assert metadata.returncode == 1
    assert b"author identity" in metadata.stderr
    assert env["GIT_AUTHOR_EMAIL"].encode() not in metadata.stdout + metadata.stderr
