"""Atomic local publications and credential-free raw response caching."""

import gzip
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_json(path: Path, payload: object) -> None:
    atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def cache_response(path: Path, payload: bytes) -> None:
    atomic_bytes(path, gzip.compress(payload, mtime=0))
