"""Pre-commit tripwire: block staged content with the owner's email, user paths or token patterns.

Install: copy `scripts/pre-commit` to `.git/hooks/pre-commit`. It scans staged blobs.
"""

import re
import subprocess
import sys

PATTERNS = [
    ("personal email", re.compile(rb"[A-Za-z0-9._%+-]+@gmail\.com", re.IGNORECASE)),
    ("Windows user path", re.compile(rb"[A-Z]:[\\/]+Users[\\/]+[^\s\"'<>]+", re.IGNORECASE)),
    ("Unix home path", re.compile(rb"[private home path removed]"'<>]+", re.IGNORECASE)),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("Telegram bot token", re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("OpenAI-style key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("JWT", re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
SKIP = {"scripts/pre_commit_secret_guard.py"}


def main() -> int:
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    findings = []
    for raw in filter(None, names):
        path = raw.decode("utf-8", "replace")
        if path in SKIP:
            continue
        blob = subprocess.run(
            ["git", "show", f":{path}"], check=True, stdout=subprocess.PIPE
        ).stdout
        findings += [f"{path}: {label}" for label, rx in PATTERNS if rx.search(blob)]
    if findings:
        print("Commit blocked by secret/privacy guard:", *findings, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
