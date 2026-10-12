"""SQLite archive. Messages and incident revisions are immutable availability records."""

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airalert.news.policy import NEWS_DB, TOKEN_RESERVE
from airalert.schemas import NewsEvent


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("News timestamps must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Message:
    handle: str
    message_id: int
    published_at: datetime
    observed_at: datetime
    text: str
    edited: bool = False
    version_id: int = 0

    @property
    def source_key(self) -> str:
        return f"{self.handle}/{self.message_id}@{self.version_id}"

    @property
    def url(self) -> str:
        return f"https://t.me/{self.handle}/{self.message_id}"


@dataclass(frozen=True)
class Incident:
    event: NewsEvent
    version_ids: tuple[int, ...]
    extraction_finished: datetime
    revision: int = 0

    def usable(self, t: datetime) -> bool:
        return (
            self.event.published_at < t
            and self.event.observed_at < t
            and self.extraction_finished < t
            and t < self.event.expires_at
            and self.event.status != "cleared"
        )


class Store:
    def __init__(self, path: Path = NEWS_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS channels (
                handle TEXT PRIMARY KEY, cursor INTEGER, failures INTEGER DEFAULT 0,
                retry_at TEXT, paused INTEGER DEFAULT 0, last_overlap TEXT, last_poll TEXT,
                lagging INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages (
                version_id INTEGER PRIMARY KEY, handle TEXT, message_id INTEGER,
                published_at TEXT, observed_at TEXT, first_observed_at TEXT,
                text TEXT, text_hash TEXT, edited INTEGER, processed INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS message_identity ON messages(handle, message_id, version_id);
            CREATE INDEX IF NOT EXISTS message_hash ON messages(text_hash, published_at);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, stage TEXT, started_at TEXT, finished_at TEXT,
                model TEXT, effort TEXT, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, state TEXT, version_ids TEXT);
            CREATE TABLE IF NOT EXISTS incidents (
                revision INTEGER PRIMARY KEY, id TEXT, event TEXT, version_ids TEXT,
                extraction_finished TEXT);
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY, fingerprint TEXT, finished_at TEXT, effects TEXT);
            CREATE TABLE IF NOT EXISTS evaluation_models (
                evaluation_id INTEGER PRIMARY KEY, models TEXT);
            CREATE TABLE IF NOT EXISTS adjustments (
                id INTEGER PRIMARY KEY, base_at TEXT, computed_at TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS run_outputs (run_id INTEGER PRIMARY KEY, output TEXT);
            CREATE TABLE IF NOT EXISTS evidence (
                run_id INTEGER, version_id INTEGER, quote TEXT,
                PRIMARY KEY(run_id,version_id,quote));
        """)
        self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def channel(self, handle: str) -> dict[str, object]:
        with self._lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO channels(handle) VALUES (?)", (handle,))
            row = self.db.execute("SELECT * FROM channels WHERE handle=?", (handle,)).fetchone()
            return dict(row)

    def channel_update(self, handle: str, **values: object) -> None:
        allowed = {"failures", "retry_at", "paused", "last_overlap", "last_poll", "lagging"}
        if not values.keys() <= allowed:
            raise ValueError("Unknown channel state")
        with self._lock, self.db:
            self.channel(handle)
            for key, value in values.items():
                self.db.execute(f"UPDATE channels SET {key}=? WHERE handle=?", (value, handle))

    def ingest(self, handle: str, messages: list[Message], cursor: int | None) -> list[Message]:
        result = []
        with self._lock, self.db:
            self.channel(handle)
            for message in messages:
                last = self.db.execute(
                    "SELECT * FROM messages WHERE handle=? AND message_id=? "
                    "ORDER BY version_id DESC LIMIT 1",
                    (handle, message.message_id),
                ).fetchone()
                digest = hashlib.sha256(message.text.encode("utf-8")).hexdigest()
                if last is not None and last["text_hash"] == digest:
                    continue
                first = last["first_observed_at"] if last else iso(message.observed_at)
                row = self.db.execute(
                    "INSERT INTO messages(handle,message_id,published_at,observed_at,"
                    "first_observed_at,"
                    "text,text_hash,edited) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        handle,
                        message.message_id,
                        iso(message.published_at),
                        iso(message.observed_at),
                        first,
                        message.text,
                        digest,
                        message.edited or last is not None,
                    ),
                )
                result.append(
                    Message(
                        handle,
                        message.message_id,
                        message.published_at,
                        message.observed_at,
                        message.text,
                        message.edited or last is not None,
                        int(row.lastrowid),
                    )
                )
            if cursor is not None:
                self.db.execute(
                    "UPDATE channels SET cursor=MAX(COALESCE(cursor,0),?) WHERE handle=?",
                    (cursor, handle),
                )
        return result

    def pending(self, enabled: set[str]) -> list[Message]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE processed=0 ORDER BY version_id"
            ).fetchall()
        return [
            Message(
                r["handle"],
                r["message_id"],
                datetime.fromisoformat(r["published_at"]),
                datetime.fromisoformat(r["observed_at"]),
                r["text"],
                bool(r["edited"]),
                r["version_id"],
            )
            for r in rows
            if r["handle"] in enabled
        ]

    def processed(self, messages: list[Message]) -> None:
        with self._lock, self.db:
            self.db.executemany(
                "UPDATE messages SET processed=1 WHERE version_id=?",
                [(m.version_id,) for m in messages],
            )

    def repeated(self, message: Message) -> bool:
        """Identical recent reports cannot refresh an incident or become corroboration."""
        digest = hashlib.sha256(message.text.encode("utf-8")).hexdigest()
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM messages WHERE text_hash=? AND version_id<? "
                "AND published_at>=? AND published_at<=? LIMIT 1",
                (
                    digest,
                    message.version_id,
                    iso(message.published_at - timedelta(hours=6)),
                    iso(message.published_at),
                ),
            ).fetchone()
            return row is not None

    def run_start(
        self, stage: str, model: str, effort: str, at: datetime, messages: list[Message]
    ) -> int:
        with self._lock, self.db:
            row = self.db.execute(
                "INSERT INTO runs(stage,started_at,model,effort,state,version_ids) "
                "VALUES (?,?,?,?,?,?)",
                (
                    stage,
                    iso(at),
                    model,
                    effort,
                    "started",
                    json.dumps([m.version_id for m in messages]),
                ),
            )
            return int(row.lastrowid)

    def run_finish(
        self, run: int, at: datetime, state: str, input_tokens: int = 0, output_tokens: int = 0
    ) -> None:
        with self._lock, self.db:
            self.db.execute(
                "UPDATE runs SET finished_at=?,state=?,input_tokens=?,output_tokens=? WHERE id=?",
                (iso(at), state, input_tokens, output_tokens, run),
            )

    def run_output(self, run: int, output: dict[str, object]) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO run_outputs(run_id,output) VALUES (?,?)",
                (run, json.dumps(output, ensure_ascii=False)),
            )

    def last_run_time(self, stage: str) -> datetime | None:
        with self._lock:
            row = self.db.execute(
                "SELECT started_at FROM runs WHERE stage=? ORDER BY id DESC LIMIT 1", (stage,)
            ).fetchone()
            return datetime.fromisoformat(row["started_at"]) if row else None

    def record_evidence(self, run: int, quotes: list[tuple[int, str]]) -> None:
        with self._lock, self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO evidence(run_id,version_id,quote) VALUES (?,?,?)",
                [(run, version, quote) for version, quote in quotes],
            )

    def usage(self, at: datetime) -> tuple[int, int, int]:
        from zoneinfo import ZoneInfo

        from airalert.config import KYIV_TZ

        day = at.astimezone(ZoneInfo(KYIV_TZ)).date()
        with self._lock:
            rows = self.db.execute("SELECT * FROM runs").fetchall()
        today = [
            r
            for r in rows
            if datetime.fromisoformat(r["started_at"]).astimezone(ZoneInfo(KYIV_TZ)).date() == day
        ]
        return (
            sum(r["stage"] == "extract" for r in today),
            sum(r["stage"] == "evaluate" for r in today),
            sum(
                r["input_tokens"]
                + r["output_tokens"]
                + (TOKEN_RESERVE if r["state"] == "started" else 0)
                for r in today
            ),
        )

    def append_incident(self, incident: Incident) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO incidents(id,event,version_ids,extraction_finished) VALUES (?,?,?,?)",
                (
                    incident.event.id,
                    incident.event.model_dump_json(),
                    json.dumps(incident.version_ids),
                    iso(incident.extraction_finished),
                ),
            )

    def incidents(self, before: datetime | None = None) -> list[Incident]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM incidents ORDER BY revision").fetchall()
        latest: dict[str, Incident] = {}
        for r in rows:
            finished = datetime.fromisoformat(r["extraction_finished"])
            if before is not None and finished >= before:
                continue
            incident = Incident(
                NewsEvent.model_validate_json(r["event"]),
                tuple(json.loads(r["version_ids"])),
                finished,
                r["revision"],
            )
            latest[incident.event.id] = incident
        return list(latest.values())

    def evaluation(
        self, fingerprint: str, at: datetime, effects: list[dict[str, object]], models: str = ""
    ) -> None:
        with self._lock, self.db:
            row = self.db.execute(
                "INSERT INTO evaluations(fingerprint,finished_at,effects) VALUES (?,?,?)",
                (fingerprint, iso(at), json.dumps(effects)),
            )
            self.db.execute(
                "INSERT INTO evaluation_models(evaluation_id,models) VALUES (?,?)",
                (row.lastrowid, models),
            )

    def latest_evaluation(self, before: datetime | None = None) -> dict[str, object] | None:
        with self._lock:
            if before is None:
                row = self.db.execute(
                    "SELECT evaluations.*,evaluation_models.models FROM evaluations "
                    "LEFT JOIN evaluation_models ON evaluations.id=evaluation_models.evaluation_id "
                    "ORDER BY evaluations.id DESC LIMIT 1"
                ).fetchone()
            else:
                row = self.db.execute(
                    "SELECT evaluations.*,evaluation_models.models FROM evaluations "
                    "LEFT JOIN evaluation_models ON evaluations.id=evaluation_models.evaluation_id "
                    "WHERE finished_at<? ORDER BY evaluations.id DESC LIMIT 1",
                    (iso(before),),
                ).fetchone()
        return dict(row) if row else None

    def save_adjustments(self, base_at: datetime, at: datetime, payload: str) -> None:
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT payload FROM adjustments WHERE base_at=? ORDER BY id DESC LIMIT 1",
                (iso(base_at),),
            ).fetchone()
            if row is None or row["payload"] != payload:
                self.db.execute(
                    "INSERT INTO adjustments(base_at,computed_at,payload) VALUES (?,?,?)",
                    (iso(base_at), iso(at), payload),
                )

    def control(self, key: str, value: str | None = None) -> str | None:
        with self._lock, self.db:
            if value is not None:
                self.db.execute(
                    "INSERT OR REPLACE INTO control(key,value) VALUES (?,?)", (key, value)
                )
            row = self.db.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
