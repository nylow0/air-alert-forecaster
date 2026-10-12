"""Canonical dataset identifiers; geography must match these, never rename them."""

import re
import unicodedata


def slug(value: str) -> str:
    """Normalize English dataset names, dropping apostrophes before separator folding."""
    value = value.translate(str.maketrans("", "", "'’ʼ`"))
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    if not result:
        raise ValueError(f"Name has no ASCII identifier: {value!r}")
    return result


def oblast_id(oblast_name: str) -> str:
    """Strip the administrative suffix before forming an oblast identifier."""
    return slug(re.sub(r" oblast$", "", oblast_name.strip(), flags=re.IGNORECASE))


def raion_id(oblast_name: str, raion_name: str | None) -> str:
    """Use a missing raion only for a special-status city, after checking the universe."""
    region = oblast_id(oblast_name)
    district = (
        slug(re.sub(r" raion$", "", raion_name.strip(), flags=re.IGNORECASE))
        if raion_name and raion_name.strip()
        else region
    )
    return f"{region}/{district}"
