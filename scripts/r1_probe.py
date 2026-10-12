"""Read public Telegram previews at a polite rate; emit evidence to stdout only.

Run with uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py.
This research probe neither authenticates nor writes downloads into the workspace.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import sys
import time
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser


@dataclass(frozen=True)
class Message:
    id: int
    date_utc: str
    edited: bool
    text: str


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    seconds: float
    title: str
    description: str
    older_cursor: str | None
    messages: list[Message]


class Preview:
    """Space all request starts by at least 1.05 seconds and stop on HTTP errors."""

    def __init__(self) -> None:
        self.client = httpx.Client(follow_redirects=True, timeout=45)
        self.last_request = 0.0
        self.pages: list[Page] = []

    def get(self, channel: str, before: int | None = None, search: str | None = None) -> Page:
        delay = 1.05 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        url = f"https://t.me/s/{channel}"
        params: dict[str, str] = {}
        if before is not None:
            params["before"] = str(before)
        if search is not None:
            params["q"] = search
        self.last_request = time.monotonic()
        started = time.perf_counter()
        response = self.client.get(url, params=params)
        seconds = time.perf_counter() - started
        response.raise_for_status()
        html = LexborHTMLParser(response.text)
        messages: list[Message] = []
        for node in html.css(".tgme_widget_message"):
            post = node.attributes.get("data-post", "")
            timestamp = node.css_first(".tgme_widget_message_date time")
            if not post or timestamp is None:
                continue
            raw_date = timestamp.attributes.get("datetime")
            if raw_date is None:
                raise ValueError(f"Missing publish timestamp in {post}")
            published = datetime.fromisoformat(raw_date).astimezone(UTC)
            text_node = node.css_first(".tgme_widget_message_text")
            if text_node is not None:
                # Preserve line breaks without inserting spaces inside links or emoji.
                for br in text_node.css("br"):
                    br.replace_with("\n")
                content = text_node.text(strip=False).strip()
            else:
                content = ""
            metadata = node.css_first(".tgme_widget_message_meta")
            edited = metadata is not None and "edited" in metadata.text().lower()
            messages.append(
                Message(
                    int(post.rsplit("/", 1)[1]),
                    published.isoformat().replace("+00:00", "Z"),
                    edited,
                    content,
                )
            )
        title = html.css_first(".tgme_channel_info_header_title") or html.css_first("title")
        description = html.css_first(".tgme_channel_info_description")
        older = html.css_first("a.tme_messages_more[data-before]")
        page = Page(
            str(response.url), response.status_code, round(seconds, 3),
            title.text(strip=True) if title is not None else "",
            description.text(separator=" ", strip=True) if description is not None else "",
            older.attributes.get("data-before") if older is not None else None,
            sorted(messages, key=lambda message: message.id),
        )
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.client.close()


def summary(page: Page) -> dict[str, object]:
    """Keep page timings and boundary IDs so reach claims remain auditable."""
    return {
        "url": page.url, "status": page.status, "seconds": page.seconds,
        "title": page.title, "description": page.description,
        "older_cursor": page.older_cursor, "count": len(page.messages),
        "first": asdict(page.messages[0]) if page.messages else None,
        "last": asdict(page.messages[-1]) if page.messages else None,
        "edited_count": sum(message.edited for message in page.messages),
    }


def find_day(preview: Preview, channel: str, target: date) -> list[Message]:
    """Locate a UTC day by bounded ID bisection, then page across both boundaries."""
    latest = preview.get(channel)
    if not latest.messages:
        raise ValueError(f"No public preview for {channel}")
    if target > date.fromisoformat(latest.messages[-1].date_utc[:10]):
        raise ValueError("Requested day is newer than the newest visible message")
    low = 1
    high = latest.messages[-1].id + 1
    cursor = high
    for _ in range(18):
        middle = (low + high) // 2
        page = preview.get(channel, middle)
        if not page.messages or date.fromisoformat(page.messages[-1].date_utc[:10]) < target:
            low = middle + 1
        elif date.fromisoformat(page.messages[0].date_utc[:10]) > target:
            high = middle
        else:
            cursor = page.messages[-1].id + 1
            break
        if high <= low:
            cursor = high
            break
    else:
        raise RuntimeError("Day search exceeded its 18-request limit")
    # First walk forward until the visible page includes the following day.
    for _ in range(120):
        page = preview.get(channel, cursor + 20)
        if page.messages and date.fromisoformat(page.messages[-1].date_utc[:10]) > target:
            cursor = page.messages[-1].id + 1
            break
        cursor += 20
    else:
        raise RuntimeError("Forward day boundary was not found")
    result: dict[int, Message] = {}
    for _ in range(120):
        page = preview.get(channel, cursor)
        if not page.messages:
            break
        for message in page.messages:
            if date.fromisoformat(message.date_utc[:10]) == target:
                result[message.id] = message
        if date.fromisoformat(page.messages[0].date_utc[:10]) < target:
            break
        next_cursor = page.messages[0].id
        if next_cursor >= cursor:
            raise RuntimeError("Telegram preview cursor did not move backward")
        cursor = next_cursor
    else:
        raise RuntimeError("Backward day boundary was not found")
    if not result:
        raise ValueError("No messages found on the requested day")
    return sorted(result.values(), key=lambda message: (message.date_utc, message.id))


def utc_datetime(value: str) -> datetime:
    """Parse the CSV's UTC times, retaining explicit offsets when supplied."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def align(
    sample: Path, csv_path: Path | None, oblasts: list[str], ids: set[int]
) -> dict[str, object]:
    """Compare named-place posts to raw starts and active intervals, without scoring models."""
    messages = [
        Message(**json.loads(line)) for line in sample.read_text(encoding="utf-8").splitlines()
    ]
    if csv_path is None:
        response = httpx.get(
            "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/master/datasets/official_data_en.csv",
            timeout=120, follow_redirects=True,
        )
        response.raise_for_status()
        raw = response.content
    else:
        raw = csv_path.read_bytes()
    start_window = datetime.fromisoformat(messages[0].date_utc) - timedelta(days=2)
    end_window = datetime.fromisoformat(messages[-1].date_utc) + timedelta(days=1)
    rows: list[tuple[datetime, datetime | None, dict[str, str]]] = []
    max_started: datetime | None = None
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        started = utc_datetime(row["started_at"])
        finished = utc_datetime(row["finished_at"]) if row["finished_at"] else None
        max_started = max(max_started, started) if max_started else started
        if started <= end_window and (finished is None or finished >= start_window):
            rows.append((started, finished, row))
    by_oblast = {
        oblast: sorted(
            [item for item in rows if item[2]["oblast"] == oblast], key=lambda item: item[0]
        )
        for oblast in oblasts or sorted({item[2]["oblast"] for item in rows})
    }
    events: list[dict[str, object]] = []
    for message in messages:
        if ids and message.id not in ids:
            continue
        published = utc_datetime(message.date_utc)
        regions: dict[str, object] = {}
        for oblast, intervals in by_oblast.items():
            active = [
                row for start, end, row in intervals
                if start < published and (end is None or end > published)
            ]
            position = bisect_right([item[0] for item in intervals], published)
            next_item = intervals[position] if position < len(intervals) else None
            regions[oblast] = {
                "active_raw_records": len(active),
                "active_records": active,
                "next_start": next_item[2] if next_item else None,
                "minutes_until_next_raw_start": (
                    round((next_item[0] - published).total_seconds() / 60, 3)
                    if next_item else None
                ),
            }
        events.append({"message": asdict(message), "regions": regions})
    return {
        "csv_sha256": hashlib.sha256(raw).hexdigest(), "csv_bytes": len(raw),
        "csv_max_started_at": max_started.isoformat() if max_started else None,
        "events": events,
        "method": (
            "First strictly later raw start in each selected region; "
            "report already-active records separately. These are timing examples, "
            "not model scores or a typical lead-time estimate."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pages", "day", "align", "validate"])
    parser.add_argument("--channel", default="kpszsu")
    parser.add_argument("--before", type=int, action="append")
    parser.add_argument("--sequential", type=int, default=0)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--oblast", action="append", default=[])
    parser.add_argument("--ids", default="")
    parser.add_argument("--search")
    parser.add_argument("--include-messages", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.mode in {"align", "validate"}:
        if args.sample is None:
            parser.error("--sample is required")
        if args.mode == "align":
            output = align(
                args.sample, args.csv, args.oblast,
                {int(value) for value in args.ids.split(",") if value},
            )
        else:
            lines = args.sample.read_text(encoding="utf-8").splitlines()
            messages = [Message(**json.loads(line)) for line in lines]
            assert len({message.id for message in messages}) == len(messages)
            assert messages == sorted(messages, key=lambda message: (message.date_utc, message.id))
            assert all(message.date_utc.endswith("Z") for message in messages)
            assert all(
                set(json.loads(line)) == {"id", "date_utc", "edited", "text"} for line in lines
            )
            assert all(isinstance(message.edited, bool) for message in messages)
            assert all(isinstance(message.text, str) for message in messages)
            assert all(utc_datetime(message.date_utc).tzinfo == UTC for message in messages)
            days = sorted({message.date_utc[:10] for message in messages})
            assert len(days) == 1
            output = {
                "messages": len(messages), "days": days,
                "edited": sum(message.edited for message in messages),
                "first": messages[0].date_utc, "last": messages[-1].date_utc,
                "first_id": messages[0].id, "last_id": messages[-1].id,
                "id_gaps": messages[-1].id - messages[0].id + 1 - len(messages),
            }
        print(json.dumps(output, ensure_ascii=False))
        return
    preview = Preview()
    try:
        messages: list[Message] = []
        if args.mode == "day":
            if args.date is None:
                parser.error("--date is required")
            messages = find_day(preview, args.channel, args.date)
        else:
            page = preview.get(args.channel, search=args.search)
            for _ in range(args.sequential):
                if not page.messages:
                    break
                page = preview.get(args.channel, page.messages[0].id, args.search)
            for before in args.before or []:
                preview.get(args.channel, before, args.search)
            messages = list({
                message.id: message for page in preview.pages for message in page.messages
            }.values())
        output = {
            "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "channel": args.channel, "pages": [summary(page) for page in preview.pages],
            "median_seconds": round(statistics.median(page.seconds for page in preview.pages), 3),
            "message_count": len(messages),
        }
        if args.include_messages or args.mode == "day":
            output["messages"] = [asdict(message) for message in messages]
        print(json.dumps(output, ensure_ascii=False))
    finally:
        preview.close()


if __name__ == "__main__":
    main()
