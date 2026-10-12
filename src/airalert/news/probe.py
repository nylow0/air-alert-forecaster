"""One authorized real pipeline check; opt-in exists only in a temporary settings copy.

Run explicitly with `python -m airalert.news.probe`. It never logs in or logs out,
never changes normal settings and stores sanitized evidence under data/news/.
"""

import argparse
import asyncio
import csv
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from airalert.config import DATA_DIR, RAIONS_PATH
from airalert.news.auth import Auth
from airalert.news.settings import SettingsStore, default_settings
from airalert.news.store import Store
from airalert.news.worker import Worker
from airalert.schemas import ForecastResponse, ForecastRow


def synthetic_base() -> ForecastResponse:
    with RAIONS_PATH.open(encoding="utf-8", newline="") as stream:
        ids = [row["raion_id"] for row in csv.DictReader(stream)]
    at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return ForecastResponse(
        at=at,
        mode="latest",
        onset_model="synthetic-news-check",
        duration_model=None,
        rows=[
            ForecastRow(
                raion_id=rid,
                p_1=0.09,
                p_3=0.23,
                p_12=0.55,
                active=False,
                active_since=None,
                end_p10=None,
                end_p50=None,
                end_p90=None,
                actual_onset_1=None,
                actual_onset_3=None,
                actual_onset_12=None,
            )
            for rid in ids
        ],
    )


async def run(resume: str | None = None) -> dict[str, object]:
    auth = Auth()
    status = await asyncio.to_thread(auth.status, True)
    if not status.logged_in:
        raise RuntimeError("Existing Codex login is required; probe does not perform login")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = DATA_DIR / "news" / f"e2e-{stamp}"
    if resume:
        folder = (DATA_DIR / "news" / resume).resolve()
        if folder.parent != (DATA_DIR / "news").resolve() or not (folder / "news.sqlite").exists():
            raise ValueError("Resume must name an existing data/news probe directory")
    archive = Store(folder / "news.sqlite")
    with tempfile.TemporaryDirectory(prefix="airalert-news-settings-") as scratch:
        settings = SettingsStore(Path(scratch) / "settings.json")
        value = default_settings()
        value.news_enabled = True
        settings.save(value, auth.models)
        worker = Worker(settings=settings, store=archive, auth=auth)
        worker.base_provider = synthetic_base
        started = time.perf_counter()
        try:
            await worker.cycle(
                bootstrap_only=True, skip_poll=resume is not None, skip_extract=resume is not None
            )
            elapsed = time.perf_counter() - started
            feed = worker.feed()
            channels = [archive.channel(c.handle) for c in value.channels]
            all_incidents = archive.incidents()
            report: dict[str, object] = {
                "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "synthetic_base": True,
                "normal_settings_changed": False,
                "login_or_logout_performed": False,
                "wall_seconds": round(elapsed, 3),
                "channels_requested": len(channels),
                "channels_successful": sum(
                    c["last_poll"] is not None and not c["failures"] for c in channels
                ),
                "message_versions": archive.db.execute("SELECT COUNT(*) FROM messages").fetchone()[
                    0
                ],
                "measurements": worker.last_measurements,
                "validated_incidents": [i.event.model_dump(mode="json") for i in all_incidents],
                "feed": feed.model_dump(mode="json"),
                "daily_usage": archive.usage(datetime.now(UTC)),
            }
            report_name = "report-retry.json" if resume else "report.json"
            (folder / report_name).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return report
        finally:
            await worker.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume")
    report = asyncio.run(run(parser.parse_args().resume))
    # Only public contract data and counters. No subprocess stdout, paths or local process IDs.
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"feed", "validated_incidents"}},
            ensure_ascii=True,
        )
    )
    print(
        json.dumps(
            {
                "status": report["feed"]["status"],
                "validated_incidents": len(report["validated_incidents"]),
                "adjustments": len(report["feed"]["adjustments"]),
            }
        )
    )
