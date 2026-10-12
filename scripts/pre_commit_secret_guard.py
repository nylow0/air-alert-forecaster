"""Block private staged content and require a publication-safe commit identity.

Install: copy `scripts/pre-commit` to `.git/hooks/pre-commit`. It scans staged blobs.
"""

import re
import subprocess
import sys
import zlib

PATTERNS = [
    (
        "personal email",
        re.compile(
            rb"\b[A-Za-z0-9._%+-]+@(?:gmail|googlemail|yahoo|outlook|hotmail|live|icloud|"
            rb"protonmail)\.com\b|\b[A-Za-z0-9._%+-]+@proton\.me\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Windows user path",
        re.compile(rb"[A-Z]:[\\/]+Users[\\/]+[^\s`\"'<>]+", re.IGNORECASE),
    ),
    (
        "Unix home path",
        re.compile(rb"/(?:[a-z]/Users|Users|home)/[^\s`\"'<>]+", re.IGNORECASE),
    ),
    ("tilde home path", re.compile(rb"(?<![A-Za-z0-9_~])~[\\/]+[^\s`\"'<>]+")),
    (
        "absolute workspace path",
        re.compile(rb"[A-Z]:[\\/]+Business[\\/]+[^\s`\"'<>]+", re.IGNORECASE),
    ),
    (
        "literal process identifier",
        re.compile(
            rb"\b(?:PIDs?|Process[_ ]?IDs?|process(?:\s+(?:IDs?|identifiers?))?)[`\"']?"
            rb"\s*(?:(?:[:=#]|was|were|is|are)\s*)?(?:\[\s*)?"
            rb"[`\"']?\d{2,}(?:\s*[,/]\s*[`\"']?\d{2,})*\b(?:\s*\])?"
            rb"|\b(?:Stop-Process|Get-Process)\s+-Id\s+\d{2,}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("Telegram bot token", re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("OpenAI-style key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("JWT", re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
SAFE_IDENTITY_EMAIL = re.compile(
    rb"(?:[A-Za-z0-9._%+-]+@users\.noreply\.github\.com|airalert@example\.invalid)\Z",
    re.IGNORECASE,
)


def scan_regions(data: bytes) -> list[bytes]:
    """Scan readable binary metadata without treating compressed image bytes as paths."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        regions: list[bytes] = []
        offset = 8
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            kind = data[offset + 4:offset + 8]
            chunk = data[offset + 8:offset + 8 + length]
            if kind == b"tEXt":
                regions.append(chunk)
            elif kind == b"zTXt":
                keyword, compressed = chunk.split(b"\0", 1)
                regions.append(keyword + b"\0" + zlib.decompress(compressed[1:]))
            elif kind == b"iTXt":
                keyword, rest = chunk.split(b"\0", 1)
                _, _, text = rest[2:].split(b"\0", 2)
                regions.append(keyword + b"\0" + (zlib.decompress(text) if rest[0] else text))
            offset += length + 12
        return regions
    if b"\0" in data:
        return re.findall(rb"[\t\r\n\x20-\x7e]{8,}", data)
    return [data]


def find_private_content(data: bytes) -> list[str]:
    """Return categories only; matched values must never appear in hook output."""
    regions = scan_regions(data)
    return [label for label, rx in PATTERNS if any(rx.search(region) for region in regions)]


def check_identity(identity: bytes) -> bool:
    """Allow a GitHub no-reply address or the anonymous project placeholder."""
    match = re.fullmatch(rb"[^<>\r\n]+ <([^<>\s]+)> \d+ [+-]\d{4}\s*", identity)
    return bool(match and SAFE_IDENTITY_EMAIL.fullmatch(match[1]))


def main() -> int:
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    findings: list[str] = []
    for raw in filter(None, names):
        path = raw.decode("utf-8", "replace")
        blob = subprocess.run(
            ["git", "show", f":{path}"], check=True, stdout=subprocess.PIPE
        ).stdout
        # A hostile filename may itself contain private data; print a file count instead.
        categories = set(find_private_content(raw)) | set(find_private_content(blob))
        findings += [f"staged file: {label}" for label in sorted(categories)]
    for role in ("AUTHOR", "COMMITTER"):
        identity = subprocess.run(
            ["git", "var", f"GIT_{role}_IDENT"], capture_output=True
        )
        if identity.returncode or not check_identity(identity.stdout):
            findings.append(f"{role.lower()} identity: use a no-reply or anonymous project email")
    if findings:
        print("Commit blocked by secret/privacy guard:", *findings, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
