"""Wave-0 dated dataset fetcher; A1 adds record expansion and episode construction."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx

from airalert.config import OFFICIAL_CSV_URL, RAW_CSV_TEMPLATE, RAW_DIR


def fetch() -> Path:
    """Reuse today's UTC copy and verify its hash; publish downloads only when complete."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / RAW_CSV_TEMPLATE.format(date=datetime.now(UTC).date().isoformat())
    checksum_path = path.with_suffix(".csv.sha256")
    reused = path.exists()
    if not reused:
        temporary_path = path.with_suffix(".csv.part")
        try:
            with httpx.stream(
                "GET", OFFICIAL_CSV_URL, follow_redirects=True, timeout=120
            ) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if checksum_path.exists():
        expected = checksum_path.read_text(encoding="ascii").split()[0]
        if expected != digest:
            raise ValueError(f"SHA256 mismatch for {path}; preserve the copy for inspection")
    else:
        checksum_path.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    print(f"{'Reused' if reused else 'Saved'} {path.relative_to(RAW_DIR.parent.parent)}")
    print(f"SHA256 {digest}")
    return path
